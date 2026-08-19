/**
 * /platform 平台数据层测试（05 §12.6 平台表、13 §89–§90，P4-E4）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { configurePlatformClient, PlatformError } from "@/lib/platform-client";
import {
  fetchPlatformAccountSkill,
  filterAuditEventsByAccount,
  isPlatformResetTarget,
  listPlatformAccountResources,
  listPlatformAccountSkills,
  listPlatformMemberApiKeys,
  listPlatformMemberSessions,
  platformErrorMessage,
  platformSearchFind,
  revokePlatformMemberApiKey,
} from "@/features/iam/platform";
import type { AuditEvent } from "@/features/iam/admin-governance";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("平台数据层（05 §12.6 平台表）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async () => jsonResponse(200, { status: "ok", result: null }));
    configurePlatformClient({ fetchImpl: fetchMock });
  });

  afterEach(() => {
    configurePlatformClient({ fetchImpl: undefined as never, csrfTokenProvider: undefined as never });
    vi.restoreAllMocks();
  });

  it("平台资源列表路径与查询参数（source_type/status/cursor/limit）", async () => {
    await listPlatformAccountResources("acc-1", {
      sourceType: "web",
      status: "failed",
      cursor: "c1",
      limit: 20,
    });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe(
      "/api/platform/v1/platform/accounts/acc-1/resources?source_type=web&status=failed&limit=20&cursor=c1",
    );
  });

  it("平台成员检索/会话/Key/Skill 路径固定平台前缀", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/search/find")) {
        return jsonResponse(200, { status: "ok", result: { items: [] } });
      }
      return jsonResponse(200, { status: "ok", result: { items: [], next_cursor: null } });
    });
    await platformSearchFind("acc-1", "u-1", { query: "hello" });
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/platform/v1/platform/accounts/acc-1/users/u-1/search/find",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ query: "hello" });

    await listPlatformMemberSessions("acc-1", "u-1");
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/platform/v1/platform/accounts/acc-1/users/u-1/sessions",
    );

    await listPlatformMemberApiKeys("acc-1", "u-1");
    expect(fetchMock.mock.calls[2][0]).toBe(
      "/api/platform/v1/platform/accounts/acc-1/users/u-1/api-keys",
    );

    await listPlatformAccountSkills("acc-1");
    expect(fetchMock.mock.calls[3][0]).toBe("/api/platform/v1/platform/accounts/acc-1/skills");

    await fetchPlatformAccountSkill("acc-1", "skill-1");
    expect(fetchMock.mock.calls[4][0]).toBe("/api/platform/v1/platform/accounts/acc-1/skills/skill-1");
  });

  it("撤销 API Key 幂等：KEY_NOT_FOUND 视为已撤销成功（05 §12.6）", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(404, { status: "error", error: { code: "KEY_NOT_FOUND", message: "not found" } }),
    );
    const result = await revokePlatformMemberApiKey("acc-1", "u-1", "cred-1");
    expect(result.revoked).toBe(true);
  });

  it("审计按目标 Account 筛选：匹配 Subject Account 或 Actor Account（13 §90，AC⑧）", () => {
    const events = [
      { id: "e1", subject_account_id: "acc-1", actor_account_id: "acc-0" },
      { id: "e2", subject_account_id: "acc-2", actor_account_id: "acc-1" },
      { id: "e3", subject_account_id: "acc-2", actor_account_id: "acc-2" },
    ] as unknown as AuditEvent[];
    const filtered = filterAuditEventsByAccount(events, "acc-1");
    expect(filtered.map((e) => e.id)).toEqual(["e1", "e2"]);
    expect(filterAuditEventsByAccount(events, "")).toHaveLength(3);
    expect(filterAuditEventsByAccount(events, "acc-9")).toHaveLength(0);
  });

  it("PSA 不可被重置：isPlatformResetTarget 对 platform_super_admin 返回 false（AC⑥）", () => {
    expect(isPlatformResetTarget("platform_super_admin")).toBe(false);
    expect(isPlatformResetTarget("account_admin")).toBe(true);
    expect(isPlatformResetTarget("user")).toBe(true);
    expect(isPlatformResetTarget(null)).toBe(false);
    expect(isPlatformResetTarget(undefined)).toBe(false);
  });

  it("稳定错误码 → 文案（ACCOUNT_CODE_ALREADY_EXISTS / PROVISIONING_NOT_RETRYABLE 等）", () => {
    const cases: [string, string][] = [
      ["ACCOUNT_CODE_ALREADY_EXISTS", "Account code 已被使用"],
      ["EMAIL_ALREADY_EXISTS", "邮箱已被其他用户使用"],
      ["PROVISIONING_NOT_RETRYABLE", "当前状态不可重试开通"],
      ["PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN", "禁止重置 Platform Super Admin"],
      ["NOT_FOUND", "对象不存在"],
      ["PERMISSION_DENIED", "权限不足"],
      ["UNAVAILABLE", "网络异常"],
    ];
    for (const [code, expected] of cases) {
      const error = new PlatformError({ code, status: 409 });
      expect(platformErrorMessage(error, "fallback")).toContain(expected);
    }
    expect(platformErrorMessage(new Error("x"), "fallback-text")).toBe("fallback-text");
  });
});
