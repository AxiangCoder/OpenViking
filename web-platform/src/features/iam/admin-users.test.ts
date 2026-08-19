/**
 * /admin 用户生命周期数据层单测（05 §12.6，14 号计划 §98.6，P4-E1）。
 *
 * - 请求路径/方法/请求体与后端 admin.py 契约逐项一致（05 §12.6 表）；
 * - 创建携带 Idempotency-Key（05 §12.2）；错误码映射稳定（13 §85.6）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { configurePlatformClient } from "@/lib/platform-client";
import {
  adminUserErrorMessage,
  createAdminUser,
  deleteAdminUser,
  disableAdminUser,
  enableAdminUser,
  fetchAdminUserDeletionPreview,
  formatIsoDateTime,
  listAdminUsers,
  newCreateUserIdempotencyKey,
  resetAdminUserPassword,
} from "@/features/iam/admin-users";
import { PlatformError } from "@/lib/platform-client";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("admin-users 数据层（05 §12.6）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async () => jsonResponse(200, { status: "ok", result: null }));
    configurePlatformClient({ fetchImpl: fetchMock });
  });

  afterEach(() => {
    configurePlatformClient({ fetchImpl: undefined });
  });

  it("listAdminUsers：GET /admin/users（无 Account 参数，Account 固定来自 Session）", async () => {
    await listAdminUsers();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/platform/v1/admin/users",
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
  });

  it("listAdminUsers：cursor 分页（05 §12.2）", async () => {
    await listAdminUsers("c-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/platform/v1/admin/users?cursor=c-1",
      expect.any(Object),
    );
  });

  it("createAdminUser：POST /admin/users，body 仅 email/username/display_name，带 Idempotency-Key", async () => {
    fetchMock.mockImplementation(async (_input: unknown, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      return jsonResponse(200, {
        status: "ok",
        result: {
          id: "u-new",
          username: body.username,
          email: body.email,
          display_name: body.display_name ?? null,
          status: "active",
          role: "user",
          ov_user_id: null,
          created_at: "2026-08-19T00:00:00Z",
          last_login_at: null,
          initial_password: "InitPass123",
        },
      });
    });
    const created = await createAdminUser(
      { email: "new@example.com", username: "newbie", display_name: "New User" },
      "create-user-k-1",
    );
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/admin/users");
    expect(init.method).toBe("POST");
    const sent = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(sent).toEqual({ email: "new@example.com", username: "newbie", display_name: "New User" });
    // AC③：请求体无角色字段，不能创建/提升 account_admin
    expect("role" in sent).toBe(false);
    expect("roles" in sent).toBe(false);
    expect(init.headers).toMatchObject({ "Idempotency-Key": "create-user-k-1" });
    expect(created.initial_password).toBe("InitPass123");
  });

  it("enableAdminUser：PATCH /admin/users/{id}（status=active，user.update）", async () => {
    await enableAdminUser("u-1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/admin/users/u-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({ status: "active" });
  });

  it("disableAdminUser：POST /admin/users/{id}/disable（user.disable，AC⑤）", async () => {
    await disableAdminUser("u-1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/admin/users/u-1/disable");
    expect(init.method).toBe("POST");
  });

  it("resetAdminUserPassword：POST /admin/users/{id}/password/reset", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(200, {
        status: "ok",
        result: { new_password: "NewPass456", sessions_revoked: 1 },
      }),
    );
    const result = await resetAdminUserPassword("u-1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/admin/users/u-1/password/reset");
    expect(init.method).toBe("POST");
    expect(result).toEqual({ new_password: "NewPass456", sessions_revoked: 1 });
  });

  it("fetchAdminUserDeletionPreview：GET /admin/users/{id}/deletion-preview（user.delete）", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(200, {
        status: "ok",
        result: {
          resource_type: "user",
          resource_id: "u-1",
          target_name: "Alice",
          impacted: { login_sessions: 2, api_keys: 3 },
          recoverable: true,
          purge_after: null,
        },
      }),
    );
    const preview = await fetchAdminUserDeletionPreview("u-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/platform/v1/admin/users/u-1/deletion-preview",
      expect.objectContaining({ method: "GET" }),
    );
    expect(preview.impacted).toEqual({ login_sessions: 2, api_keys: 3 });
  });

  it("deleteAdminUser：DELETE /admin/users/{id}（软删除，进入 30 天回收期）", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(200, {
        status: "ok",
        result: {
          resource_type: "user",
          resource_id: "u-1",
          deletion_job_id: "job-1",
          deleted_at: "2026-08-19T00:00:00Z",
          restore_until: "2026-09-18T00:00:00Z",
        },
      }),
    );
    const result = await deleteAdminUser("u-1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/admin/users/u-1");
    expect(init.method).toBe("DELETE");
    expect(result.restore_until).toBe("2026-09-18T00:00:00Z");
  });

  it("newCreateUserIdempotencyKey：每次生成唯一键（05 §12.2）", () => {
    const a = newCreateUserIdempotencyKey();
    const b = newCreateUserIdempotencyKey();
    expect(a).toMatch(/^create-user-/);
    expect(a).not.toBe(b);
  });

  it("formatIsoDateTime：RFC3339 → 本地可读时间；空值占位（05 §12.2）", () => {
    expect(formatIsoDateTime(null)).toBe("—");
    expect(formatIsoDateTime(undefined)).toBe("—");
    const rendered = formatIsoDateTime("2026-08-18T09:00:00Z");
    expect(rendered).toContain("2026");
    expect(Number.isNaN(new Date("2026-08-18T09:00:00Z").getTime())).toBe(false);
  });
});

describe("adminUserErrorMessage（13 §85.6，05 §12.2 稳定错误码）", () => {
  it("映射稳定错误码为管理页统一文案", () => {
    const err = (code: string, status = 409) =>
      new PlatformError({ code, status });
    expect(adminUserErrorMessage(err("EMAIL_ALREADY_EXISTS"), "fallback")).toContain("邮箱");
    expect(adminUserErrorMessage(err("USERNAME_ALREADY_EXISTS"), "fallback")).toContain("code");
    expect(adminUserErrorMessage(err("LAST_ACCOUNT_ADMIN_REQUIRED"), "fallback")).toContain(
      "最后一个 Account Admin",
    );
    expect(
      adminUserErrorMessage(err("PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN", 403), "fallback"),
    ).toContain("严格低等级");
    expect(adminUserErrorMessage(err("PROVISIONING_PENDING"), "fallback")).toContain("开通中");
    expect(adminUserErrorMessage(err("PROVISIONING_FAILED"), "fallback")).toContain("开通失败");
    expect(adminUserErrorMessage(err("UNAVAILABLE", 0), "fallback")).toContain("网络异常");
  });

  it("未知/非 PlatformError 回落 fallback，不泄露底层异常", () => {
    expect(adminUserErrorMessage(new PlatformError({ code: "WEIRD", status: 500 }), "fallback")).toBe(
      "fallback",
    );
    expect(adminUserErrorMessage(new Error("boom"), "fallback")).toBe("fallback");
    expect(adminUserErrorMessage(undefined, "fallback")).toBe("fallback");
  });
});
