/**
 * lib/permissions.ts 单测：权限判定、角色判定、按钮/共享管理门控（AC②）。
 */

import { describe, expect, it } from "vitest";
import {
  canManageSharedContent,
  canPerform,
  hasPermission,
  isAccountAdmin,
  isPlatformSuperAdmin,
  PERMISSION_CODES,
} from "@/lib/permissions";
import type { AuthMeResult } from "@/features/auth/auth-state";

const userMe: AuthMeResult = {
  account: { id: "acc-1" },
  user: { id: "u-1", ov_user_id: "ov-u-1" },
  roles: ["user"],
  permissions: ["resource.user_private.read.self", "resource.account_shared.read.account"],
  can_switch_account: false,
  csrf_token: null,
};

const adminMe: AuthMeResult = {
  ...userMe,
  roles: ["account_admin"],
  permissions: [
    ...userMe.permissions,
    "resource.account_shared.write.account",
    "skill.account_shared.manage.account",
  ],
};

describe("permissions", () => {
  it("目录与后端 iam/permissions.py 对齐（70 码冻结清单）", () => {
    expect(Object.keys(PERMISSION_CODES)).toHaveLength(70);
    expect(PERMISSION_CODES["resource.user_private.read.self"]).toBeTruthy();
    expect(PERMISSION_CODES["integration.oauth.authorize.self"]).toBeTruthy();
  });

  it("hasPermission：无权限列表 / 缺失 code 均返回 false", () => {
    expect(hasPermission(["a.b.c"], "a.b.c")).toBe(true);
    expect(hasPermission(undefined, "a.b.c")).toBe(false);
    expect(hasPermission(["a.b.c"], "a.b.d")).toBe(false);
  });

  it("角色判定", () => {
    expect(isAccountAdmin(["account_admin"])).toBe(true);
    expect(isAccountAdmin(["user"])).toBe(false);
    expect(isPlatformSuperAdmin(["platform_super_admin"])).toBe(true);
    expect(isPlatformSuperAdmin(["account_admin"])).toBe(false);
  });

  it("canPerform：按钮级门控（无 me 即 false，AC②）", () => {
    expect(canPerform(adminMe, "resource.account_shared.write.account")).toBe(true);
    expect(canPerform(userMe, "resource.account_shared.write.account")).toBe(false);
    expect(canPerform(null, "resource.account_shared.write.account")).toBe(false);
  });

  it("共享区管理能力：普通 User 无管理入口（06 §13.3）", () => {
    expect(canManageSharedContent(adminMe)).toBe(true);
    expect(canManageSharedContent(userMe)).toBe(false);
    expect(canManageSharedContent(null)).toBe(false);
  });
});
