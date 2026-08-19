"""TargetPolicy：动作→Permission 映射与默认目标规则（05 §11.5，14 号计划 §97.2）。

最低映射规则（05 §11.5 表）：

| 目标/动作 | 必需 Permission |
| --- | --- |
| 读取/检索自己的 `viking://user/{actor}/resources/**` | `resource.user_private.read.self` |
| 新增/写入/改名/移动/打标签/恢复自己的私有 Resource | `resource.user_private.write.self` |
| 删除自己的私有 Resource | `resource.user_private.delete.self` |
| 读取/检索 `viking://resources/**` | `.account`（本 Account）/ `.platform`（平台管理） |
| 任何会改变 `viking://resources/**` 的动作 | `.write.account/platform`；删除用 `.delete.*` |
| 读取/使用/管理自己的 User 私有 Skill | `skill.user_private.read/use/manage.self` |
| 查看其他 User 的私有 Skill | Account Admin `.read.account`；PSA `.read.platform` |
| 发布其他 User 的私有 Skill | 仅 Account Admin `skill.user_private.publish.account` |
| 读取/使用/管理 `viking://agent/skills/**` | `.read/.use.account`；仅 Admin `.manage.account`；PSA 仅 `.read.platform` |
| `agent/endpoints/tools/payments` 或内部根 | 默认拒绝（须另行定义控制面 Permission） |

"会改变"包括 `add_resource`、`write`、`mkdir`、`mv`（源与目标）、`set_tags`、
归档、导入、恢复和批量操作；跨可见性移动不得作为普通 `mv` 放行（05 §11.5）。

默认目标规则（05 §11.5）：API Key/OAuth 调用 `add_resource` 未显式指定目标时
服务端强制 `viking://user/{actor_ov_user_id}/resources/**`；Skill 默认保持
`viking://user/{actor_ov_user_id}/skills/**`；显式共享目标不改变身份，
只触发共享写 Permission 检查（普通 User 403）。
"""

from __future__ import annotations

from dataclasses import dataclass

_ACTION_MAP: dict[tuple[str, str, str], str] = {
    # (object_type, visibility, action) → 权限后缀（self/account/platform 由 scope 决定）
    ("resource", "user_private", "read"): "resource.user_private.read.{scope}",
    ("resource", "user_private", "write"): "resource.user_private.write.{scope}",
    ("resource", "user_private", "delete"): "resource.user_private.delete.{scope}",
    ("resource", "account_shared", "read"): "resource.account_shared.read.{scope}",
    ("resource", "account_shared", "write"): "resource.account_shared.write.{scope}",
    ("resource", "account_shared", "delete"): "resource.account_shared.delete.{scope}",
    ("skill", "user_private", "read"): "skill.user_private.read.{scope}",
    ("skill", "user_private", "use"): "skill.user_private.use.{scope}",
    ("skill", "user_private", "manage"): "skill.user_private.manage.{scope}",
    ("skill", "user_private", "publish"): "skill.user_private.publish.account",
    ("skill", "account_shared", "read"): "skill.account_shared.read.{scope}",
    ("skill", "account_shared", "use"): "skill.account_shared.use.{scope}",
    ("skill", "account_shared", "manage"): "skill.account_shared.manage.{scope}",
}

# scope 归一：self（Actor 自己的私有对象）不需要 scope 占位。
_SCOPE_FOR_PERMISSION = {
    "self": "self",
    "account": "account",
    "platform": "platform",
}


@dataclass(frozen=True)
class TargetPolicy:
    """05 §11.5：动作→Permission 映射 + 默认目标规则。

    `permission_for(object_type, visibility, action, scope)` 返回必需
    Permission code；`None` 表示该组合在产品策略中无对应权限（默认拒绝）。
    """

    def permission_for(
        self,
        *,
        object_type: str | None,
        visibility: str,
        action: str,
        scope: str,
    ) -> str | None:
        if object_type is None:
            return None
        template = _ACTION_MAP.get((object_type, visibility, action))
        if template is None:
            return None
        if template == "skill.user_private.publish.account":
            # 发布其他 User 的私有 Skill：仅 Account Admin（scope 无关）
            return template
        perm_scope = _SCOPE_FOR_PERMISSION.get(scope, "self")
        return template.format(scope=perm_scope)

    def default_target_uri(self, actor_ov_user_id: str, *, object_type: str) -> str:
        """默认目标规则（05 §11.5）：未显式指定目标时强制 Actor 私有根。"""
        if object_type == "skill":
            return f"viking://user/{actor_ov_user_id}/skills/"
        return f"viking://user/{actor_ov_user_id}/resources/"
