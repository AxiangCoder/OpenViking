/**
 * platform-client 单元测试：envelope 解析、稳定错误码、Request ID、CSRF、401/403。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AUTH_CHALLENGE_EVENT,
  configurePlatformClient,
  isPlatformError,
  request,
} from "@/lib/platform-client";
import { PlatformError } from "@/lib/platform-client/errors";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

describe("platform-client", () => {
  beforeEach(() => {
    configurePlatformClient({
      fetchImpl: (() => {
        const fetchMock = vi.fn<typeof fetch>();
        fetchMock.mockResolvedValue(jsonResponse(200, { status: "ok", result: null }));
        return fetchMock;
      })(),
    });
  });

  afterEach(() => {
    configurePlatformClient({ csrfTokenProvider: () => null });
  });

  it("成功响应解析 envelope 并返回 result", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        status: "ok",
        result: { account: { id: "a1" }, roles: ["user"] },
      }),
    );
    configurePlatformClient({ fetchImpl: fetchMock });

    const result = await request<{ roles: string[] }>("/auth/me");
    expect(result.roles).toEqual(["user"]);
  });

  it("每个请求都携带 X-Request-ID（客户端生成）", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(jsonResponse(200, { status: "ok", result: {} }));
    configurePlatformClient({ fetchImpl: fetchMock });

    await request("/auth/me");
    const init = fetchMock.mock.calls[0]![1]! as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Request-ID"]).toMatch(/^[A-Za-z0-9._:-]{1,128}$/);
  });

  it("写请求携带内存态 CSRF Token，读请求不携带", async () => {
    let csrf = "csrf-abc";
    configurePlatformClient({ csrfTokenProvider: () => csrf });
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockImplementation(async () =>
      jsonResponse(200, { status: "ok", result: {} }),
    );
    configurePlatformClient({ fetchImpl: fetchMock });

    await request("/auth/logout", { method: "POST" });
    await request("/auth/me");

    const writeHeaders = fetchMock.mock.calls[0]![1]!.headers as Record<string, string>;
    const readHeaders = fetchMock.mock.calls[1]![1]!.headers as Record<string, string>;
    expect(writeHeaders["X-CSRF-Token"]).toBe("csrf-abc");
    expect(readHeaders["X-CSRF-Token"]).toBeUndefined();
  });

  it("写请求可选携带 Idempotency-Key（05 §12.2）", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(jsonResponse(200, { status: "ok", result: {} }));
    configurePlatformClient({ fetchImpl: fetchMock });

    await request("/admin/users", { method: "POST", body: {}, idempotencyKey: "k-1" });
    const headers = fetchMock.mock.calls[0]![1]!.headers as Record<string, string>;
    expect(headers["Idempotency-Key"]).toBe("k-1");
  });

  it("envelope 错误 → PlatformError：code/status/requestId（header 回显）", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(
      jsonResponse(
        403,
        { status: "error", error: { code: "PERMISSION_NOT_GRANTED", message: "无权限" } },
        { "X-Request-ID": "req-123" },
      ),
    );
    configurePlatformClient({ fetchImpl: fetchMock });

    await expect(request("/admin/roles")).rejects.toMatchObject({
      code: "PERMISSION_NOT_GRANTED",
      status: 403,
      requestId: "req-123",
    });
  });

  it("FastAPI 默认 detail.code 形态（未挂载 handler）也归一化", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(
      jsonResponse(401, { detail: { code: "SESSION_EXPIRED" } }),
    );
    configurePlatformClient({ fetchImpl: fetchMock });

    try {
      await request("/auth/me");
      expect.unreachable();
    } catch (error) {
      expect(isPlatformError(error)).toBe(true);
      expect((error as PlatformError).code).toBe("SESSION_EXPIRED");
      expect((error as PlatformError).status).toBe(401);
    }
  });

  it("限流 401 附带 Retry-After → PlatformError.retryAfterSeconds（03 §8.3，登录页冷却用）", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(
      jsonResponse(
        401,
        { status: "error", error: { code: "LOGIN_FAILED" } },
        { "Retry-After": "30" },
      ),
    );
    configurePlatformClient({ fetchImpl: fetchMock });

    try {
      await request("/auth/login", { method: "POST", body: { email: "a@b.c", password: "x" } });
      expect.unreachable();
    } catch (error) {
      expect(isPlatformError(error)).toBe(true);
      expect((error as PlatformError).code).toBe("LOGIN_FAILED");
      expect((error as PlatformError).retryAfterSeconds).toBe(30);
    }
  });

  it("无 Retry-After 的普通 401 → retryAfterSeconds 为 null", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(
      jsonResponse(401, { status: "error", error: { code: "LOGIN_FAILED" } }),
    );
    configurePlatformClient({ fetchImpl: fetchMock });

    try {
      await request("/auth/login", { method: "POST", body: { email: "a@b.c", password: "x" } });
      expect.unreachable();
    } catch (error) {
      expect((error as PlatformError).retryAfterSeconds).toBeNull();
    }
  });

  it("401/403 触发全局 auth-challenge 事件（AC③）", async () => {
    const listener = vi.fn();
    globalThis.addEventListener(AUTH_CHALLENGE_EVENT, listener);
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(
      jsonResponse(401, { status: "error", error: { code: "SESSION_EXPIRED" } }),
    );
    configurePlatformClient({ fetchImpl: fetchMock });

    await expect(request("/auth/me")).rejects.toBeInstanceOf(PlatformError);
    expect(listener).toHaveBeenCalledTimes(1);
    globalThis.removeEventListener(AUTH_CHALLENGE_EVENT, listener);
  });

  it("网络失败 → UNAVAILABLE（status=0），不抛原始异常", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    configurePlatformClient({ fetchImpl: fetchMock });

    try {
      await request("/auth/me");
      expect.unreachable();
    } catch (error) {
      expect(isPlatformError(error)).toBe(true);
      expect((error as PlatformError).code).toBe("UNAVAILABLE");
      expect((error as PlatformError).status).toBe(0);
    }
  });

  it("非 JSON 响应（如反代 HTML 错误页）→ UNKNOWN", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(
      new Response("<html>502 Bad Gateway</html>", { status: 502, headers: { "content-type": "text/html" } }),
    );
    configurePlatformClient({ fetchImpl: fetchMock });

    try {
      await request("/auth/me");
      expect.unreachable();
    } catch (error) {
      expect((error as PlatformError).code).toBe("INTERNAL");
      expect((error as PlatformError).status).toBe(502);
    }
  });
});
