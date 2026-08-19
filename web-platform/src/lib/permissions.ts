/**
 * 前端权限目录与 Guard/按钮判定（06 §13.4，P3-E1 冻结点）。
 *
 * - code 清单与后端 `openviking/server/platform/iam/permissions.py`（03 §9.1）逐项一致，
 *   后续 Epic 若后端追加权限码，本文件只 append、不重构既有条目；
 * - 前端权限只负责体验（隐藏/禁用），不是安全边界（06 §13.4）；
 *   后端每次请求仍必须重复鉴权。
 */

import type { AuthMeResult } from "@/features/auth/auth-state";

export const ROLES = {
  PLATFORM_SUPER_ADMIN: "platform_super_admin",
  ACCOUNT_ADMIN: "account_admin",
  USER: "user",
} as const;

export type RoleCode = (typeof ROLES)[keyof typeof ROLES];

/** 权限码目录：`<domain>.<action>.<scope>` 或 `<domain>.<visibility>.<action>.<scope>`。 */
export const PERMISSION_CODES = {
  // account
  "account.read": "查看当前 Account 基本信息",
  "account.update": "修改 Account 基本信息",
  "account.delete": "软删除 Account（进入 30 天回收期）",
  "account.read.platform": "平台范围查看全部 Account",
  "account.manage.platform": "创建/管理 Account（含 Provisioning 重试）",
  // user
  "user.read": "查看当前 Account 用户列表",
  "user.create": "创建普通用户（角色固定 user）",
  "user.update": "更新用户状态等基本信息",
  "user.disable": "禁用用户（撤销全部登录 Session 与 API Key）",
  "user.delete": "软删除用户（进入 30 天回收期）",
  "user.read.account": "查看当前 Account 用户详情（v0.1 预留）",
  "user.read.platform": "平台范围查看用户",
  "user.password.reset.account": "重置当前 Account 严格低等级用户密码",
  "user.password.reset.platform": "平台范围重置低等级用户密码（禁目标 PSA）",
  // credential
  "credential.read.self": "查看自己的 API Key 元数据",
  "credential.create.self": "创建自己的 API Key",
  "credential.revoke.self": "撤销自己的 API Key",
  "credential.read.account": "查看 Account 用户 API Key 元数据",
  "credential.revoke.account": "撤销 Account 用户 API Key",
  "credential.read.platform": "平台范围查看 API Key 元数据",
  "credential.revoke.platform": "平台范围撤销 API Key",
  // role
  "role.read": "查看内置角色与权限矩阵",
  "role.assign.platform": "平台范围角色授予/提升（仅 user→account_admin）",
  // memory
  "memory.read.self": "查看自己的记忆",
  "memory.read.account": "查看 Account 成员记忆",
  "memory.read.platform": "平台范围查看记忆",
  // resource.user_private
  "resource.user_private.read.self": "读取自己的私有 Resource",
  "resource.user_private.write.self": "写入自己的私有 Resource",
  "resource.user_private.delete.self": "软删除自己的私有 Resource",
  "resource.user_private.read.account": "查看 Account 成员私有 Resource",
  "resource.user_private.write.account": "修改 Account 成员私有 Resource（v0.1 预留）",
  "resource.user_private.delete.account": "删除 Account 成员私有 Resource（v0.1 预留）",
  "resource.user_private.read.platform": "平台范围查看成员私有 Resource",
  "resource.user_private.write.platform": "平台范围修改成员私有 Resource（v0.1 预留）",
  "resource.user_private.delete.platform": "平台范围删除成员私有 Resource（v0.1 预留）",
  // resource.account_shared
  "resource.account_shared.read.account": "读取当前 Account 共享 Resource",
  "resource.account_shared.write.account": "写入当前 Account 共享 Resource（含发布为共享副本）",
  "resource.account_shared.delete.account": "删除当前 Account 共享 Resource",
  "resource.account_shared.read.platform": "平台范围读取共享 Resource",
  "resource.account_shared.write.platform": "平台范围写入共享 Resource",
  "resource.account_shared.delete.platform": "平台范围删除共享 Resource",
  // session
  "session.read.self": "查看自己的对话 Session 列表",
  "session.write.self": "写入自己的对话 Session",
  "session.delete.self": "软删除自己的对话 Session",
  "session.commit.self": "Context Commit 自己的对话 Session",
  "session.read.account": "查看 Account 成员对话 Session 历史",
  "session.read.platform": "平台范围查看对话 Session 历史",
  // skill.user_private
  "skill.user_private.read.self": "读取自己的私有 Skill",
  "skill.user_private.use.self": "在自己的执行入口使用私有 Skill",
  "skill.user_private.manage.self": "创建/更新/软删/恢复自己的私有 Skill",
  "skill.user_private.read.account": "查看 Account 成员私有 Skill",
  "skill.user_private.publish.account": "将 Account 成员私有 Skill 原地发布为共享",
  "skill.user_private.read.platform": "平台范围只读私有 Skill",
  // skill.account_shared
  "skill.account_shared.read.account": "读取当前 Account 共享 Skill",
  "skill.account_shared.use.account": "在被允许的执行入口使用共享 Skill",
  "skill.account_shared.manage.account": "管理当前 Account 共享 Skill",
  "skill.account_shared.read.platform": "平台范围只读共享 Skill",
  // audit / monitoring / privacy_config
  "audit.read": "查看审计事件",
  "monitoring.read": "查看业务健康摘要",
  "privacy_config.read.self": "读取自己的 Skill 私密配置",
  "privacy_config.write.self": "写入自己的 Skill 私密配置",
  // integration.oauth
  "integration.oauth.authorize.self": "授权 MCP OAuth 客户端",
  "integration.oauth.read.self": "查看自己的 OAuth 授权",
  "integration.oauth.revoke.self": "撤销自己的 OAuth 授权",
  // task
  "task.read.self": "查看自己的处理任务",
  "task.cancel.self": "取消自己的处理任务",
  "task.read.account_shared": "查看当前 Account 共享对象处理任务",
  "task.cancel.account_shared": "取消当前 Account 共享对象任务",
  "task.read.platform": "平台范围查看处理任务",
  "task.cancel.platform": "平台范围取消处理任务",
} as const;

