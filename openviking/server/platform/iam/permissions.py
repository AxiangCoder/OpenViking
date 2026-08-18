"""RBAC 权限目录与三内置角色定义（03 §9.1–9.3，05 §11 `iam/permissions.py`）。

本模块是 P1-E2 的唯一事实来源（code 定义），种子服务（`service.RbacService`）
按本文件内容幂等落库并维护全局 `permission_schema_version`（04 §10.5）。

目录语义（03 §9.1）：

- 一般数据类 Permission Code 采用 `<domain>.<action>.<scope>`；
  Resource/Skill 同时存在两种可见性，采用 `<domain>.<visibility>.<action>.<scope>`。
- `risk_level ∈ {low, medium, high, critical}`（04 §10.5）。设计文档未逐码指定，
  本模块按「越权影响面 + 破坏性」给出稳定分级，代码注释注明归因。
- v0.1 只提供三个内置角色，不开放自定义角色 CRUD（03 §9.2）。

## PSA 的 Skill 权限（03 §9.1 固定值 + §9.3 回填）

Platform Super Admin 的 Skill 权限**固定为** `skill.user_private.read.platform` 与
`skill.account_shared.read.platform` 两个只读码，不从平台最高角色推导任何 Skill
写入/发布/恢复/使用权限。种子实现**不得经继承 Account Admin 权限集合隐式获得**
Skill 写/用权限，必须显式剔除（03 §9.3 回填发现，Spike README §4.2 #4）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

PLATFORM_SUPER_ADMIN = "platform_super_admin"
ACCOUNT_ADMIN = "account_admin"
USER = "user"

# 平台角色等级（04 §10.4）：数值越大等级越高，仅用于严格等级比较（03 §8.3）。
# 与 OpenViking `Role` 内置 rank（USER=0/ADMIN=1/ROOT=2，openviking/server/identity.py）
# 完全独立、禁止混用与数值映射。
ROLE_RANKS: dict[str, int] = {
    PLATFORM_SUPER_ADMIN: 3,
    ACCOUNT_ADMIN: 2,
    USER: 1,
}

OV_BASE_ROLES: dict[str, str | None] = {
    PLATFORM_SUPER_ADMIN: None,
    ACCOUNT_ADMIN: "admin",
    USER: "user",
}

# ── Permission 目录（03 §9.1 全部 code，共 70 项）──
# 元组：(code, description, risk_level)；domain/action 由 code 按第一个 "." 拆解。

PERMISSION_SPECS: tuple[tuple[str, str, str], ...] = (
    # account（5）
    ("account.read", "查看当前 Account 基本信息", "low"),
    ("account.update", "修改 Account 基本信息", "high"),
    ("account.delete", "软删除 Account（进入 30 天回收期）", "critical"),
    ("account.read.platform", "平台范围查看全部 Account", "high"),
    ("account.manage.platform", "创建/管理 Account（含 Provisioning 重试）", "critical"),
    # user（9）
    ("user.read", "查看当前 Account 用户列表", "medium"),
    ("user.create", "创建普通用户（角色固定 user）", "high"),
    ("user.update", "更新用户状态等基本信息", "high"),
    ("user.disable", "禁用用户（撤销全部登录 Session 与 API Key）", "critical"),
    ("user.delete", "软删除用户（进入 30 天回收期）", "critical"),
    ("user.read.account", "查看当前 Account 用户详情（v0.1 预留）", "medium"),
    ("user.read.platform", "平台范围查看用户", "high"),
    ("user.password.reset.account", "重置当前 Account 严格低等级用户密码", "critical"),
    ("user.password.reset.platform", "平台范围重置低等级用户密码（禁目标 PSA）", "critical"),
    # credential（7）
    ("credential.read.self", "查看自己的 API Key 元数据", "medium"),
    ("credential.create.self", "创建自己的 API Key", "high"),
    ("credential.revoke.self", "撤销自己的 API Key", "high"),
    ("credential.read.account", "查看 Account 用户 API Key 元数据", "high"),
    ("credential.revoke.account", "撤销 Account 用户 API Key", "critical"),
    ("credential.read.platform", "平台范围查看 API Key 元数据", "high"),
    ("credential.revoke.platform", "平台范围撤销 API Key", "critical"),
    # role（2）
    ("role.read", "查看内置角色与权限矩阵", "medium"),
    ("role.assign.platform", "平台范围角色授予/提升（仅 user→account_admin）", "critical"),
    # memory（3）
    ("memory.read.self", "查看自己的记忆", "low"),
    ("memory.read.account", "查看 Account 成员记忆", "medium"),
    ("memory.read.platform", "平台范围查看记忆", "medium"),
    # resource.user_private（9）
    ("resource.user_private.read.self", "读取自己的私有 Resource", "low"),
    ("resource.user_private.write.self", "写入自己的私有 Resource", "medium"),
    ("resource.user_private.delete.self", "软删除自己的私有 Resource", "high"),
    ("resource.user_private.read.account", "查看 Account 成员私有 Resource", "medium"),
    ("resource.user_private.write.account", "修改 Account 成员私有 Resource（v0.1 预留，无平台 API 端点）", "critical"),
    ("resource.user_private.delete.account", "删除 Account 成员私有 Resource（v0.1 预留，无平台 API 端点）", "critical"),
    ("resource.user_private.read.platform", "平台范围查看成员私有 Resource", "high"),
    ("resource.user_private.write.platform", "平台范围修改成员私有 Resource（v0.1 预留，无平台 API 端点）", "critical"),
    ("resource.user_private.delete.platform", "平台范围删除成员私有 Resource（v0.1 预留，无平台 API 端点）", "critical"),
    # resource.account_shared（6）
    ("resource.account_shared.read.account", "读取当前 Account 共享 Resource", "low"),
    ("resource.account_shared.write.account", "写入当前 Account 共享 Resource（含发布为共享副本）", "high"),
    ("resource.account_shared.delete.account", "删除当前 Account 共享 Resource", "high"),
    ("resource.account_shared.read.platform", "平台范围读取共享 Resource", "medium"),
    ("resource.account_shared.write.platform", "平台范围写入共享 Resource", "high"),
    ("resource.account_shared.delete.platform", "平台范围删除共享 Resource", "critical"),
    # session（6）
    ("session.read.self", "查看自己的对话 Session 列表", "medium"),
    ("session.write.self", "写入自己的对话 Session", "medium"),
    ("session.delete.self", "软删除自己的对话 Session", "high"),
    ("session.commit.self", "Context Commit 自己的对话 Session", "medium"),
    ("session.read.account", "查看 Account 成员对话 Session 历史", "high"),
    ("session.read.platform", "平台范围查看对话 Session 历史", "high"),
    # skill.user_private（6）
    ("skill.user_private.read.self", "读取自己的私有 Skill", "low"),
    ("skill.user_private.use.self", "在自己的执行入口使用私有 Skill", "medium"),
    ("skill.user_private.manage.self", "创建/更新/软删/恢复自己的私有 Skill", "high"),
    ("skill.user_private.read.account", "查看 Account 成员私有 Skill", "medium"),
    ("skill.user_private.publish.account", "将 Account 成员私有 Skill 原地发布为共享（独立高风险权限）", "critical"),
    ("skill.user_private.read.platform", "平台范围只读私有 Skill", "medium"),
    # skill.account_shared（4）
    ("skill.account_shared.read.account", "读取当前 Account 共享 Skill", "low"),
    ("skill.account_shared.use.account", "在被允许的执行入口使用共享 Skill", "medium"),
    ("skill.account_shared.manage.account", "管理当前 Account 共享 Skill", "high"),
    ("skill.account_shared.read.platform", "平台范围只读共享 Skill", "medium"),
    # audit / monitoring / privacy_config（4）
    ("audit.read", "查看审计事件", "medium"),
    ("monitoring.read", "查看业务健康摘要", "medium"),
    ("privacy_config.read.self", "读取自己的 Skill 私密配置", "medium"),
    ("privacy_config.write.self", "写入自己的 Skill 私密配置", "high"),
    # integration.oauth（3）
    ("integration.oauth.authorize.self", "授权 MCP OAuth 客户端", "medium"),
    ("integration.oauth.read.self", "查看自己的 OAuth 授权", "medium"),
    ("integration.oauth.revoke.self", "撤销自己的 OAuth 授权", "high"),
    # task（6）
    ("task.read.self", "查看自己的处理任务", "low"),
    ("task.cancel.self", "取消自己的处理任务", "medium"),
    ("task.read.account_shared", "查看当前 Account 共享对象处理任务", "medium"),
    ("task.cancel.account_shared", "取消当前 Account 共享对象任务", "high"),
    ("task.read.platform", "平台范围查看处理任务", "medium"),
    ("task.cancel.platform", "平台范围取消处理任务", "high"),
)

# ── 三内置角色权限矩阵（03 §9.3 逐行映射，05 §12.6 路由表交叉验证）──

# user：自己 + 当前 Account 共享只读/使用（03 §9.2）
USER_PERMISSIONS: frozenset[str] = frozenset(
    {
        "credential.read.self",
        "credential.create.self",
        "credential.revoke.self",
        "memory.read.self",
        "session.read.self",
        "session.write.self",
        "session.delete.self",
        "session.commit.self",
        "resource.user_private.read.self",
        "resource.user_private.write.self",
        "resource.user_private.delete.self",
        "resource.account_shared.read.account",
        "skill.user_private.read.self",
        "skill.user_private.use.self",
        "skill.user_private.manage.self",
        "skill.account_shared.read.account",
        "skill.account_shared.use.account",
        "task.read.self",
        "task.cancel.self",
        "task.read.account_shared",
        "privacy_config.read.self",
        "privacy_config.write.self",
        "integration.oauth.authorize.self",
        "integration.oauth.read.self",
        "integration.oauth.revoke.self",
    }
)

# account_admin：当前 Account 管理（03 §9.2）
ACCOUNT_ADMIN_PERMISSIONS: frozenset[str] = frozenset(
    USER_PERMISSIONS
    | {
        "account.read",
        "user.read",
        "user.create",
        "user.update",
        "user.disable",
        "user.delete",
        "user.password.reset.account",
        "role.read",
        "credential.read.account",
        "credential.revoke.account",
        "memory.read.account",
        "session.read.account",
        "resource.user_private.read.account",
        "resource.account_shared.write.account",
        "resource.account_shared.delete.account",
        "skill.user_private.read.account",
        "skill.user_private.publish.account",
        "skill.account_shared.manage.account",
        "audit.read",
        "monitoring.read",
        "task.cancel.account_shared",
    }
)

# PSA 必须显式剔除的 4 个 Skill 写/用权限（03 §9.3 回填，Spike §4.2 #4）。
# 设计要求的剔除范围比这 4 个更宽：PSA 的 Skill 权限固定为两个 platform 只读码。
SKILL_WRITE_OR_USE_PERMISSIONS: frozenset[str] = frozenset(
    {
        "skill.user_private.manage.self",
        "skill.user_private.publish.account",
        "skill.account_shared.use.account",
        "skill.account_shared.manage.account",
    }
)

# PSA 的 Skill 权限固定值（03 §9.1：「Platform Super Admin 的 Skill 权限固定为
# skill.user_private.read.platform 与 skill.account_shared.read.platform」）
PLATFORM_SKILL_READ_ONLY: frozenset[str] = frozenset(
    {
        "skill.user_private.read.platform",
        "skill.account_shared.read.platform",
    }
)

# account_admin 持有的全部 Skill 码（PSA 集合按「全部剔除再补固定只读码」构造，
# 保证任何 Skill 写/用权限都不可能经继承隐式获得）
_ACCOUNT_ADMIN_SKILL_CODES: frozenset[str] = frozenset(
    p for p in ACCOUNT_ADMIN_PERMISSIONS if p.startswith("skill.")
)

_PLATFORM_EXTRA_PERMISSIONS: frozenset[str] = frozenset(
    {
        "account.read.platform",
        "account.manage.platform",
        "account.update",
        "account.delete",
        "user.read.platform",
        "user.password.reset.platform",
        "role.assign.platform",
        "credential.read.platform",
        "credential.revoke.platform",
        "memory.read.platform",
        "session.read.platform",
        "resource.user_private.read.platform",
        "resource.account_shared.read.platform",
        "resource.account_shared.write.platform",
        "resource.account_shared.delete.platform",
        "task.read.platform",
        "task.cancel.platform",
    }
)

# platform_super_admin：全平台管理，Skill 只读（03 §9.2）
PLATFORM_PERMISSIONS: frozenset[str] = frozenset(
    (ACCOUNT_ADMIN_PERMISSIONS - _ACCOUNT_ADMIN_SKILL_CODES)
    | PLATFORM_SKILL_READ_ONLY
    | _PLATFORM_EXTRA_PERMISSIONS
)

BUILTIN_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    USER: USER_PERMISSIONS,
    ACCOUNT_ADMIN: ACCOUNT_ADMIN_PERMISSIONS,
    PLATFORM_SUPER_ADMIN: PLATFORM_PERMISSIONS,
}


@dataclass(frozen=True)
class BuiltinRoleSpec:
    """三内置角色全局单行定义（04 §10.4）。"""

    code: str
    name: str
    description: str
    ov_base_role: str | None
    rank: int


BUILTIN_ROLES: dict[str, BuiltinRoleSpec] = {
    PLATFORM_SUPER_ADMIN: BuiltinRoleSpec(
        code=PLATFORM_SUPER_ADMIN,
        name="平台超级管理员",
        description="平台最高权限角色：管理全部 Account/用户与大部分数据，Skill 只读（03 §9.2）",
        ov_base_role=None,
        rank=3,
    ),
    ACCOUNT_ADMIN: BuiltinRoleSpec(
        code=ACCOUNT_ADMIN,
        name="账号管理员",
        description="当前 Account 管理员：管理本 Account 用户与共享对象（03 §9.2）",
        ov_base_role="admin",
        rank=2,
    ),
    USER: BuiltinRoleSpec(
        code=USER,
        name="普通用户",
        description="普通用户：管理自己的私有数据，只读/使用当前 Account 共享对象（03 §9.2）",
        ov_base_role="user",
        rank=1,
    ),
}


@dataclass(frozen=True)
class UserPermissions:
    """有效权限计算产物（03 §9.4：union(active role permissions) − disabled）。"""

    role_codes: tuple[str, ...]
    permissions: frozenset[str]
    ov_base_role: str | None
    rank: int


@dataclass(frozen=True)
class RoleView:
    """内置角色只读视图（GET /admin/roles 数据基础，05 §12.6）。"""

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    ov_base_role: str | None
    rank: int
    is_system: bool
    status: str
    permissions: frozenset[str]
