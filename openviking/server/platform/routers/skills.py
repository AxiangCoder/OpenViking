"""Skill 产品 API 路由（10 §61.1–§61.5，05 §12.5/§12.6，14 号计划 §97.4）。

覆盖：

- `/me/skills`：当前 User 私有 Skill 列表/详情/在线创建/上传消费/整体更新
  （name 不可变）/软删/恢复（10 §61.1）；
- `/me/skill-configs`：私密配置三接口（05 §12.5，仅当前 User、脱敏）；
- `/account/skills`：当前 Account 共享 Skill（普通 User 只读，管理动作
  仅 Account Admin，10 §61.2）；
- `/admin/users/{id}/skills`：成员私有 Skill 只读 + 发布（10 §61.3）；
- `/platform/accounts/{id}/skills`、`.../users/{uid}/skills`：PSA 全只读
  （10 §61.4）。

错误映射（10 §63）：`SKILL_NAME_CONFLICT`（创建/发布/恢复，只说明名称
在当前 Account 不可用，不泄露占用者）、`SKILL_NAME_IMMUTABLE`、
`SKILL_INVALID_FORMAT`、`SKILL_PUBLISH_FORBIDDEN`；跨 Account/越权目标
统一 404（防 IDOR）。写操作全部要求登录 Session + CSRF（03 §8.2）。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.db import get_session
from openviking.server.platform.deletion.service import DeletionService
from openviking.server.platform.dependencies import (
    get_current_principal,
    require_high_risk_write,
    require_permission,
    verify_csrf,
)
from openviking.server.platform.errors import (
    ConstraintViolationError,
    DeletionJobError,
    EntityNotFoundError,
    SkillInvalidFormatError,
    SkillNameConflictError,
    SkillNameImmutableError,
    SkillPublishForbiddenError,
    UploadNotConsumableError,
)
from openviking.server.platform.registry.service import SKILL_NAME_UNAVAILABLE
from openviking.server.platform.skills.service import SkillService

router = APIRouter(prefix="/api/platform/v1", tags=["skills"])

NOT_FOUND = "NOT_FOUND"
CONFLICT_MESSAGE = "该名称在当前 Account 不可用"

NAME_PATTERN = "^[A-Za-z0-9_-]{1,64}$"


class SkillCreateRequest(BaseModel):
    """在线创建或消费已上传的 `upload_id`（10 §61.1；不提交 URI/visibility）。"""

    name: str | None = Field(default=None, pattern=NAME_PATTERN)
    description: str | None = Field(default=None, max_length=1024)
    tags: list[str] | None = None
    allowed_tools: list[str] | None = None
    content: str | None = None
    upload_id: uuid.UUID | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _exactly_one_mode(self) -> "SkillCreateRequest":
        if self.upload_id is not None:
            # 上传消费：name + upload_id（10 §61.5；不携带在线字段/URI/visibility）
            if self.name is None:
                raise ValueError("upload consumption requires name")
            if self.description is not None or self.content is not None:
                raise ValueError("upload cannot be combined with online fields")
            return self
        if self.name is None or self.description is None or self.content is None:
            raise ValueError("online create requires name, description and content")
        return self


class SkillUpdateRequest(BaseModel):
    """整体更新（10 §55.2/§61.1 注）：name 不接受，提交即拒 `SKILL_NAME_IMMUTABLE`。"""

    name: str | None = Field(default=None, pattern=NAME_PATTERN)
    description: str | None = Field(default=None, max_length=1024)
    tags: list[str] | None = None
    allowed_tools: list[str] | None = None
    content: str | None = None
    upload_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _exactly_one_mode(self) -> "SkillUpdateRequest":
        if self.upload_id is not None and (self.description is not None or self.content is not None):
            raise ValueError("upload replacement cannot be combined with online fields")
        return self


class SkillConfigPutRequest(BaseModel):
    values: dict


def _skill(request: Request) -> SkillService:
    return request.app.state.iam_skill_service


def _deletion(request: Request) -> DeletionService:
    return request.app.state.iam_deletion_service


def _request_id(request: Request) -> str | None:
    return request.headers.get("x-request-id")


def _constraint_code(exc: ConstraintViolationError) -> str:
    return str(exc)


def skill_dto(
    ref,
    *,
    include_owner: bool = False,
    include_content: bool = False,
    content: dict | None = None,
) -> dict:
    dto = {
        "id": str(ref.id),
        "name": ref.canonical_name,
        "description": ref.description,
        "tags": list(ref.tags or []),
        "visibility": ref.visibility,
        "source_type": ref.source_type,
        "has_auxiliary_files": bool(ref.source_type == "zip"),
        "status": ref.status,
        "version": ref.version,
        "created_at": ref.created_at.isoformat() if ref.created_at else None,
        "updated_at": ref.updated_at.isoformat() if ref.updated_at else None,
    }
    if include_owner:
        dto["owner_user_id"] = str(ref.owner_user_id) if ref.owner_user_id else None
    if include_content:
        dto["content"] = (content or {}).get("skill_md", "")
        dto["allowed_tools"] = list((content or {}).get("allowed_tools", []))
        dto["files"] = [
            {"name": f.name, "size_bytes": f.size_bytes} for f in (content or {}).get("files", [])
        ]
    return dto


def _skill_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SkillNameConflictError):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "SKILL_NAME_CONFLICT", "message": CONFLICT_MESSAGE},
        )
    if isinstance(exc, ConstraintViolationError) and str(exc) == SKILL_NAME_UNAVAILABLE:
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "SKILL_NAME_CONFLICT", "message": CONFLICT_MESSAGE},
        )
    if isinstance(exc, SkillNameImmutableError):
        return HTTPException(status.HTTP_409_CONFLICT, detail={"code": "SKILL_NAME_IMMUTABLE"})
    if isinstance(exc, SkillInvalidFormatError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "SKILL_INVALID_FORMAT"})
    if isinstance(exc, SkillPublishForbiddenError):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "SKILL_PUBLISH_FORBIDDEN"})
    if isinstance(exc, DeletionJobError):
        return HTTPException(status.HTTP_409_CONFLICT, detail={"code": exc.reason})
    if isinstance(exc, UploadNotConsumableError):
        # 04 §10.14：跨 Scope/过期/已消费/重放统一拒绝（不泄露存在性）
        status_code = status.HTTP_400_BAD_REQUEST
        if exc.reason == "UPLOAD_NOT_FOUND":
            status_code = status.HTTP_404_NOT_FOUND
        return HTTPException(status_code, detail={"code": exc.reason})
    if isinstance(exc, EntityNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND})
    raise exc


# ── 10 §61.1 当前 User 私有 Skill ──


@router.get("/me/skills", dependencies=[Depends(require_permission("skill.user_private.read.self"))])
async def list_my_skills(
    request: Request,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    rows = await service.list_skills(
        session,
        account_id=principal.actor_account_id,
        visibility="user_private",
        owner_user_id=principal.actor_user_id,
    )
    return {"status": "ok", "result": {"items": [skill_dto(r) for r in rows], "next_cursor": None}}


@router.post(
    "/me/skills",
    dependencies=[Depends(verify_csrf), Depends(require_permission("skill.user_private.manage.self"))],
)
async def create_my_skill(
    request: Request,
    body: SkillCreateRequest,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        if body.upload_id is not None:
            ref = await service.create_from_upload(
                session,
                actor=principal,
                name=body.name,
                upload_id=body.upload_id,
                visibility="user_private",
                idempotency_key=body.idempotency_key,
                request_id=_request_id(request),
            )
        else:
            ref = await service.create_online(
                session,
                actor=principal,
                name=body.name,
                description=body.description,
                tags=body.tags,
                allowed_tools=body.allowed_tools,
                content=body.content,
                visibility="user_private",
                idempotency_key=body.idempotency_key,
                request_id=_request_id(request),
            )
    except Exception as exc:  # noqa: BLE001
        raise _skill_error(exc) from exc
    await session.commit()
    return {"status": "ok", "result": skill_dto(ref)}


@router.get("/me/skills/{skill_id}", dependencies=[Depends(require_permission("skill.user_private.read.self"))])
async def get_my_skill(
    request: Request,
    skill_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        ref = await service.get_skill(
            session,
            account_id=principal.actor_account_id,
            skill_id=skill_id,
            visibility="user_private",
            owner_user_id=principal.actor_user_id,
        )
        content = await service.read_skill_content(session, uri=ref.ov_uri)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    return {"status": "ok", "result": skill_dto(ref, include_content=True, content=content)}


@router.put(
    "/me/skills/{skill_id}",
    dependencies=[Depends(verify_csrf), Depends(require_permission("skill.user_private.manage.self"))],
)
async def update_my_skill(
    request: Request,
    skill_id: uuid.UUID,
    body: SkillUpdateRequest,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        ref = await service.get_skill(
            session,
            account_id=principal.actor_account_id,
            skill_id=skill_id,
            visibility="user_private",
            owner_user_id=principal.actor_user_id,
        )
        if body.name is not None:
            # 10 §55.2：更新 DTO 不接受 name（AC②）
            raise SkillNameImmutableError()
        if body.upload_id is not None:
            ref = await service.replace_from_upload(
                session, actor=principal, ref=ref, upload_id=body.upload_id,
                request_id=_request_id(request),
            )
        else:
            ref = await service.update_online(
                session,
                actor=principal,
                ref=ref,
                description=body.description,
                tags=body.tags,
                allowed_tools=body.allowed_tools,
                content=body.content,
                request_id=_request_id(request),
            )
    except Exception as exc:  # noqa: BLE001
        raise _skill_error(exc) from exc
    await session.commit()
    return {"status": "ok", "result": skill_dto(ref)}


@router.delete(
    "/me/skills/{skill_id}",
    dependencies=[
        Depends(
            require_high_risk_write(
                action="skill.delete",
                permission_code="skill.user_private.manage.self",
                scope="self",
            )
        )
    ],
)
async def delete_my_skill(
    request: Request,
    skill_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        ref = await service.get_skill(
            session,
            account_id=principal.actor_account_id,
            skill_id=skill_id,
            visibility="user_private",
            owner_user_id=principal.actor_user_id,
        )
        result = await service.soft_delete(
            session, actor=principal, ref=ref, request_id=_request_id(request)
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    await session.commit()
    return {"status": "ok", "result": result}


@router.post(
    "/me/skills/{skill_id}/restore",
    dependencies=[Depends(verify_csrf), Depends(require_permission("skill.user_private.manage.self"))],
)
async def restore_my_skill(
    request: Request,
    skill_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    ref = await service.get_deleted_skill_for_restore(
        session, account_id=principal.actor_account_id, skill_id=skill_id, visibility="user_private",
        owner_user_id=principal.actor_user_id,
    )
    try:
        result = await service.restore(
            session, actor=principal, ref=ref, scope="self", request_id=_request_id(request)
        )
    except (DeletionJobError, EntityNotFoundError) as exc:
        raise _skill_error(exc) from exc
    await session.commit()
    return {"status": "ok", "result": result}


# ── 05 §12.5 me/skill-configs 三接口（AC⑨：仅当前 User、脱敏）──


async def _require_skill_config_target(service: SkillService, session, principal, skill_id):
    """配置目标 Skill 必须对当前 User 可读（自己的私有或当前 Account 共享）。"""
    try:
        return await service.get_skill(
            session,
            account_id=principal.actor_account_id,
            skill_id=skill_id,
            visibility="user_private",
            owner_user_id=principal.actor_user_id,
        )
    except EntityNotFoundError:
        return await service.get_skill(
            session,
            account_id=principal.actor_account_id,
            skill_id=skill_id,
            visibility="account_shared",
        )


@router.get(
    "/me/skill-configs/{skill_id}",
    dependencies=[Depends(require_permission("privacy_config.read.self"))],
)
async def get_skill_config(
    request: Request,
    skill_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        ref = await _require_skill_config_target(service, session, principal, skill_id)
        snapshot = await service.get_skill_config(session, actor=principal, skill_ref=ref)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    return {"status": "ok", "result": snapshot.dto()}


@router.put(
    "/me/skill-configs/{skill_id}",
    dependencies=[Depends(verify_csrf), Depends(require_permission("privacy_config.write.self"))],
)
async def put_skill_config(
    request: Request,
    skill_id: uuid.UUID,
    body: SkillConfigPutRequest,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        ref = await _require_skill_config_target(service, session, principal, skill_id)
        snapshot = await service.put_skill_config(
            session, actor=principal, skill_ref=ref, values=body.values
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "CONFIG_INVALID"}) from exc
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    await session.commit()
    return {"status": "ok", "result": snapshot.dto()}


@router.post(
    "/me/skill-configs/{skill_id}/versions/{version}/activate",
    dependencies=[Depends(verify_csrf), Depends(require_permission("privacy_config.write.self"))],
)
async def activate_skill_config(
    request: Request,
    skill_id: uuid.UUID,
    version: int,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        ref = await _require_skill_config_target(service, session, principal, skill_id)
        snapshot = await service.activate_skill_config(
            session, actor=principal, skill_ref=ref, version=version
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "CONFIG_VERSION_NOT_FOUND"}) from exc
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    await session.commit()
    return {"status": "ok", "result": snapshot.dto()}


# ── 10 §61.2 当前 Account 共享 Skill ──


@router.get("/account/skills", dependencies=[Depends(require_permission("skill.account_shared.read.account"))])
async def list_shared_skills(
    request: Request,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    rows = await service.list_skills(
        session, account_id=principal.actor_account_id, visibility="account_shared"
    )
    return {"status": "ok", "result": {"items": [skill_dto(r) for r in rows], "next_cursor": None}}


@router.get(
    "/account/skills/{skill_id}",
    dependencies=[Depends(require_permission("skill.account_shared.read.account"))],
)
async def get_shared_skill(
    request: Request,
    skill_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        ref = await service.get_skill(
            session, account_id=principal.actor_account_id, skill_id=skill_id, visibility="account_shared"
        )
        content = await service.read_skill_content(session, uri=ref.ov_uri)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    return {"status": "ok", "result": skill_dto(ref, include_content=True, content=content)}


@router.post(
    "/account/skills",
    dependencies=[Depends(verify_csrf), Depends(require_permission("skill.account_shared.manage.account"))],
)
async def create_shared_skill(
    request: Request,
    body: SkillCreateRequest,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        if body.upload_id is not None:
            ref = await service.create_from_upload(
                session,
                actor=principal,
                name=body.name,
                upload_id=body.upload_id,
                visibility="account_shared",
                idempotency_key=body.idempotency_key,
                request_id=_request_id(request),
            )
        else:
            ref = await service.create_online(
                session,
                actor=principal,
                name=body.name,
                description=body.description,
                tags=body.tags,
                allowed_tools=body.allowed_tools,
                content=body.content,
                visibility="account_shared",
                idempotency_key=body.idempotency_key,
                request_id=_request_id(request),
            )
    except Exception as exc:  # noqa: BLE001
        raise _skill_error(exc) from exc
    await session.commit()
    return {"status": "ok", "result": skill_dto(ref)}


@router.put(
    "/account/skills/{skill_id}",
    dependencies=[Depends(verify_csrf), Depends(require_permission("skill.account_shared.manage.account"))],
)
async def update_shared_skill(
    request: Request,
    skill_id: uuid.UUID,
    body: SkillUpdateRequest,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        ref = await service.get_skill(
            session, account_id=principal.actor_account_id, skill_id=skill_id, visibility="account_shared"
        )
        if body.name is not None:
            raise SkillNameImmutableError()
        if body.upload_id is not None:
            ref = await service.replace_from_upload(
                session, actor=principal, ref=ref, upload_id=body.upload_id,
                request_id=_request_id(request),
            )
        else:
            ref = await service.update_online(
                session,
                actor=principal,
                ref=ref,
                description=body.description,
                tags=body.tags,
                allowed_tools=body.allowed_tools,
                content=body.content,
                request_id=_request_id(request),
            )
    except Exception as exc:  # noqa: BLE001
        raise _skill_error(exc) from exc
    await session.commit()
    return {"status": "ok", "result": skill_dto(ref)}


@router.delete(
    "/account/skills/{skill_id}",
    dependencies=[
        Depends(
            require_high_risk_write(
                action="skill.delete",
                permission_code="skill.account_shared.manage.account",
                scope="account",
            )
        )
    ],
)
async def delete_shared_skill(
    request: Request,
    skill_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        ref = await service.get_skill(
            session, account_id=principal.actor_account_id, skill_id=skill_id, visibility="account_shared"
        )
        result = await service.soft_delete(
            session, actor=principal, ref=ref, request_id=_request_id(request)
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    await session.commit()
    return {"status": "ok", "result": result}


@router.post(
    "/account/skills/{skill_id}/restore",
    dependencies=[Depends(verify_csrf), Depends(require_permission("skill.account_shared.manage.account"))],
)
async def restore_shared_skill(
    request: Request,
    skill_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    ref = await service.get_deleted_skill_for_restore(
        session, account_id=principal.actor_account_id, skill_id=skill_id, visibility="account_shared"
    )
    try:
        result = await service.restore(
            session, actor=principal, ref=ref, scope="account", request_id=_request_id(request)
        )
    except (DeletionJobError, EntityNotFoundError) as exc:
        raise _skill_error(exc) from exc
    await session.commit()
    return {"status": "ok", "result": result}


# ── 10 §61.3 Account Admin 成员 Skill（只读 + 发布）──


@router.get(
    "/admin/users/{user_id}/skills",
    dependencies=[Depends(require_permission("skill.user_private.read.account"))],
)
async def list_member_skills(
    request: Request,
    user_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        await service.require_user_in_account(
            session, account_id=principal.actor_account_id, user_id=user_id
        )
        rows = await service.list_skills(
            session,
            account_id=principal.actor_account_id,
            visibility="user_private",
            owner_user_id=user_id,
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    return {
        "status": "ok",
        "result": {"items": [skill_dto(r, include_owner=True) for r in rows], "next_cursor": None},
    }


@router.get(
    "/admin/users/{user_id}/skills/{skill_id}",
    dependencies=[Depends(require_permission("skill.user_private.read.account"))],
)
async def get_member_skill(
    request: Request,
    user_id: uuid.UUID,
    skill_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        await service.require_user_in_account(
            session, account_id=principal.actor_account_id, user_id=user_id
        )
        ref = await service.get_skill(
            session,
            account_id=principal.actor_account_id,
            skill_id=skill_id,
            visibility="user_private",
            owner_user_id=user_id,
        )
        content = await service.read_skill_content(session, uri=ref.ov_uri)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    return {"status": "ok", "result": skill_dto(ref, include_owner=True, include_content=True, content=content)}


@router.post(
    "/admin/users/{user_id}/skills/{skill_id}/publish",
    dependencies=[Depends(verify_csrf), Depends(require_permission("skill.user_private.publish.account"))],
)
async def publish_member_skill(
    request: Request,
    user_id: uuid.UUID,
    skill_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        await service.require_user_in_account(
            session, account_id=principal.actor_account_id, user_id=user_id
        )
        result = await service.publish(
            session,
            actor=principal,
            target_user_id=user_id,
            skill_id=skill_id,
            request_id=_request_id(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _skill_error(exc) from exc
    await session.commit()
    return {
        "status": "ok",
        "result": {
            "skill_id": str(result.skill_id),
            "operation_id": str(result.operation_id),
            "status": result.status,
            "visibility": "account_shared",
        },
    }


# ── 10 §61.4 Platform 只读 Skill（全只读，无 POST/PUT/DELETE/restore/publish）──


@router.get(
    "/platform/accounts/{account_id}/skills",
    dependencies=[Depends(require_permission("skill.account_shared.read.platform"))],
)
async def list_platform_shared_skills(
    request: Request,
    account_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        await service.require_account(session, account_id)
        rows = await service.list_skills(session, account_id=account_id, visibility="account_shared")
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    return {"status": "ok", "result": {"items": [skill_dto(r) for r in rows], "next_cursor": None}}


@router.get(
    "/platform/accounts/{account_id}/skills/{skill_id}",
    dependencies=[Depends(require_permission("skill.account_shared.read.platform"))],
)
async def get_platform_shared_skill(
    request: Request,
    account_id: uuid.UUID,
    skill_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        await service.require_account(session, account_id)
        ref = await service.get_skill(
            session, account_id=account_id, skill_id=skill_id, visibility="account_shared"
        )
        content = await service.read_skill_content(session, uri=ref.ov_uri)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    return {"status": "ok", "result": skill_dto(ref, include_content=True, content=content)}


@router.get(
    "/platform/accounts/{account_id}/users/{user_id}/skills",
    dependencies=[Depends(require_permission("skill.user_private.read.platform"))],
)
async def list_platform_user_skills(
    request: Request,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        await service.require_account(session, account_id)
        await service.require_user_in_account(session, account_id=account_id, user_id=user_id)
        rows = await service.list_skills(
            session, account_id=account_id, visibility="user_private", owner_user_id=user_id
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    return {
        "status": "ok",
        "result": {"items": [skill_dto(r, include_owner=True) for r in rows], "next_cursor": None},
    }


@router.get(
    "/platform/accounts/{account_id}/users/{user_id}/skills/{skill_id}",
    dependencies=[Depends(require_permission("skill.user_private.read.platform"))],
)
async def get_platform_user_skill(
    request: Request,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    skill_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service: SkillService = Depends(_skill),
    session: AsyncSession = Depends(get_session),
):
    try:
        await service.require_account(session, account_id)
        await service.require_user_in_account(session, account_id=account_id, user_id=user_id)
        ref = await service.get_skill(
            session,
            account_id=account_id,
            skill_id=skill_id,
            visibility="user_private",
            owner_user_id=user_id,
        )
        content = await service.read_skill_content(session, uri=ref.ov_uri)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    return {"status": "ok", "result": skill_dto(ref, include_owner=True, include_content=True, content=content)}