export type PermissionCode = keyof typeof PERMISSION_CODES;

export function hasPermission(
  permissions: readonly string[] | undefined,
  code: string,
): boolean {
  if (!permissions) return false;
  return permissions.includes(code);
}

export function isAccountAdmin(roles: readonly string[] | undefined): boolean {
  return roles?.includes(ROLES.ACCOUNT_ADMIN) ?? false;
}

export function isPlatformSuperAdmin(roles: readonly string[] | undefined): boolean {
  return roles?.includes(ROLES.PLATFORM_SUPER_ADMIN) ?? false;
}

/** 按钮/入口判定（AC②）：需要权限时无权限即禁用/隐藏。 */
export function canPerform(me: AuthMeResult | null, code: string): boolean {
  return me != null && hasPermission(me.permissions, code);
}

/** 共享区管理能力：Account Admin 专属（普通 User 在共享页不显示管理入口，06 §13.3）。 */
export const ACCOUNT_SHARED_MANAGE_CODES = [
  "resource.account_shared.write.account",
  "resource.account_shared.delete.account",
  "skill.account_shared.manage.account",
  "skill.user_private.publish.account",
] as const;

export function canManageSharedContent(me: AuthMeResult | null): boolean {
  if (!me) return false;
  return ACCOUNT_SHARED_MANAGE_CODES.some((code) => me.permissions.includes(code));
}

/**
 * 内置角色等级（03 §9.3：platform_super_admin > account_admin > user，P4-E1）。
 * 分级密码重置仅允许对严格低级别目标操作（85.4：`actor_role_rank > target_role_rank`）。
 * 前端隐藏按钮不是安全边界，后端仍强制校验（03 §8.3）。
 */
export const ROLE_RANKS: Record<RoleCode, number> = {
  [ROLES.PLATFORM_SUPER_ADMIN]: 3,
  [ROLES.ACCOUNT_ADMIN]: 2,
  [ROLES.USER]: 1,
};

export function roleRank(role: string | undefined): number {
  if (!role) return 0;
  return ROLE_RANKS[role as RoleCode] ?? 0;
}

/** Actor 有效角色中的最高等级（多角色取最大 rank）。 */
export function actorMaxRoleRank(roles: readonly string[] | undefined): number {
  if (!roles) return 0;
  return roles.reduce((max, role) => Math.max(max, roleRank(role)), 0);
}

/** 分级密码重置判定：仅严格低级别且角色等级已知的目标（03 §8.3、13 §85.4，P4-E1）。 */
export function canResetTargetPassword(
  actorRoles: readonly string[] | undefined,
  targetRole: string | undefined,
): boolean {
  const targetRank = roleRank(targetRole);
  return targetRank > 0 && actorMaxRoleRank(actorRoles) > targetRank;
}
