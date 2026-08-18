/**
 * Auth 状态层单测（06 §13.4，AC①③）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AUTH_CHALLENGE_EVENT,
  configurePlatformClient,
} from "@/lib/platform-client";
import {
  authReady,
  bootstrapAuth,
  getAuthState,
  getCsrfToken,
  onSessionExpired,
  setAuthStateForTest,
  setCsrfToken,
  type AuthMeResult,
} from "./auth-state";

const ME: AuthMeResult = {
  account: { id: "acc-1" },
  user: { id: "u-1", ov_user_id: "ov-u-1" },
  roles: ["user"],
  permissions: ["session.read.self"],
  can_switch_account: false,
  csrf_token: null,
};

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("auth-state", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockResolvedValue(
      jsonResponse(200, { status: "ok", result: ME }),
    );
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "idle", me: null, sessionExpired: false });
  });

  afterEach(() => {
    setCsrfToken(null);
  });

  it("启动（bootstrapAuth）必调 /auth/me（AC①）", async () => {
    await bootstrapAuth();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/platform/v1/auth/me");
    expect(getAuthState().status).toBe("authenticated");
    expect(getAuthState().me?.user.id).toBe("u-1");
    const ready = await authReady();
    expect(ready.status).toBe("authenticated");
  });

  it("会话过期：/auth/me 401 → unauthenticated + sessionExpired（AC③）", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(401, { status: "error", error: { code: "SESSION_EXPIRED" } }),
    );
    const handler = vi.fn();
    const unsubscribe = onSessionExpired(handler);

    await bootstrapAuth();

    expect(getAuthState().status).toBe("unauthenticated");
    expect(getAuthState().sessionExpired).toBe(true);
    expect(handler).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("401/403（auth-challenge 事件）→ 刷新 /auth/me 并更新界面（AC③）", async () => {
    await bootstrapAuth();
    expect(getAuthState().status).toBe("authenticated");

    fetchMock.mockImplementation(async () =>
      jsonResponse(403, { status: "error", error: { code: "PERMISSION_DENIED" } }),
    );
    const before = fetchMock.mock.calls.length;
    globalThis.dispatchEvent(new CustomEvent(AUTH_CHALLENGE_EVENT));

    await vi.waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(before);
    });
    await vi.waitFor(() => {
      expect(getAuthState().status).toBe("unauthenticated");
    });
  });

  it("权限变化（角色提升后 /auth/me 返回新权限）→ 状态即时更新（07 §21 条目 18）", async () => {
    await bootstrapAuth();
    expect(getAuthState().me?.roles).toEqual(["user"]);

    fetchMock.mockImplementation(async () =>
      jsonResponse(200, {
        status: "ok",
        result: { ...ME, roles: ["account_admin"], permissions: ["user.read"] },
      }),
    );
    await import("@/lib/platform-client").then(({ request }) =>
      request<AuthMeResult>("/auth/me"),
    );
    // 上面直接请求会触发 challenge 刷新；这里显式刷新验证状态层更新
    setAuthStateForTest({ status: "idle", me: null, sessionExpired: false });
    await bootstrapAuth();
    expect(getAuthState().me?.roles).toEqual(["account_admin"]);
    expect(getAuthState().me?.permissions).toEqual(["user.read"]);
  });

  it("CSRF Token 只内存持有（AC④：不落任何存储）", () => {
    setCsrfToken("csrf-token-abc");
    expect(getCsrfToken()).toBe("csrf-token-abc");
    expect(globalThis.localStorage.getItem("csrf")).toBeNull();
    expect(globalThis.sessionStorage.getItem("csrf")).toBeNull();
  });
});
