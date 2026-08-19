/**
 * 登录 API 与统一错误文案映射（13 §80.3–80.5，P3-E2 AC①）。
 *
 * - `POST /auth/login` 仅 email+password；成功后服务端签发 HttpOnly Cookie
 *   （`__Host-ov_session`）并一次性下发 CSRF Token（05 §12.3，04 §10.7）；
 * - 凭证错误统一 `LOGIN_FAILED`（防枚举，03 §8.3）；限流同样对外 `LOGIN_FAILED`
 *   但附带 Retry-After header → 前端按 header 判定限流并进入按钮冷却（13 §80.5）；
 * - 停用/开通中状态文案按 13 §80.5 逐码映射（ACCOUNT_SUSPENDED / USER_DISABLED /
 *   PROVISIONING_PENDING / PROVISIONING_FAILED）。
 */

import { PlatformError, request } from "@/lib/platform-client";

export interface LoginResult {
  csrf_token: string | null;
}

export interface LoginErrorMessage {
  text: string;
  /** 非空表示进入限流冷却（秒），登录按钮禁用并倒计时。 */
  cooldownSeconds: number | null;
}

export function retryAfterSeconds(error: unknown): number | null {
  if (error instanceof PlatformError && error.retryAfterSeconds != null) {
    return error.retryAfterSeconds;
  }
  return null;
}

export function isRateLimited(error: unknown): boolean {
  return error instanceof PlatformError && (error.code === "LOGIN_RATE_LIMITED" || error.retryAfterSeconds != null);
}

/** 13 §80.5 错误与状态文案表（登录页唯一事实来源）。 */
export function loginErrorMessage(error: unknown): LoginErrorMessage {
  const cooldown = retryAfterSeconds(error);
  if (cooldown != null) {
    return { text: "尝试过多，请稍后再试", cooldownSeconds: cooldown };
  }
  const code = error instanceof PlatformError ? error.code : "UNKNOWN";
  switch (code) {
    case "LOGIN_RATE_LIMITED":
      return { text: "尝试过多，请稍后再试", cooldownSeconds: 60 };
    case "ACCOUNT_SUSPENDED":
      return { text: "账号已暂停，请联系管理员", cooldownSeconds: null };
    case "USER_DISABLED":
      return { text: "账号已停用，请联系管理员", cooldownSeconds: null };
    case "PROVISIONING_PENDING":
      return { text: "账号正在开通，请联系管理员", cooldownSeconds: null };
    case "PROVISIONING_FAILED":
      return { text: "账号开通失败，请联系管理员", cooldownSeconds: null };
    case "UNAVAILABLE":
      return { text: "网络异常，请检查连接后重试", cooldownSeconds: null };
    default:
      // LOGIN_FAILED 与未知错误统一「邮箱或密码不正确」（80.5：不区分账号是否存在）
      return { text: "邮箱或密码不正确", cooldownSeconds: null };
  }
}

export async function login(email: string, password: string): Promise<LoginResult> {
  return request<LoginResult>("/api/platform/v1/auth/login", {
    method: "POST",
    body: { email, password },
  });
}
