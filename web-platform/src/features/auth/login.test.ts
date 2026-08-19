/**
 * 登录错误文案映射单测（13 §80.5，P3-E2 AC①）。
 */

import { describe, expect, it } from "vitest";
import { PlatformError } from "@/lib/platform-client";
import { loginErrorMessage } from "./login";

function errorOf(code: string, retryAfter: number | null = null): PlatformError {
  return new PlatformError({ code, status: 401, retryAfterSeconds: retryAfter });
}

describe("loginErrorMessage（13 §80.5 状态文案表）", () => {
  it("凭证错误统一文案（LOGIN_FAILED，不区分账号是否存在）", () => {
    const mapped = loginErrorMessage(errorOf("LOGIN_FAILED"));
    expect(mapped.text).toBe("邮箱或密码不正确");
    expect(mapped.cooldownSeconds).toBeNull();
  });

  it("未知错误也统一为「邮箱或密码不正确」（防枚举兜底）", () => {
    expect(loginErrorMessage(new Error("boom")).text).toBe("邮箱或密码不正确");
    expect(loginErrorMessage(errorOf("UNKNOWN")).text).toBe("邮箱或密码不正确");
  });

  it("限流（Retry-After）→ 提示稍后重试并返回冷却秒数", () => {
    const mapped = loginErrorMessage(errorOf("LOGIN_FAILED", 45));
    expect(mapped.text).toBe("尝试过多，请稍后再试");
    expect(mapped.cooldownSeconds).toBe(45);
  });

  it("LOGIN_RATE_LIMITED 无 Retry-After → 默认 60s 冷却", () => {
    const mapped = loginErrorMessage(errorOf("LOGIN_RATE_LIMITED"));
    expect(mapped.text).toBe("尝试过多，请稍后再试");
    expect(mapped.cooldownSeconds).toBe(60);
  });

  it("ACCOUNT_SUSPENDED → 账号已暂停", () => {
    expect(loginErrorMessage(errorOf("ACCOUNT_SUSPENDED")).text).toBe("账号已暂停，请联系管理员");
  });

  it("USER_DISABLED → 账号已停用", () => {
    expect(loginErrorMessage(errorOf("USER_DISABLED")).text).toBe("账号已停用，请联系管理员");
  });

  it("PROVISIONING_PENDING/FAILED → 开通中/开通失败", () => {
    expect(loginErrorMessage(errorOf("PROVISIONING_PENDING")).text).toBe("账号正在开通，请联系管理员");
    expect(loginErrorMessage(errorOf("PROVISIONING_FAILED")).text).toBe("账号开通失败，请联系管理员");
  });

  it("网络错误（UNAVAILABLE）→ 保留输入的可重试文案", () => {
    expect(loginErrorMessage(errorOf("UNAVAILABLE")).text).toBe("网络异常，请检查连接后重试");
  });

  it("UNAVAILABLE 误带 Retry-After 时优先按限流处理（后端不会同时返回）", () => {
    const mapped = loginErrorMessage(errorOf("UNAVAILABLE", 10));
    expect(mapped.cooldownSeconds).toBe(10);
  });
});
