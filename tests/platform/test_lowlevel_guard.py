"""P2-E6a 低层入口统一守卫单元测试（05 §11.5，14 号计划 §97.6）。

验收映射：
- AC①：低层写动作未显式目标强制私有根、显式共享目标对普通 User 403；
- AC②：所有「会改变」动作（write/mkdir/mv 源与目标/set_tags/批量/归档/
  导入/恢复）经 TargetPolicy；跨可见性移动被拒；
- AC④：拒绝与成功写动作都写脱敏审计（Actor=Key 归属者）；
- AC⑤：跨 User IDOR、编码/别名 URI 归一后授权一致。
"""

from __future__ import annotations

import uuid

import pytest

from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal
from openviking.server.platform.auth.uri_policy import (
    AuthorizationService,
    OVMapper,
    canonicalize_uri,
)
from openviking.server.platform.errors import (
    AccessDeniedError,
    CanonicalUriMismatchError,
    CrossVisibilityMoveError,
    LowLevelGuardError,
)
from openviking.server.platform.iam.permissions import (
    ACCOUNT_ADMIN_PERMISSIONS,
    PLATFORM_PERMISSIONS,
    USER_PERMISSIONS,
)
from openviking.server.platform.lowlevel.guard import LowLevelPolicyGuard, object_type_for_uri
from openviking.server.platform.target_policy import TargetPolicy


