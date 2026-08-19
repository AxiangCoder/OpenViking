/**
 * 个人设置数据层（13 §81，P3-E2 AC⑧⑨）。
 *
 * - `auth/me/session-summary`：当前登录 Session 脱敏摘要（最后活动时间、IP hash、
 *   User-Agent 截断；无完整 IP、无单会话列表，05 §12.3 / 13 §81.4）；
 * - `auth/password/change`：必填旧密码；成功后 Session 轮换并同步下发新 Set-Cookie
 *   与一次性 CSRF Token（05 §12.3 回填发现）→ 前端必须更新内存 CSRF（04 §10.7）；
 * - `auth/logout-all`：撤销当前用户全部登录 Session，不影响 API Key 与 OAuth Grant。
 */

import { request } from "@/lib/platform-client";
import { setCsrfToken } from "@/features/auth/auth-state";
import type { AuthMeResult } from "@/features/auth/auth-state";

export interface SessionSummary {
  session_id: string;
  last_seen_at: string;
  created_at: string;
  ip_hash: string | null;
  user_agent: string | null;
}

export async function fetchSessionSummary(): Promise<SessionSummary> {
  return request<SessionSummary>("/api/platform/v1/auth/me/session-summary");
}

export interface PasswordChangeResult {
  csrf_token: string | null;
  session_rotated: boolean;
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<PasswordChangeResult> {
  const result = await request<PasswordChangeResult>("/api/platform/v1/auth/password/change", {
    method: "POST",
    body: { old_password: oldPassword, new_password: newPassword },
  });
  // Session 已轮换：新 CSRF 只在此响应一次性下发，立即替换内存态（04 §10.7）
  if (result.csrf_token) setCsrfToken(result.csrf_token);
  return result;
}

export async function logoutAll(): Promise<number> {
  const result = await request<{ sessions_revoked: number }>("/api/platform/v1/auth/logout-all", {
    method: "POST",
  });
  return result.sessions_revoked;
}

// ── 基本信息（只读展示，13 §81.2）──

export interface ProfileIdentity {
  displayName: string;
  email: string;
  accountLabel: string;
  /** 字段来自后端 /auth/me 是否齐全（P2 联调前用脱敏占位）。 */
  partial: boolean;
}

export function identityFromMe(me: AuthMeResult | null): ProfileIdentity | null {
  if (me == null) return null;
  const displayName = me.user.display_name ?? me.user.email ?? "";
  const email = me.user.email ?? "";
  const accountName = me.account?.name ?? me.account?.code ?? "";
  const accountLabel = accountName || (me.account?.id ? `${me.account.id.slice(0, 8)}…` : "—");
  return {
    displayName: displayName || (me.user.id ? `${me.user.id.slice(0, 8)}…` : "—"),
    email: email || "—",
    accountLabel,
    partial: !displayName || !email,
  };
}
