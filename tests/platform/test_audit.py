"""P1-E5 审计基础验收（04 §10.8，14 号计划 §96.5 验收标准 ⑥）。

- 每项管理动作一条审计（Actor/Subject 分离）；
- metadata 脱敏：无密码/Token/API Key 明文或完整 hash；
- 拒绝动作先写 denied 审计再抛出；
- 管理员访问他人数据时 actor_user_id 与 subject_user_id 同时存在。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.admin.service import AdminService
from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal
from openviking.server.platform.errors import (
    AdminActionForbiddenError,
    EntityNotFoundError,
    LastAccountAdminError,
)
from openviking.server.platform.models import IamUser
from tests.platform.helpers import build_auth_setup, create_api_key

DEFAULT_PASSWORD = "Init-Pass-2026-Dev!"


def _principal(user: IamUser) -> AuthenticatedUserPrincipal:
    return AuthenticatedUserPrincipal(
        actor_user_id=user.id,
        actor_account_id=user.account_id,
        actor_ov_user_id=user.ov_user_id,
        actor_ov_account_id=None,
        user_status=user.status,
        authentication_method="session",
    )


async def test_every_management_action_has_audit(session: AsyncSession) -> None:
    """AC ⑥：每项管理动作恰好一条 success 审计，Actor/Subject 分离、脱敏。"""
    setup = await build_auth_setup(session)
    admin = AdminService(setup.repo, setup.rbac, setup.auth)

    # 1. account.create
    created = await admin.create_account_with_first_admin(
        session,
        actor=_principal(setup.psa),
        account_code="beta",
        account_name="Beta",
        admin_email="badmin@beta.com",
        admin_username="badmin",
    )
    await session.commit()
    # 2. user.create（bob）
    bob = await admin.create_account_user(
        session, actor=_principal(setup.admin), email="bob@acme.com", username="bob"
    )
    await session.commit()
    # 3. user.update（display_name）
    await admin.patch_user(
        session, actor=_principal(setup.admin), target_user_id=bob.user.id, display_name="Bob B"
    )
    await session.commit()
    # 4. user.disable（bob）
    await admin.set_user_status(
        session, actor=_principal(setup.admin), target_user_id=bob.user.id, status="disabled"
    )
    await session.commit()
    # 5. user.enable（bob）
    await admin.set_user_status(
        session, actor=_principal(setup.admin), target_user_id=bob.user.id, status="active"
    )
    await session.commit()
    # 6. user.password.reset（alice ← admin，AuthService 审计）
    await admin.reset_user_password(
        session, actor=_principal(setup.admin), target_user_id=setup.alice.id
    )
    await session.commit()
    # 7. role.assign（alice 提升，platform 路径）
    await admin.promote_user(
        session, actor=_principal(setup.psa), target_user_id=setup.alice.id, account_id=setup.acme.id
    )
    await session.commit()
    # 8. credential.revoke（alice 的 Key）
    key = await create_api_key(setup.repo, session, setup.acme, setup.alice, name="Codex")
    await admin.revoke_user_api_key(
        session,
        actor=_principal(setup.admin),
        scope_account_id=setup.acme.id,
        target_user_id=setup.alice.id,
        credential_id=key.id,
    )
    await session.commit()

    expected = {
        "account.create": 1,
        "user.create": 1,
        "user.update": 1,
        "user.disable": 1,
        "user.enable": 1,
        "user.password.reset": 1,
        "role.assign": 1,
        "credential.revoke": 1,
    }
    for action, count in expected.items():
        events = await setup.repo.list_audit_events(session, action=action, result="success")
        assert len(events) == count, f"action {action}: expected {count}, got {len(events)}"

    # Actor/Subject 分离：管理员操作他人时两者必须同时存在且不同（04 §10.8）
    cross_actions = {
        "user.create": (setup.admin.id, bob.user.id),
        "user.disable": (setup.admin.id, bob.user.id),
        "user.enable": (setup.admin.id, bob.user.id),
        "user.password.reset": (setup.admin.id, setup.alice.id),
        "role.assign": (setup.psa.id, setup.alice.id),
        "credential.revoke": (setup.admin.id, setup.alice.id),
    }
    for action, (actor_id, subject_id) in cross_actions.items():
        event = (await setup.repo.list_audit_events(session, action=action))[0]
        assert event.actor_user_id == actor_id
        assert event.subject_user_id == subject_id
        assert event.actor_user_id != event.subject_user_id

    # 脱敏：任何事件不包含密码/Key 明文或完整 hash（04 §10.8）
    all_events = await setup.repo.list_audit_events(session)
    secrets = {
        DEFAULT_PASSWORD,
        created.initial_password,
        bob.initial_password,
        "ovk_u.",  # Key 明文前缀（本测试未创建明文 Key，仅为防御性断言）
    }
    for event in all_events:
        serialized = str(event.metadata_json) + str(event.reason) + str(event.target_id)
        for secret in secrets:
            assert secret not in serialized, f"secret leaked in {event.action}: {serialized}"
        assert "password" not in str(event.metadata_json)
        assert "$argon2id$" not in serialized  # 完整凭证 hash 不落审计


async def test_denied_actions_audited_before_reject(session: AsyncSession) -> None:
    """拒绝动作先写 denied 审计再抛出（守卫语义可审计）。"""
    setup = await build_auth_setup(session)
    admin = AdminService(setup.repo, setup.rbac, setup.auth)

    # 提升已提升目标 → 403 + denied 审计
    with pytest.raises(AdminActionForbiddenError):
        await admin.promote_user(
            session,
            actor=_principal(setup.psa),
            target_user_id=setup.admin.id,
            account_id=setup.acme.id,
        )
    await session.commit()
    events = await setup.repo.list_audit_events(session, action="role.assign", result="denied")
    assert len(events) == 1
    assert events[0].reason == "ROLE_ASSIGNMENT_ONLY_FROM_USER"
    assert events[0].actor_user_id == setup.psa.id
    assert events[0].subject_user_id == setup.admin.id

    # 最后一名 Account Admin 禁用 → 409 + denied 审计
    solo = await admin.create_account_with_first_admin(
        session,
        actor=_principal(setup.psa),
        account_code="solo",
        account_name="Solo",
        admin_email="solo@acme.com",
        admin_username="solo",
    )
    await session.commit()
    with pytest.raises(LastAccountAdminError):
        await admin.set_user_status(
            session, actor=_principal(solo.admin), target_user_id=solo.admin.id, status="disabled"
        )
    await session.commit()
    events = await setup.repo.list_audit_events(session, action="user.disable", result="denied")
    assert len(events) == 1
    assert events[0].reason == "LAST_ACCOUNT_ADMIN_REQUIRED"

    # 跨 Account 不可见不写审计（防枚举，04 §10.8）
    before = len(await setup.repo.list_audit_events(session, action="user.password.reset"))
    import uuid

    with pytest.raises(EntityNotFoundError):
        await admin.reset_user_password(
            session,
            actor=_principal(setup.psa),
            target_user_id=setup.alice.id,
            scope_account_id=uuid.uuid4(),
        )
    await session.commit()
    assert len(await setup.repo.list_audit_events(session, action="user.password.reset")) == before
