"""ProvisioningService（05 §11.3，14 号计划 §97.1）。

创建流程（单一身份事实来源，凭证不双写）：

```text
1. PostgreSQL transaction
   - 创建 account/user，status=provisioning
   - 写 iam_outbox（account.provision / user.provision）
2. Commit
3. Provisioning Worker
   - 使用内部 SystemPrincipal 调用控制面（初始化 OpenViking namespace）
4. 成功：IAM status=active，outbox=completed
5. 失败：记录脱敏错误并指数退避重试
```

- 产品请求只允许 `active` 状态（04 §10.1/§10.2，05 §11.3）；管理页面展示
  `provisioning/failed` 并支持安全重试（`POST .../provisioning/retry`，05 §12.6）；
- Worker 审计：`actor_type=system` + `actor_system_component`（04 §10.8），
  不伪装成用户；`last_error` 只存脱敏错误（去路径/口令类片段，04 §10.9）。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.errors import (
    EntityNotFoundError,
    ProvisioningError,
    ProvisioningNotRetryableError,
)
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.models import IamAccount, IamOutbox, IamUser
from openviking.server.platform.provisioning.control_plane import ControlPlaneAdapter
from openviking.server.platform.provisioning.principals import SystemPrincipal
from openviking.server.platform.provisioning.repository import (
    EVENT_ACCOUNT_PROVISION,
    EVENT_USER_PROVISION,
    ProvisioningRepository,
)

COMPONENT = "provisioning.worker"

_SENSITIVE_PATTERN = re.compile(
    r"(?i)\b(password|secret|token|api[_-]?key|initial[_-]?password)\b\s*[=:]\s*\S+"
)
_PATH_PATTERN = re.compile(r"(?<![\w.-])(?:/|~|\.\.)[^\s'\"]+(?:/[^\s'\"]*)+")


def sanitize_error(exc: BaseException) -> str:
    """脱敏错误文本（04 §10.9：不含路径/密钥/口令，截断 500 字符）。"""
    message = str(exc) or exc.__class__.__name__
    message = _SENSITIVE_PATTERN.sub(r"\1=***", message)
    message = _PATH_PATTERN.sub("<path>", message)
    return message[:500]


@dataclass(frozen=True)
class RetryAccountResult:
    """`provisioning/retry` 结果（05 §12.6；幂等，仅 provisioning/failed 生效）。"""

    account_id: uuid.UUID
    status: str
    retried_events: int


class ProvisioningService:
    """Provisioning 编排：outbox 事务写入、事件处理、安全重试。"""

    def __init__(
        self,
        repo: IamRepository,
        outbox: ProvisioningRepository,
        control_plane: ControlPlaneAdapter,
        config: PlatformConfig = platform_config,
    ) -> None:
        self._repo = repo
        self._outbox = outbox
        self._control_plane = control_plane
        self._config = config

    # ── outbox 事务写入（05 §11.3 步骤 1：与 Account/User 同一 PG 事务）──

    async def create_provisioning_events(
        self,
        session: AsyncSession,
        *,
        account: IamAccount,
        admin: IamUser,
        request_id: str | None = None,
    ) -> list[IamOutbox]:
        """Account + 首位 Admin 的 outbox 事件（account.provision + user.provision）。

        在调用方事务内执行：Account/User 创建失败回滚时事件一并回滚（AC①）。

        P5-E2（14 号计划 §99.2，06 §17.2）：`request_id` 写入事件 payload，
        Worker 处理时回填审计事件，保证 HTTP 请求 → 后台 Provisioning Task
        的 Request ID 贯通（日志/审计/trace 可关联）。
        """
        now = datetime.now(timezone.utc)
        account_event = await self._outbox.enqueue(
            session,
            event_type=EVENT_ACCOUNT_PROVISION,
            aggregate_id=account.id,
            payload={
                "account_id": str(account.id),
                "ov_account_id": account.ov_account_id,
                "code": account.code,
                "display_name": account.display_name,
                "request_id": request_id,
            },
            now=now,
        )
        user_event = await self._outbox.enqueue(
            session,
            event_type=EVENT_USER_PROVISION,
            aggregate_id=admin.id,
            payload={
                "account_id": str(account.id),
                "user_id": str(admin.id),
                "ov_user_id": admin.ov_user_id,
                "email": admin.email,
                "request_id": request_id,
            },
            now=now,
        )
        return [account_event, user_event]

    # ── Worker：处理单个事件（05 §11.3 步骤 3–5）──

    async def process_event(self, session: AsyncSession, event: IamOutbox) -> None:
        """用 SystemPrincipal 初始化 OpenViking namespace；成功 → active、
        失败 → failed 并抛出（Worker 记录退避与脱敏错误）。

        幂等（AC③）：控制面动作本身幂等；事件重放不会产生重复 namespace。
        """
        principal = SystemPrincipal(component=COMPONENT, task_id=str(event.id))
        request_id = (event.payload or {}).get("request_id") or None
        if event.event_type == EVENT_ACCOUNT_PROVISION:
            account = await self._repo.get_account(session, event.aggregate_id)
            if account is None or account.deleted_at is not None:
                # 目标已删除/不存在：无事可做，视为完成（Worker 侧不再重试）
                return
            try:
                await self._control_plane.provision_account(account.ov_account_id)
                await self._repo.update_account(
                    session,
                    account.id,
                    expected_version=account.version,
                    status="active",
                    provisioning_error=None,
                )
            except Exception as exc:  # noqa: BLE001
                error = sanitize_error(exc)
                await self._repo.update_account(
                    session,
                    account.id,
                    expected_version=account.version,
                    status="failed",
                    provisioning_error=error,
                )
                await self._append_system_audit(
                    session,
                    principal=principal,
                    request_id=request_id,
                    action="provision.account",
                    account_id=account.id,
                    target_type="iam_accounts",
                    target_id=str(account.id),
                    result="failed",
                    reason=error,
                )
                raise ProvisioningError(error) from exc
            await self._append_system_audit(
                session,
                principal=principal,
                request_id=request_id,
                action="provision.account",
                account_id=account.id,
                target_type="iam_accounts",
                target_id=str(account.id),
                result="success",
            )
            return

        if event.event_type == EVENT_USER_PROVISION:
            user = await self._repo.get_user(session, event.aggregate_id)
            if user is None or user.deleted_at is not None:
                return
            try:
                account = (
                    await self._repo.get_account(session, user.account_id)
                    if user.account_id is not None
                    else None
                )
                if account is None or user.ov_user_id is None:
                    raise ProvisioningError("user has no account/ov mapping")
                await self._control_plane.provision_user(account.ov_account_id, user.ov_user_id)
                await self._repo.update_user(session, user.id, status="active")
            except Exception as exc:  # noqa: BLE001
                error = sanitize_error(exc)
                await self._repo.update_user(session, user.id, status="failed")
                await self._append_system_audit(
                    session,
                    principal=principal,
                    request_id=request_id,
                    action="provision.user",
                    account_id=user.account_id,
                    subject_user_id=user.id,
                    target_type="iam_users",
                    target_id=str(user.id),
                    result="failed",
                    reason=error,
                )
                raise ProvisioningError(error) from exc
            await self._append_system_audit(
                session,
                principal=principal,
                request_id=request_id,
                action="provision.user",
                account_id=user.account_id,
                subject_user_id=user.id,
                target_type="iam_users",
                target_id=str(user.id),
                result="success",
            )
            return

        raise ProvisioningError(f"unknown event_type {event.event_type}")

    # ── 安全重试（05 §12.6：POST .../provisioning/retry）──

    async def retry_account(
        self,
        session: AsyncSession,
        *,
        actor,
        account_id: uuid.UUID,
        request_id: str | None = None,
    ) -> RetryAccountResult:
        """重试失败的 Account/首位 Account Admin Provisioning（幂等，AC⑤）。

        - Account 不存在/已删除 → 404（不可见语义，spike ==12 语义）；
        - Account 已 active 且无未完成事件 → 409 `PROVISIONING_NOT_RETRYABLE`；
        - 仅 `provisioning/failed` 状态可重试：未完成事件重置为 pending
          （attempts 归零、next_attempt_at=now），failed 状态目标回退为
          provisioning；重复重试不产生重复事件（幂等）；
        - 写一条审计（Actor=PSA，action=provisioning.retry）。
        """
        account = await self._repo.get_account(session, account_id)
        if account is None or account.deleted_at is not None:
            raise EntityNotFoundError(f"account {account_id} not visible")
        events = await self._outbox.list_by_account(session, account_id)
        pending = [e for e in events if e.status != "completed"]
        if account.status == "active" and not pending:
            raise ProvisioningNotRetryableError("PROVISIONING_NOT_RETRYABLE")

        now = datetime.now(timezone.utc)
        if account.status == "failed":
            account = await self._repo.update_account(
                session,
                account.id,
                expected_version=account.version,
                status="provisioning",
                provisioning_error=None,
            )
        retried = 0
        for event in pending:
            await self._outbox.update_status(
                session,
                event,
                status="pending",
                now=now,
                attempts=0,
                last_error=None,
                next_attempt_at=now,
                completed_at=None,
                processing_started_at=None,
            )
            retried += 1
            if event.event_type == EVENT_USER_PROVISION:
                user = await self._repo.get_user(session, event.aggregate_id)
                if user is not None and user.status == "failed":
                    await self._repo.update_user(session, user.id, status="provisioning")
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=account.id,
            actor_type="user",
            actor_user_id=actor.actor_user_id,
            actor_account_id=actor.actor_account_id,
            actor_session_id=actor.session_id,
            authentication_method=actor.authentication_method,
            subject_account_id=account.id,
            action="provisioning.retry",
            target_type="iam_accounts",
            target_id=str(account.id),
            scope="platform" if actor.actor_account_id is None else "account",
            result="success",
            metadata={"retried_events": retried},
        )
        return RetryAccountResult(account_id=account.id, status=account.status, retried_events=retried)

    # ── 内部工具 ──

    async def _append_system_audit(
        self,
        session: AsyncSession,
        *,
        principal: SystemPrincipal,
        action: str,
        account_id: uuid.UUID | None,
        subject_user_id: uuid.UUID | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        result: str,
        reason: str | None = None,
        metadata: dict | None = None,
        request_id: str | None = None,
    ) -> None:
        """系统动作审计（04 §10.8：actor_type=system + 组件名，不伪装成用户）。

        P5-E2（06 §17.2）：`request_id` 由事件 payload 回填，保证后台任务
        审计与触发它的 HTTP 请求可关联。
        """
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=account_id,
            actor_type="system",
            actor_system_component=principal.component,
            authentication_method="system",
            subject_account_id=account_id,
            subject_user_id=subject_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            scope="platform",
            result=result,
            reason=reason,
            metadata=metadata,
        )
