"""P1-E2 RBAC 服务层验收（14 号计划 §96.2 验收标准 ①③④⑤⑥⑦）。

依赖真实 PostgreSQL（conftest 会话级重建测试库并 upgrade head）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.errors import EntityNotFoundError, RoleAssignmentError
from openviking.server.platform.iam import PostgresIamRepository, RbacService
from openviking.server.platform.iam.permissions import (
    ACCOUNT_ADMIN,
    ACCOUNT_ADMIN_PERMISSIONS,
    BUILTIN_ROLE_PERMISSIONS,
    PLATFORM_PERMISSIONS,
    PLATFORM_SUPER_ADMIN,
    USER,
    USER_PERMISSIONS,
)
from openviking.server.platform.iam.service import SEED_SYSTEM_COMPONENT
from openviking.server.platform.models import IamAccount, IamUser


@dataclass
class _Setup:
    service: RbacService
    psa: IamUser
    acme: IamAccount
    alice: IamUser
    admin: IamUser


async def _seed(repo: PostgresIamRepository, session: AsyncSession) -> RbacService:
    service = RbacService(repo)
    await service.seed_catalog(session)
    await session.commit()
    return service


async def _account(
    repo: PostgresIamRepository,
    session: AsyncSession,
    code: str,
) -> IamAccount:
    account = await repo.create_account(
        session,
        ov_account_id=f"ov_account_{code}",
        code=code,
        display_name=code,
        status="active",
    )
    await session.commit()
    return account


async def _user(
    repo: PostgresIamRepository,
    session: AsyncSession,
    account: IamAccount | None,
    *,
    email: str,
    username: str,
    status: str = "active",
) -> IamUser:
    return await repo.create_user(
        session,
        account_id=account.id if account is not None else None,
        ov_user_id=None if account is None else f"ov_user_{username}",
        username=username,
        email=email,
        display_name=username,
        password_hash="x" * 64,
        status=status,
    )


async def _psa_bootstrap(
    service: RbacService,
    repo: PostgresIamRepository,
    session: AsyncSession,
) -> IamUser:
    psa = await _user(repo, session, None, email="psa@platform.local", username="psa")
    await session.commit()
    await service.assign_platform_super_admin(session, target_user_id=psa.id)
    await session.commit()
    return psa


async def _setup(
    repo: PostgresIamRepository,
    session: AsyncSession,
) -> _Setup:
    """种子 + PSA + acme Account（admin/alice 两用户，alice 授予 user 角色）。"""
    service = await _seed(repo, session)
    psa = await _psa_bootstrap(service, repo, session)
    acme = await _account(repo, session, "acme")
    admin = await _user(repo, session, acme, email="admin@acme.com", username="admin")
    alice = await _user(repo, session, acme, email="alice@acme.com", username="alice")
    await session.commit()
    await service.assign_role(
        session,
        actor_user_id=psa.id,
        actor_account_id=None,
        target_user_id=alice.id,
        role_code=USER,
    )
    await session.commit()
    return _Setup(service=service, psa=psa, acme=acme, alice=alice, admin=admin)


# ── AC ①：种子后恰三内置角色且 rank/ov_base_role 正确 ──


async def test_seed_creates_three_builtin_roles(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """AC ①：恰三内置角色，rank=3/2/1、ov_base_role=None/admin/user（verify.py ==3）。"""
    await _seed(repo, session)
    roles = {r.code: r for r in await repo.list_roles(session)}
    assert set(roles) == {PLATFORM_SUPER_ADMIN, ACCOUNT_ADMIN, USER}
    assert roles[PLATFORM_SUPER_ADMIN].rank == 3
    assert roles[PLATFORM_SUPER_ADMIN].ov_base_role is None
    assert roles[ACCOUNT_ADMIN].rank == 2
    assert roles[ACCOUNT_ADMIN].ov_base_role == "admin"
    assert roles[USER].rank == 1
    assert roles[USER].ov_base_role == "user"
    assert all(r.is_system for r in roles.values())
    assert all(r.status == "active" for r in roles.values())
    assert all(r.account_id is None for r in roles.values())  # 全局单行（04 §10.4）


async def test_seed_populates_catalog_and_role_permissions(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """目录全部落库；各角色 DB 权限集合与代码定义完全一致（种子以代码为准）。"""
    service = await _seed(repo, session)
    assert len(await repo.list_permissions(session)) == 70
    views = {v.code: v for v in await service.get_role_views(session)}
    assert set(views) == set(BUILTIN_ROLE_PERMISSIONS)
    for code, expected in BUILTIN_ROLE_PERMISSIONS.items():
        assert views[code].permissions == expected


async def test_seed_idempotent_no_version_bump_no_audit(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """幂等重跑：schema_version 不递增、无新增审计（变更才递增，04 §10.5）。"""
    service = await _seed(repo, session)
    v1 = await service.get_permission_schema_version(session)
    assert v1 == 1
    result = await service.seed_catalog(session)
    await session.commit()
    assert result.changed is False
    assert await service.get_permission_schema_version(session) == v1
    assert await repo.list_audit_events(session) == []


# ── AC ⑤：种子/migration 变更递增 permission_schema_version 且参与全部缓存键 ──


async def test_seed_change_bumps_schema_version_with_audit(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """AC ⑤⑦：目录内容变更 → schema_version+1 + system 审计（04 §10.8）。"""
    service = await _seed(repo, session)
    await session.execute(
        text("UPDATE iam_permission_schema SET catalog_fingerprint = 'stale-fingerprint'")
    )
    await session.commit()

    result = await service.seed_catalog(session)
    await session.commit()
    assert result.changed is True
    assert await service.get_permission_schema_version(session) == 2

    events = await repo.list_audit_events(session, action="permission_catalog.seed")
    assert len(events) == 1
    event = events[0]
    assert event.actor_type == "system"
    assert event.actor_system_component == SEED_SYSTEM_COMPONENT
    assert event.actor_user_id is None
    assert event.subject_user_id is None
    assert event.result == "success"
    assert event.metadata_json["schema_version"] == 2
    assert event.metadata_json["catalog_fingerprint"]


async def test_schema_version_participates_in_cache_key(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """AC ⑤：缓存键含 permission_schema_version——
    版本不变返回缓存（旧数据），版本递增后立即重新计算（不再命中旧键）。"""
    setup = await _setup(repo, session)
    service, alice = setup.service, setup.alice

    first = await service.get_user_permissions(session, alice.id)
    cached = await service.get_user_permissions(session, alice.id)
    assert cached is first  # 缓存命中：同一实例

    # 直接改库但不递增任何版本：契约外变更，缓存仍返回旧结果（版本键隔离）
    await session.execute(text("UPDATE iam_roles SET status = 'disabled' WHERE code = 'user'"))
    await session.execute(
        text("UPDATE iam_permission_schema SET schema_version = schema_version + 1")
    )
    await session.commit()
    # 不调用 session.expire_all()：重算路径内的 SELECT 会按 identity map 原地刷新
    # 角色对象；版本递增保证缓存不命中，重新计算即可看到 disabled 状态。

    recomputed = await service.get_user_permissions(session, alice.id)
    assert recomputed is not first
    assert recomputed.permissions == frozenset()  # 角色已禁用 + 新版本 → 重算为空


# ── AC ③：有效权限 = union(active role permissions) − disabled ──


async def test_effective_permissions_union_of_active_role(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """AC ③：有效权限与角色权限集合一致（union，v0.1 单角色）。"""
    setup = await _setup(repo, session)
    perms = await setup.service.get_user_permissions(session, setup.alice.id)
    assert perms.role_codes == (USER,)
    assert perms.permissions == USER_PERMISSIONS
    assert perms.ov_base_role == "user"
    assert perms.rank == 1


async def test_disabled_user_effective_permissions_empty(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """AC ③：用户禁用后有效权限为空（03 §9.4），重启用则恢复（版本驱动）。"""
    setup = await _setup(repo, session)
    alice = setup.alice
    assert (await setup.service.get_user_permissions(session, alice.id)).permissions

    await repo.update_user(session, alice.id, status="disabled")
    await repo.bump_permission_version(session, alice.id)
    await session.commit()

    disabled = await setup.service.get_user_permissions(session, alice.id)
    assert disabled.permissions == frozenset()
    assert disabled.role_codes == ()
    assert disabled.rank == 0
    assert disabled.ov_base_role is None

    await repo.update_user(session, alice.id, status="active")
    await repo.bump_permission_version(session, alice.id)
    await session.commit()
    restored = await setup.service.get_user_permissions(session, alice.id)
    assert restored.permissions == USER_PERMISSIONS


# ── AC ④：提升后免重登生效（permission_version 缓存失效）──


async def test_promotion_effective_without_relogin(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """AC ④：user→account_admin 提升后，同一会话立即拿到新权限
    （对应 verify.py ==11：提升免重登生效）。"""
    setup = await _setup(repo, session)
    service, alice, psa = setup.service, setup.alice, setup.psa
    v0 = (await repo.get_user(session, alice.id)).permission_version

    before = await service.get_user_permissions(session, alice.id)
    assert "user.create" not in before.permissions

    await service.assign_role(
        session,
        actor_user_id=psa.id,
        actor_account_id=None,
        target_user_id=alice.id,
        role_code=ACCOUNT_ADMIN,
    )
    await session.commit()

    after = await service.get_user_permissions(session, alice.id)
    assert "user.create" in after.permissions
    assert after.permissions == ACCOUNT_ADMIN_PERMISSIONS
    assert after.role_codes == (ACCOUNT_ADMIN,)
    assert after.rank == 2
    assert (await repo.get_user(session, alice.id)).permission_version == v0 + 1


async def test_single_role_replaced_on_promotion(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """v0.1 单角色：提升为替换语义，iam_user_roles 恒为一行（04 §10.6）。"""
    setup = await _setup(repo, session)
    alice, psa = setup.alice, setup.psa
    link = await repo.get_role_for_user(session, alice.id)
    assert link is not None and link.role_id is not None

    await setup.service.assign_role(
        session,
        actor_user_id=psa.id,
        actor_account_id=None,
        target_user_id=alice.id,
        role_code=ACCOUNT_ADMIN,
    )
    await session.commit()

    rows = (
        await session.execute(
            text("SELECT count(*) FROM iam_user_roles WHERE user_id = :uid"),
            {"uid": alice.id},
        )
    ).scalar()
    assert rows == 1
    after = await repo.get_role_for_user(session, alice.id)
    admin_role = await repo.get_role_by_code(session, ACCOUNT_ADMIN)
    assert after is not None and after.role_id == admin_role.id


async def test_assign_same_role_idempotent(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """重复授予同一角色幂等：不产生重复绑定、版本不重复递增。"""
    setup = await _setup(repo, session)
    alice, psa = setup.alice, setup.psa
    v0 = (await repo.get_user(session, alice.id)).permission_version

    link = await setup.service.assign_role(
        session,
        actor_user_id=psa.id,
        actor_account_id=None,
        target_user_id=alice.id,
        role_code=USER,
    )
    await session.commit()
    assert link.user_id == alice.id
    assert (await repo.get_user(session, alice.id)).permission_version == v0


# ── AC ⑥：跨 Account 授予 account_admin/user 被拒 ──


async def test_cross_account_grant_denied_with_audit(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """AC ⑥⑦：跨 Account 授予 account_admin/user → RoleAssignmentError +
    denied 审计（Actor/Subject 分离）。"""
    setup = await _setup(repo, session)
    beta = await _account(repo, session, "beta")
    admin_beta = await _user(
        repo, session, beta, email="admin@beta.com", username="admin_beta"
    )
    await session.commit()

    for role_code in (ACCOUNT_ADMIN, USER):
        with pytest.raises(RoleAssignmentError) as exc_info:
            await setup.service.assign_role(
                session,
                actor_user_id=admin_beta.id,
                actor_account_id=beta.id,
                target_user_id=setup.alice.id,
                role_code=role_code,
            )
        assert exc_info.value.reason == "CROSS_ACCOUNT_ROLE_ASSIGNMENT"
        await session.commit()

    events = await repo.list_audit_events(session, action="role.assign", result="denied")
    assert len(events) == 2
    for event in events:
        assert event.actor_user_id == admin_beta.id
        assert event.actor_account_id == beta.id
        assert event.subject_user_id == setup.alice.id
        assert event.subject_account_id == setup.acme.id
        assert event.reason == "CROSS_ACCOUNT_ROLE_ASSIGNMENT"
    # 授予未生效：alice 仍是 user
    assert (await repo.get_role_for_user(session, setup.alice.id)) is not None


async def test_same_account_grant_allowed(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """正向对照：同 Account 授予 user 角色成功，无 denied 审计。"""
    setup = await _setup(repo, session)
    bob = await _user(repo, session, setup.acme, email="bob@acme.com", username="bob")
    await session.commit()
    await setup.service.assign_role(
        session,
        actor_user_id=setup.admin.id,
        actor_account_id=setup.acme.id,
        target_user_id=bob.id,
        role_code=USER,
    )
    await session.commit()
    assert (await repo.get_role_for_user(session, bob.id)) is not None
    assert await repo.list_audit_events(session, result="denied") == []


async def test_grant_to_null_account_user_denied(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """Account 一致性：无 Account 用户（PSA）不能授予 account_admin/user（04 §10.6）。

    DB 部分唯一索引 `uq_iam_users_psa_null_account` 保证至多一个无 Account 用户
    （P1-E1 已验），因此目标即 bootstrap 出的 PSA 本人。
    """
    setup = await _setup(repo, session)
    with pytest.raises(RoleAssignmentError) as exc_info:
        await setup.service.assign_role(
            session,
            actor_user_id=setup.psa.id,
            actor_account_id=None,
            target_user_id=setup.psa.id,
            role_code=USER,
        )
    assert exc_info.value.reason == "ROLE_REQUIRES_ACCOUNT"
    await session.commit()
    events = await repo.list_audit_events(session, result="denied")
    assert len(events) == 1
    assert events[0].reason == "ROLE_REQUIRES_ACCOUNT"
    assert events[0].subject_user_id == setup.psa.id
    assert events[0].subject_account_id is None


async def test_psa_role_grant_via_assign_role_rejected(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """AC ⑥：platform_super_admin 仅平台初始化路径可授予（04 §10.6）。"""
    setup = await _setup(repo, session)
    with pytest.raises(RoleAssignmentError) as exc_info:
        await setup.service.assign_role(
            session,
            actor_user_id=setup.psa.id,
            actor_account_id=None,
            target_user_id=setup.alice.id,
            role_code=PLATFORM_SUPER_ADMIN,
        )
    assert exc_info.value.reason == "PLATFORM_ROLE_BOOTSTRAP_ONLY"
    await session.commit()
    events = await repo.list_audit_events(session, result="denied")
    assert len(events) == 1
    assert events[0].reason == "PLATFORM_ROLE_BOOTSTRAP_ONLY"
    assert events[0].target_id == PLATFORM_SUPER_ADMIN
    assert events[0].subject_user_id == setup.alice.id


async def test_platform_super_admin_bootstrap_path(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """平台初始化路径：null-Account 用户可获 PSA；Account 用户被拒并审计。"""
    service = await _seed(repo, session)
    psa = await _user(repo, session, None, email="psa2@platform.local", username="psa2")
    acme = await _account(repo, session, "acme")
    corp = await _user(repo, session, acme, email="corp@acme.com", username="corp")
    await session.commit()

    link = await service.assign_platform_super_admin(session, target_user_id=psa.id)
    await session.commit()
    assert link.role_id is not None
    perms = await service.get_user_permissions(session, psa.id)
    assert perms.role_codes == (PLATFORM_SUPER_ADMIN,)
    assert perms.rank == 3
    assert perms.permissions == PLATFORM_PERMISSIONS
    assert "account.manage.platform" in perms.permissions

    with pytest.raises(RoleAssignmentError) as exc_info:
        await service.assign_platform_super_admin(session, target_user_id=corp.id)
    assert exc_info.value.reason == "PSA_REQUIRES_NULL_ACCOUNT"
    await session.commit()
    events = await repo.list_audit_events(session, result="denied")
    assert len(events) == 1
    assert events[0].reason == "PSA_REQUIRES_NULL_ACCOUNT"
    assert events[0].actor_type == "system"
    assert events[0].subject_user_id == corp.id


# ── AC ⑦：授权拒绝与种子变更各写一条审计 ──


async def test_check_permission_writes_denied_audit(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """AC ⑦：权限拒绝审计（04 §10.8，Actor/Subject、结果 denied）。"""
    setup = await _setup(repo, session)
    alice = setup.alice
    assert await setup.service.check_permission(session, user_id=alice.id, permission_code="memory.read.self")
    assert await repo.list_audit_events(session, result="denied") == []

    granted = await setup.service.check_permission(
        session, user_id=alice.id, permission_code="user.create"
    )
    await session.commit()
    assert granted is False
    events = await repo.list_audit_events(session, result="denied")
    assert len(events) == 1
    event = events[0]
    assert event.action == "permission.check"
    assert event.actor_user_id == alice.id
    assert event.subject_user_id == alice.id
    assert event.subject_account_id == setup.acme.id
    assert event.target_id == "user.create"
    assert event.reason == "PERMISSION_NOT_GRANTED"


async def test_assign_role_unknown_target_not_audited(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """not-found 语义：目标不存在按 EntityNotFoundError，不写审计（防枚举）。"""
    setup = await _setup(repo, session)
    with pytest.raises(EntityNotFoundError):
        await setup.service.assign_role(
            session,
            actor_user_id=setup.psa.id,
            actor_account_id=None,
            target_user_id=uuid.uuid4(),
            role_code=USER,
        )
    await session.commit()
    assert await repo.list_audit_events(session, result="denied") == []
