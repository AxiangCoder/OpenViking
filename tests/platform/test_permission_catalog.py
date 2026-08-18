"""P1-E2 权限目录单测（03 §9.1–9.3；无需 DB）。

覆盖验收标准 ② 的 4 码显式剔除与「PSA Skill 固定只读」语义，以及
① 的三内置角色 rank/ov_base_role 定义（数据落库断言见 test_rbac.py）。
"""

from __future__ import annotations

from openviking.server.platform.iam.permissions import (
    ACCOUNT_ADMIN,
    ACCOUNT_ADMIN_PERMISSIONS,
    BUILTIN_ROLE_PERMISSIONS,
    BUILTIN_ROLES,
    PERMISSION_SPECS,
    PLATFORM_PERMISSIONS,
    PLATFORM_SKILL_READ_ONLY,
    PLATFORM_SUPER_ADMIN,
    ROLE_RANKS,
    SKILL_WRITE_OR_USE_PERMISSIONS,
    USER,
    USER_PERMISSIONS,
)

# 03 §9.1 全部 70 个 Permission Code（与设计文档逐字核对）
DESIGN_91_CODES: frozenset[str] = frozenset(
    {
        "account.read",
        "account.update",
        "account.delete",
        "account.read.platform",
        "account.manage.platform",
        "user.read",
        "user.create",
        "user.update",
        "user.disable",
        "user.delete",
        "user.read.account",
        "user.read.platform",
        "user.password.reset.account",
        "user.password.reset.platform",
        "credential.read.self",
        "credential.create.self",
        "credential.revoke.self",
        "credential.read.account",
        "credential.revoke.account",
        "credential.read.platform",
        "credential.revoke.platform",
        "role.read",
        "role.assign.platform",
        "memory.read.self",
        "memory.read.account",
        "memory.read.platform",
        "resource.user_private.read.self",
        "resource.user_private.write.self",
        "resource.user_private.delete.self",
        "resource.user_private.read.account",
        "resource.user_private.write.account",
        "resource.user_private.delete.account",
        "resource.user_private.read.platform",
        "resource.user_private.write.platform",
        "resource.user_private.delete.platform",
        "resource.account_shared.read.account",
        "resource.account_shared.write.account",
        "resource.account_shared.delete.account",
        "resource.account_shared.read.platform",
        "resource.account_shared.write.platform",
        "resource.account_shared.delete.platform",
        "session.read.self",
        "session.write.self",
        "session.delete.self",
        "session.commit.self",
        "session.read.account",
        "session.read.platform",
        "skill.user_private.read.self",
        "skill.user_private.use.self",
        "skill.user_private.manage.self",
        "skill.user_private.read.account",
        "skill.user_private.publish.account",
        "skill.user_private.read.platform",
        "skill.account_shared.read.account",
        "skill.account_shared.use.account",
        "skill.account_shared.manage.account",
        "skill.account_shared.read.platform",
        "audit.read",
        "monitoring.read",
        "privacy_config.read.self",
        "privacy_config.write.self",
        "integration.oauth.authorize.self",
        "integration.oauth.read.self",
        "integration.oauth.revoke.self",
        "task.read.self",
        "task.cancel.self",
        "task.read.account_shared",
        "task.cancel.account_shared",
        "task.read.platform",
        "task.cancel.platform",
    }
)

VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}


def test_catalog_covers_design_91() -> None:
    """03 §9.1 全部 70 个 code 均入种子目录，且目录无多余 code。"""
    catalog = {code for code, _description, _risk in PERMISSION_SPECS}
    assert len(catalog) == 70
    assert catalog == DESIGN_91_CODES


def test_catalog_specs_well_formed() -> None:
    """每个 code：≥2 段；domain/action 按第一个 '.' 拆解；risk_level 合法。"""
    for code, description, risk_level in PERMISSION_SPECS:
        assert len(code.split(".")) >= 2, code
        assert risk_level in VALID_RISK_LEVELS, code
        assert description.strip(), code
        domain, action = code.split(".", 1)
        assert domain and action, code


