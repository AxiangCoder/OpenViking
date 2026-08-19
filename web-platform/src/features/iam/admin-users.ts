/**
 * /admin 用户生命周期数据层（05 §12.6，14 号计划 §98.6，P4-E1）。
 *
 * - Account 固定来自登录 Session（后端 principal.actor_account_id），
 *   前端不提交 account_id、不提供 Account 切换入口（AC①，05 §12.6）；
 * - `POST /admin/users` 与 `POST .../password/reset` 成功响应一次性返回
 *   `initial_password` / `new_password`（仅该次响应，05 §12.6），前端只入内存态；
 * - 创建用户携带 Idempotency-Key（05 §12.2：至少覆盖创建用户）；
 * - 稳定错误码映射见 `adminUserErrorMessage`（05 §12.2、13 §85.6，
 *   不依赖英文 message 判断）。
 */

import { PlatformError, request } from "@/lib/platform-client";

export type AdminUserStatus =
  | "provisioning"
  | "active"
  | "disabled"
  | "failed"
  | "pending_deletion";

export interface AdminUserRecord {
  id: string;
  username: string;
  email: string;
  display_name: string | null;
  status: AdminUserStatus;
  role: string | null;
  ov_user_id: string | null;
  created_at: string | null;
  last_login_at: string | null;
}

export interface AdminUserListPage {
  items: AdminUserRecord[];
  next_cursor: string | null;
}

/** 创建成功一次性返回初始密码（05 §12.6：后续查询不可再取）。 */
export interface CreatedAdminUser extends AdminUserRecord {
  initial_password: string;
}

export interface AdminUserStatusResult {
  id: string;
  status: string;
  sessions_revoked: number;
  keys_revoked: number;
}

/** 分级重置成功一次性返回新密码；成功事务同时撤销目标全部登录 Session。 */
export interface AdminPasswordResetResult {
  new_password: string;
  sessions_revoked: number;
}

/** deletion-preview（05 §12.6：目标名称、影响分类与数量、可恢复、purge_after）。 */
export interface AdminUserDeletionPreview {
  resource_type: string;
  resource_id: string;
  target_name: string;
  impacted: Record<string, number>;
  recoverable: boolean;
  purge_after: string | null;
}

/** DELETE 成功进入 30 天回收期并返回 deletion job ID 与恢复截止时间（AC⑨）。 */
export interface AdminUserDeletionResult {
  resource_type: string;
  resource_id: string;
  deletion_job_id: string;
  deleted_at: string;
  restore_until: string;
}

export const ADMIN_USER_STATUSES: AdminUserStatus[] = [
  "provisioning",
  "active",
  "disabled",
  "failed",
  "pending_deletion",
];

export const ADMIN_USER_STATUS_LABELS: Record<AdminUserStatus, string> = {
  provisioning: "开通中",
  active: "正常",
  disabled: "已禁用",
  failed: "开通失败",
  pending_deletion: "删除中",
};

export const ADMIN_ROLE_LABELS: Record<string, string> = {
  platform_super_admin: "Platform Super Admin",
  account_admin: "Account Admin",
  user: "user",
};

export function adminRoleLabel(role: string | null | undefined): string {
  if (!role) return "—";
  return ADMIN_ROLE_LABELS[role] ?? role;
}

export function formatIsoDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export async function listAdminUsers(cursor?: string): Promise<AdminUserListPage> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return request<AdminUserListPage>(`/api/platform/v1/admin/users${query}`);
}

export function createAdminUser(
  input: { email: string; username: string; display_name: string | null },
  idempotencyKey: string,
): Promise<CreatedAdminUser> {
  return request<CreatedAdminUser>("/api/platform/v1/admin/users", {
    method: "POST",
    body: {
      email: input.email,
      username: input.username,
      ...(input.display_name ? { display_name: input.display_name } : {}),
    },
    idempotencyKey,
  });
}

/** 启用：`PATCH /admin/users/{id}`（status=active，`user.update`）。 */
export function enableAdminUser(userId: string): Promise<AdminUserRecord> {
  return request<AdminUserRecord>(`/api/platform/v1/admin/users/${userId}`, {
    method: "PATCH",
    body: { status: "active" },
  });
}

/** 禁用：`POST /admin/users/{id}/disable`（即时杀 Session + 全部 API Key，AC⑤）。 */
export function disableAdminUser(userId: string): Promise<AdminUserStatusResult> {
  return request<AdminUserStatusResult>(`/api/platform/v1/admin/users/${userId}/disable`, {
    method: "POST",
  });
}

/** 分级密码重置：`POST /admin/users/{id}/password/reset`（`user.password.reset.account`）。 */
export function resetAdminUserPassword(userId: string): Promise<AdminPasswordResetResult> {
  return request<AdminPasswordResetResult>(`/api/platform/v1/admin/users/${userId}/password/reset`, {
    method: "POST",
  });
}

export function fetchAdminUserDeletionPreview(userId: string): Promise<AdminUserDeletionPreview> {
  return request<AdminUserDeletionPreview>(`/api/platform/v1/admin/users/${userId}/deletion-preview`);
}

/** 软删除进入 30 天回收期（`DELETE /admin/users/{id}`，`user.delete`）。 */
export function deleteAdminUser(userId: string): Promise<AdminUserDeletionResult> {
  return request<AdminUserDeletionResult>(`/api/platform/v1/admin/users/${userId}`, {
    method: "DELETE",
  });
}

/** 创建用户幂等键（05 §12.2；客户端生成，仅本次提交有效）。 */
export function newCreateUserIdempotencyKey(): string {
  const cryptoObj = globalThis.crypto as Crypto | undefined;
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
    return `create-user-${cryptoObj.randomUUID()}`;
  }
  return `create-user-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
}

/** 13 §85.6 稳定错误码 → 管理页统一文案（05 §12.2；未知码回落 fallback）。 */
export function adminUserErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof PlatformError) {
    switch (error.code) {
      case "EMAIL_ALREADY_EXISTS":
        return "该邮箱已被其他用户使用（邮箱全局唯一）。";
      case "USERNAME_ALREADY_EXISTS":
        return "该 code 在本 Account 内已被使用。";
      case "LAST_ACCOUNT_ADMIN_REQUIRED":
        return "该用户是最后一个 Account Admin，操作被拒绝。";
      case "PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN":
        return "仅可重置严格低等级用户的密码。";
      case "PROVISIONING_PENDING":
        return "该用户仍在开通中，操作被拒绝。";
      case "PROVISIONING_FAILED":
        return "该用户开通失败，操作被拒绝。";
      case "DELETION_PENDING":
        return "该用户已进入删除流程，操作被拒绝。";
      case "PERMISSION_NOT_GRANTED":
      case "PERMISSION_DENIED":
        return "权限不足，操作被拒绝。";
      case "UNAVAILABLE":
        return "网络异常，请检查连接后重试。";
      default:
        break;
    }
  }
  return fallback;
}
