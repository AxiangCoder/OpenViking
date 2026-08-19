/**
 * 三布局导航清单（06 §13.2）。
 * 依 Permission/Role 过滤（AC②）：普通 User 看不到 /admin 与 /platform 入口；
 * 全局导航无 Account 切换入口（07 §21 条目 14）；/studio 不进任何导航（06 §13.6）。
 */

import type { PermissionCode } from "@/lib/permissions";

export interface NavItem {
  to: string;
  label: string;
  /** 精确匹配高亮（列表页 vs 详情页）。 */
  exact?: boolean;
  /** 需要该权限才显示。 */
  permission?: PermissionCode;
}

export const APP_NAV: NavItem[] = [
  { to: "/app", label: "首页", exact: true },
  { to: "/app/search", label: "检索" },
  { to: "/app/resources/private", label: "我的 Resource" },
  { to: "/app/resources/shared", label: "共享 Resource", permission: "resource.account_shared.read.account" },
  { to: "/app/skills/private", label: "我的 Skill" },
  { to: "/app/skills/shared", label: "共享 Skill", permission: "skill.account_shared.read.account" },
  { to: "/app/sessions", label: "Sessions", permission: "session.read.self" },
  { to: "/app/activity", label: "Activity", permission: "task.read.self" },
  { to: "/app/recycle-bin", label: "回收站" },
  { to: "/app/profile", label: "个人设置", exact: true },
];

export const ADMIN_NAV: NavItem[] = [
  { to: "/admin/users", label: "用户管理", permission: "user.read" },
  { to: "/admin/shared-resources", label: "共享 Resource" },
  { to: "/admin/shared-skills", label: "共享 Skill" },
  { to: "/admin/roles", label: "角色与权限", permission: "role.read" },
  { to: "/admin/audit", label: "审计", permission: "audit.read" },
  { to: "/admin/activity", label: "Activity", permission: "task.read.account_shared" },
  { to: "/admin/monitoring", label: "监控", permission: "monitoring.read" },
  { to: "/admin/recycle-bin", label: "回收站" },
  { to: "/admin/settings", label: "Account 设置" },
];

export const PLATFORM_NAV: NavItem[] = [
  { to: "/platform/accounts", label: "Accounts" },
  { to: "/platform/audit", label: "审计", permission: "audit.read" },
  { to: "/platform/activity", label: "Activity", permission: "task.read.platform" },
  { to: "/platform/monitoring", label: "监控", permission: "monitoring.read" },
  { to: "/platform/recycle-bin", label: "回收站" },
];