def _uid(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def _principal(
    *,
    role_codes=("user",),
    account_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    ov_user_id: str | None = None,
    ov_account_id: str | None = None,
    permissions: frozenset[str] = USER_PERMISSIONS,
    authentication_method: str = "api_key",
    credential_id: uuid.UUID | None = None,
) -> AuthenticatedUserPrincipal:
    return AuthenticatedUserPrincipal(
        actor_user_id=user_id or _uid(100),
        actor_account_id=account_id,
        actor_ov_user_id=ov_user_id or ("ov_user_alice" if account_id else None),
        actor_ov_account_id=ov_account_id or ("ov_account_acme" if account_id else None),
        user_status="active",
        authentication_method=authentication_method,
        credential_id=credential_id,
        role_codes=tuple(role_codes),
        permissions=permissions,
    )


def _mapper() -> OVMapper:
    async def resolve_user(user_id: uuid.UUID):
        if user_id == _uid(1):
            return ("ov_account_acme", "ov_user_alice")
        if user_id == _uid(5):
            return ("ov_account_acme", "ov_user_bob")
        return None

    async def resolve_account(account_id: uuid.UUID):
        if account_id == _uid(2):
            return "ov_account_acme"
        if account_id == _uid(3):
            return "ov_account_beta"
        return None

    return OVMapper(resolve_user=resolve_user, resolve_account=resolve_account)


class RecordingAudit:
    """记录型审计 fake：断言拒绝/成功写动作都写审计且 Actor 正确（AC④）。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def __call__(self, principal, *, action, result, reason, target_uri, request_id, metadata):
        self.events.append(
            {
                "actor_user_id": principal.actor_user_id,
                "authentication_method": principal.authentication_method,
                "actor_credential_id": principal.credential_id,
                "action": action,
                "result": result,
                "reason": reason,
                "target_uri": target_uri,
                "metadata": metadata,
            }
        )


def _guard(permissions=None, audit: RecordingAudit | None = None) -> LowLevelPolicyGuard:
    if audit is None:
        audit = RecordingAudit()
    return LowLevelPolicyGuard(
        AuthorizationService(TargetPolicy(), _mapper()),
        audit=audit,
    )


ALICE = _principal(account_id=_uid(2), user_id=_uid(1))
BOB = _principal(account_id=_uid(2), user_id=_uid(5), ov_user_id="ov_user_bob")
ADMIN = _principal(
    account_id=_uid(2),
    user_id=_uid(9),
    role_codes=("account_admin",),
    permissions=frozenset(ACCOUNT_ADMIN_PERMISSIONS),
)
PSA = _principal(
    role_codes=("platform_super_admin",),
    account_id=None,
    user_id=_uid(900),
    permissions=frozenset(PLATFORM_PERMISSIONS),
)


# ── 动作映射（05 §11.5："会改变"清单）──


async def test_low_level_action_mapping_covers_all_mutating_actions() -> None:
    policy = TargetPolicy()
    assert policy.low_level_action("add_resource") == "write"
    assert policy.low_level_action("add_skill") == "write"
    assert policy.low_level_action("write") == "write"
    assert policy.low_level_action("mkdir") == "write"
    assert policy.low_level_action("mv") == "write"
    assert policy.low_level_action("set_tags") == "write"
    assert policy.low_level_action("archive") == "write"
    assert policy.low_level_action("import") == "write"
    assert policy.low_level_action("restore") == "write"
    assert policy.low_level_action("batch_write") == "write"
    assert policy.low_level_action("rm") == "delete"
    assert policy.low_level_action("forget") == "delete"
    assert policy.low_level_action("read") == "read"
    assert policy.low_level_action("mystery_action") is None


async def test_unmapped_action_denied() -> None:
    with pytest.raises(LowLevelGuardError) as excinfo:
        await _guard().authorize(
            ALICE, action="mystery_action", uri="viking://user/ov_user_alice/resources/x.md"
        )
    assert excinfo.value.reason == "ACTION_NOT_MAPPED"


# ── AC①：默认目标规则 ──


async def test_default_target_forces_actor_private_root() -> None:
    guard = _guard()
    assert guard.default_target_uri(ALICE, object_type="resource") == (
        "viking://user/ov_user_alice/resources/"
    )
    assert guard.default_target_uri(ALICE, object_type="skill") == (
        "viking://user/ov_user_alice/skills/"
    )


async def test_authorize_default_or_explicit_without_target_forces_private_root() -> None:
    access = await _guard().authorize_default_or_explicit(
        ALICE, action="add_resource", explicit_uri=None, object_type="resource"
    )
    assert access.visibility == "user_private"
    assert access.subject_ov_user_id == "ov_user_alice"
    assert access.canonical_ov_uri == "viking://user/ov_user_alice/resources"


async def test_plain_user_explicit_shared_target_denied() -> None:
    """AC①：显式共享目标对普通 User 403（write 权限未授予）。"""
    guard = _guard()
    with pytest.raises(AccessDeniedError) as excinfo:
        await guard.authorize(
            ALICE, action="add_resource", uri="viking://resources/foo", object_type="resource"
        )
    assert excinfo.value.reason == "PERMISSION_NOT_GRANTED"
    # 拒绝审计已写入
    assert [e["action"] for e in guard._audit.events] == ["lowlevel.add_resource.denied"]


async def test_account_admin_explicit_shared_target_ok() -> None:
    access = await _guard().authorize(
        ADMIN, action="add_resource", uri="viking://resources/foo", object_type="resource"
    )
    assert access.visibility == "account_shared"
    assert access.subject_user_id is None


async def test_account_admin_shared_write_audited_success() -> None:
    audit = RecordingAudit()
    guard = _guard(audit=audit)
    await guard.authorize(
        ADMIN, action="add_resource", uri="viking://resources/foo", object_type="resource"
    )
    assert [e["action"] for e in audit.events] == ["lowlevel.add_resource"]
    assert audit.events[0]["result"] == "success"
    assert audit.events[0]["actor_user_id"] == _uid(9)


# ── AC②：所有「会改变」动作经 TargetPolicy、mv 跨可见性被拒 ──


async def test_all_mutating_actions_require_write_permission() -> None:
    """普通 User 对共享区的每个会改变动作都 403（05 §11.5 全清单）。"""
    for action in (
        "write",
        "mkdir",
        "set_tags",
        "batch_write",
        "archive",
        "import",
        "restore",
        "add_resource",
    ):
        with pytest.raises(AccessDeniedError):
            await _guard().authorize(
                ALICE, action=action, uri="viking://resources/shared.md", object_type="resource"
            )


async def test_mv_source_and_target_both_guarded() -> None:
    """mv 源与目标都经 TargetPolicy；普通 User 移动他人私有对象被拒。"""
    with pytest.raises(CanonicalUriMismatchError):
        await _guard().authorize_move(
            ALICE,
            from_uri="viking://user/ov_user_bob/resources/x.md",
            to_uri="viking://user/ov_user_alice/resources/x2.md",
            object_type="resource",
        )
    # 私有内移动 OK
    src, dst = await _guard().authorize_move(
        ALICE,
        from_uri="viking://user/ov_user_alice/resources/x.md",
        to_uri="viking://user/ov_user_alice/resources/x2.md",
        object_type="resource",
    )
    assert (src.visibility, dst.visibility) == ("user_private", "user_private")


async def test_mv_cross_visibility_denied() -> None:
    """AC②：跨可见性移动不走普通 mv（私有→共享 / 共享→私有都拒绝）。"""
    guard = _guard()
    with pytest.raises(CrossVisibilityMoveError):
        await guard.authorize_move(
            ADMIN,
            from_uri="viking://user/ov_user_alice/resources/x.md",
            to_uri="viking://resources/team/x.md",
            object_type="resource",
        )
    with pytest.raises(CrossVisibilityMoveError):
        await guard.authorize_move(
            ADMIN,
            from_uri="viking://resources/team/x.md",
            to_uri="viking://user/ov_user_alice/resources/x.md",
            object_type="resource",
        )


async def test_mv_admin_within_shared_ok() -> None:
    src, dst = await _guard().authorize_move(
        ADMIN,
        from_uri="viking://resources/team/a.md",
        to_uri="viking://resources/team/b.md",
        object_type="resource",
    )
    assert src.visibility == dst.visibility == "account_shared"


async def test_mv_plain_user_shared_denied() -> None:
    """普通 User 在共享区内部移动也被拒（共享写权限未授予）。"""
    with pytest.raises(AccessDeniedError):
        await _guard().authorize_move(
            ALICE,
            from_uri="viking://resources/team/a.md",
            to_uri="viking://resources/team/b.md",
            object_type="resource",
        )


# ── AC⑤：IDOR / 编码与别名 URI ──


async def test_idor_other_user_private_uri_denied() -> None:
    """跨 User IDOR：alice 写/删 bob 的私有 URI → 拒绝（不区分失败原因）。"""
    guard = _guard()
    for action in ("write", "rm", "forget", "set_tags"):
        with pytest.raises((CanonicalUriMismatchError, AccessDeniedError)):
            await guard.authorize(
                ALICE, action=action, uri="viking://user/ov_user_bob/resources/secret.md"
            )


async def test_encoded_and_alias_uris_normalize_before_authorization() -> None:
    """编码/别名 URI（重复斜杠、尾斜杠）先归一化再授权，结果一致。"""
    guard = _guard()
    canonical = canonicalize_uri("viking://user/ov_user_alice//resources///x.md/")
    assert canonical == "viking://user/ov_user_alice/resources/x.md"
    access = await guard.authorize(
        ALICE, action="write", uri="viking://user/ov_user_alice//resources///x.md/",
        object_type="resource",
    )
    assert access.canonical_ov_uri == canonical


async def test_encoded_other_user_id_cannot_bypass_idor() -> None:
    """编码形式的他人 User ID（% 编码）归一化后不匹配 IAM 映射 → 拒绝。"""
    with pytest.raises(CanonicalUriMismatchError):
        await _guard().authorize(
            ALICE,
            action="write",
            uri="viking://user/ov_user_%62ob/resources/x.md",
            object_type="resource",
        )


async def test_internal_targets_denied_for_low_level() -> None:
    """internal 根（agent/endpoints/tools/payments）低层默认拒绝（02 §7.5）。"""
    for uri in (
        "viking://agent/endpoints/tools/payments",
        "viking://tools/thing",
        "viking://internal/fs/root",
        "viking://unknownroot/x",
    ):
        with pytest.raises((AccessDeniedError, LowLevelGuardError)):
            await _guard().authorize(
                ALICE, action="write", uri=uri, object_type=object_type_for_uri(uri)
            )


async def test_object_type_for_uri() -> None:
    assert object_type_for_uri("viking://resources/foo.md") == "resource"
    assert object_type_for_uri("viking://user/ov_user_alice/resources/x.md") == "resource"
    assert object_type_for_uri("viking://agent/skills/s.md") == "skill"
    assert object_type_for_uri("viking://user/ov_user_alice/skills/s.md") == "skill"
    assert object_type_for_uri("viking://agent/endpoints/x") is None
    assert object_type_for_uri("http://evil/x") is None


async def test_psa_low_level_requires_explicit_subject() -> None:
    """PSA 低层调用必须来自平台管理路径；无 Subject 的低层凭证默认拒绝。"""
    with pytest.raises(AccessDeniedError):
        await _guard().authorize(
            PSA, action="write", uri="viking://resources/foo.md", object_type="resource"
        )


# ── AC④：审计 ──


async def test_denied_and_success_writes_audited_with_key_owner() -> None:
    audit = RecordingAudit()
    guard = _guard(audit=audit)
    cred_id = uuid.uuid4()
    alice_key = _principal(
        account_id=_uid(2), user_id=_uid(1), authentication_method="api_key",
        credential_id=cred_id,
    )
    # 成功写
    await guard.authorize(
        alice_key,
        action="write",
        uri="viking://user/ov_user_alice/resources/notes.md",
        object_type="resource",
    )
    # 拒绝写（共享区）
    with pytest.raises(AccessDeniedError):
        await guard.authorize(
            alice_key,
            action="write",
            uri="viking://resources/team.md",
            object_type="resource",
        )
    assert [e["action"] for e in audit.events] == [
        "lowlevel.write",
        "lowlevel.write.denied",
    ]
    for event in audit.events:
        assert event["actor_user_id"] == _uid(1)
        assert event["authentication_method"] == "api_key"
        assert event["actor_credential_id"] == cred_id
    assert audit.events[0]["metadata"]["visibility"] == "user_private"
    assert audit.events[1]["reason"] == "PERMISSION_NOT_GRANTED"
    assert audit.events[1]["result"] == "denied"
