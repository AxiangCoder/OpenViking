/**
 * 成员数据只读数据层单测（05 §12.6、13 §84.2，14 号计划 §98.7，P4-E2）。
 *
 * - 请求路径/方法/请求体与后端 member_data.py / admin.py / skills.py 契约一致；
 * - 检索请求体走白名单（SearchParams，无 target_uri/score_threshold 等底层字段）；
 * - 成员 Skill 发布成功返回 skill_id/operation_id/status/visibility；
 *   失败码映射 + 可重试判定（10 §58.4）；
 * - API Key 撤销幂等（KEY_NOT_FOUND 视为已撤销成功）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { configurePlatformClient, PlatformError } from "@/lib/platform-client";
import {
  fetchMemberMemoryImpact,
  fetchMemberResource,
  fetchMemberSessionDetail,
  fetchMemberSessionMessages,
  fetchMemberSkill,
  isPublishRetryable,
  listMemberApiKeys,
  listMemberResources,
  listMemberSessions,
  listMemberSkills,
  memberDataErrorMessage,
  memberSearchFind,
  publishMemberSkill,
  revokeMemberApiKey,
} from "@/features/iam/member-data";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

describe("member-data 数据层（05 §12.6，P4-E2）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async () => jsonResponse(200, { status: "ok", result: null }));
    configurePlatformClient({ fetchImpl: fetchMock });
  });

  afterEach(() => {
    configurePlatformClient({ fetchImpl: undefined });
  });

  it("memberSearchFind：POST /admin/users/{id}/search/find，body 仅白名单字段", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(200, { status: "ok", result: { items: [] } }),
    );
    await memberSearchFind("u-1", {
      query: "hello",
      context_type: "resource",
      tags: ["lang=zh"],
      since: "2026-01-01",
      until: null,
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/admin/users/u-1/search/find");
    expect(init.method).toBe("POST");
    const sent = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(sent).toEqual({
      query: "hello",
      context_type: "resource",
      tags: ["lang=zh"],
      since: "2026-01-01",
    });
    expect(sent).not.toHaveProperty("target_uri");
    expect(sent).not.toHaveProperty("score_threshold");
    expect(sent).not.toHaveProperty("limit");
  });

  it("memberSearchFind：无筛选时仅提交 query", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(200, { status: "ok", result: { items: [] } }),
    );
    await memberSearchFind("u-1", { query: "hi" });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ query: "hi" });
  });

  it("memberSearchFind：SEARCH_UNAVAILABLE 映射为可读文案", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(503, { status: "error", error: { code: "SEARCH_UNAVAILABLE" } }),
    );
    await expect(memberSearchFind("u-1", { query: "hi" })).rejects.toMatchObject({
      code: "SEARCH_UNAVAILABLE",
    });
  });

  it("listMemberSessions：GET /admin/users/{id}/sessions（session.read.account，只读）", async () => {
    await listMemberSessions("u-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/platform/v1/admin/users/u-1/sessions",
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
  });

  it("fetchMemberSessionDetail/Messages/MemoryImpact：成员只读子路径", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(200, { status: "ok", result: { items: [] } }));
    await fetchMemberSessionDetail("u-1", "s-1");
    await fetchMemberSessionMessages("u-1", "s-1");
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/platform/v1/admin/users/u-1/sessions/s-1",
      "/api/platform/v1/admin/users/u-1/sessions/s-1/messages",
    ]);
    await fetchMemberMemoryImpact("u-1", "s-1");
    expect(String(fetchMock.mock.calls[2][0])).toBe(
      "/api/platform/v1/admin/users/u-1/sessions/s-1/memory-impact",
    );
  });

  it("listMemberResources/fetchMemberResource：GET 成员私有 Resource 只读", async () => {
    await listMemberResources("u-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/platform/v1/admin/users/u-1/resources",
      expect.objectContaining({ method: "GET" }),
    );
    fetchMock.mockClear();
    await fetchMemberResource("u-1", "res-uuid-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/platform/v1/admin/users/u-1/resources/res-uuid-1",
      expect.any(Object),
    );
  });

  it("listMemberSkills/fetchMemberSkill：GET 成员私有 Skill 只读", async () => {
    await listMemberSkills("u-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/platform/v1/admin/users/u-1/skills",
      expect.objectContaining({ method: "GET" }),
    );
    fetchMock.mockClear();
    await fetchMemberSkill("u-1", "sk-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/platform/v1/admin/users/u-1/skills/sk-1",
      expect.any(Object),
    );
  });

  it("publishMemberSkill：POST /admin/users/{id}/skills/{skillId}/publish（两段式，10 §58.4）", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(200, {
        status: "ok",
        result: {
          skill_id: "sk-1",
          operation_id: "op-9",
          status: "pending",
          visibility: "account_shared",
        },
      }),
    );
    const result = await publishMemberSkill("u-1", "sk-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/platform/v1/admin/users/u-1/skills/sk-1/publish",
      expect.objectContaining({ method: "POST" }),
    );
    expect(result).toEqual({
      skill_id: "sk-1",
      operation_id: "op-9",
      status: "pending",
      visibility: "account_shared",
    });
  });

  it("publishMemberSkill：SKILL_NAME_CONFLICT / SKILL_PUBLISH_FORBIDDEN 映射稳定文案", async () => {
    const cases: Array<[number, string, string]> = [
      [409, "SKILL_NAME_CONFLICT", "该名称在当前 Account 不可用"],
      [403, "SKILL_PUBLISH_FORBIDDEN", "无权发布该 Skill"],
    ];
    for (const [status, code, text] of cases) {
      fetchMock.mockImplementation(async () =>
        jsonResponse(status, { status: "error", error: { code } }),
      );
      await expect(publishMemberSkill("u-1", "sk-1")).rejects.toMatchObject({ code });
      expect(memberDataErrorMessage(new PlatformError({ code, status }), "fallback")).toContain(text);
    }
  });

  it("isPublishRetryable：迁移失败/网络/内部错误可重试，冲突类不可（10 §58.4，AC⑤）", () => {
    expect(isPublishRetryable(new PlatformError({ code: "SKILL_PUBLISH_MIGRATION_FAILED", status: 500 }))).toBe(true);
    expect(isPublishRetryable(new PlatformError({ code: "UNAVAILABLE", status: 0 }))).toBe(true);
    expect(isPublishRetryable(new PlatformError({ code: "INTERNAL", status: 500 }))).toBe(true);
    expect(isPublishRetryable(new PlatformError({ code: "SKILL_NAME_CONFLICT", status: 409 }))).toBe(false);
    expect(isPublishRetryable(new PlatformError({ code: "SKILL_PUBLISH_TARGET_CONFLICT", status: 409 }))).toBe(false);
    expect(isPublishRetryable(new PlatformError({ code: "SKILL_PUBLISH_FORBIDDEN", status: 403 }))).toBe(false);
  });

  it("listMemberApiKeys：GET 元数据（无明文端点）", async () => {
    await listMemberApiKeys("u-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/platform/v1/admin/users/u-1/api-keys",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("revokeMemberApiKey：DELETE 撤销；KEY_NOT_FOUND 幂等成功（AC③）", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(200, { status: "ok", result: { id: "k-1", name: "dev", revoked: true } }),
    );
    await expect(revokeMemberApiKey("u-1", "k-1")).resolves.toEqual({ id: "k-1", name: "dev", revoked: true });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/platform/v1/admin/users/u-1/api-keys/k-1",
      expect.objectContaining({ method: "DELETE" }),
    );

    fetchMock.mockClear();
    fetchMock.mockImplementation(async () =>
      jsonResponse(404, { status: "error", error: { code: "KEY_NOT_FOUND" } }),
    );
    await expect(revokeMemberApiKey("u-1", "k-2")).resolves.toEqual({ id: "k-2", name: "", revoked: true });
  });
});