def test_three_builtin_roles_defined() -> None:
    """AC ① 定义侧：恰三内置角色，rank=3/2/1，ov_base_role=None/admin/user。"""
    assert set(BUILTIN_ROLES) == {PLATFORM_SUPER_ADMIN, ACCOUNT_ADMIN, USER}
    assert set(ROLE_RANKS) == {PLATFORM_SUPER_ADMIN, ACCOUNT_ADMIN, USER}
    assert ROLE_RANKS[PLATFORM_SUPER_ADMIN] == 3
    assert ROLE_RANKS[ACCOUNT_ADMIN] == 2
    assert ROLE_RANKS[USER] == 1
    assert BUILTIN_ROLES[PLATFORM_SUPER_ADMIN].ov_base_role is None
    assert BUILTIN_ROLES[ACCOUNT_ADMIN].ov_base_role == "admin"
    assert BUILTIN_ROLES[USER].ov_base_role == "user"


def test_psa_excludes_all_skill_write_or_use() -> None:
    """AC ②：PSA 权限集合无任何 `skill.*.manage/publish/use`（含 4 码显式剔除）。"""
    forbidden = {
        p
        for p in PLATFORM_PERMISSIONS
        if p.startswith("skill.") and p.split(".")[2] in {"manage", "publish", "use"}
    }
    assert forbidden == frozenset()


def test_skill_write_or_use_set_matches_design() -> None:
    """03 §9.3 回填：显式剔除集合恰为设计列出的 4 个 Skill 写/用码。"""
    assert SKILL_WRITE_OR_USE_PERMISSIONS == frozenset(
        {
            "skill.user_private.manage.self",
            "skill.user_private.publish.account",
            "skill.account_shared.use.account",
            "skill.account_shared.manage.account",
        }
    )
    # 4 码全部存在于 Account Admin 集合（若不存在则剔除无意义，语义漂移哨兵）
    assert SKILL_WRITE_OR_USE_PERMISSIONS <= ACCOUNT_ADMIN_PERMISSIONS
    # 且均不在 PSA 集合
    assert PLATFORM_PERMISSIONS.isdisjoint(SKILL_WRITE_OR_USE_PERMISSIONS)


def test_psa_skill_fixed_to_platform_read_only() -> None:
    """03 §9.1：PSA 的 Skill 权限固定为两个 platform 只读码，不多不少。"""
    psa_skill = {p for p in PLATFORM_PERMISSIONS if p.startswith("skill.")}
    assert psa_skill == PLATFORM_SKILL_READ_ONLY
    assert psa_skill == {
        "skill.user_private.read.platform",
        "skill.account_shared.read.platform",
    }


def test_psa_not_derived_by_naive_account_admin_inheritance() -> None:
    """03 §9.3：PSA 与 AA 的 Skill 权限完全不相交（显式构造，无继承残留）。"""
    aa_skill = {p for p in ACCOUNT_ADMIN_PERMISSIONS if p.startswith("skill.")}
    assert PLATFORM_PERMISSIONS.isdisjoint(aa_skill)


def test_role_hierarchy_subsets() -> None:
    """03 §9.2：user ⊂ account_admin（account_admin 在 user 之上叠加管理权限）。"""
    assert USER_PERMISSIONS < ACCOUNT_ADMIN_PERMISSIONS


def test_granted_codes_all_exist_in_catalog() -> None:
    """种子落库前提：任何角色授予的 code 都必须在目录中（无孤儿授权）。"""
    catalog = {code for code, _description, _risk in PERMISSION_SPECS}
    for code, perms in BUILTIN_ROLE_PERMISSIONS.items():
        assert perms <= catalog, f"role {code} grants codes missing from catalog"


def test_reserved_high_risk_codes_ungranted() -> None:
    """03 §9.3：修改/删除其他用户私有数据的预留高风险管理权限 v0.1 不授予任何角色。"""
    granted = frozenset().union(*BUILTIN_ROLE_PERMISSIONS.values())
    reserved = {
        "resource.user_private.write.account",
        "resource.user_private.delete.account",
        "resource.user_private.write.platform",
        "resource.user_private.delete.platform",
    }
    assert granted.isdisjoint(reserved)
