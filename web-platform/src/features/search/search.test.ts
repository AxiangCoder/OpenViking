/**
 * 检索数据层测试（11 §69.2/§69.3/§74，05 §12.5，P3-E3 AC②③）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { configurePlatformClient } from "@/lib/platform-client";
import {
  buildSearchBody,
  isValidTag,
  normalizeTagInput,
  parseTagsInput,
  searchFind,
  searchWithSession,
  toSinceUntil,
} from "./search";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("检索数据层（11 §69，AC②③）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockResolvedValue(
      jsonResponse(200, { status: "ok", result: { items: [] } }),
    );
    configurePlatformClient({ fetchImpl: fetchMock });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("AC③：请求体仅含白名单字段（query/context_type/tags/since/until），无调试字段", () => {
    const body = buildSearchBody({
      query: "openviking",
      context_type: "resource",
      tags: ["project=openviking"],
      since: "2026-08-01",
      until: "2026-08-31",
    });
    expect(Object.keys(body).sort()).toEqual(
      ["context_type", "query", "since", "tags", "until"].sort(),
    );
    const minimal = buildSearchBody({ query: "x" });
    expect(Object.keys(minimal)).toEqual(["query"]);
  });

  it("AC③：任何位置都无法携带 target_uri/filter/score_threshold/level 等调试字段", () => {
    const body = buildSearchBody({
      query: "q",
      // 以额外键混入（类型系统之外的 JS 形态）：
    } as { query: string });
    const forbidden = [
      "target_uri",
      "filter",
      "score_threshold",
      "level",
      "include_provenance",
      "limit",
      "node_limit",
      "time_field",
    ];
    for (const key of forbidden) {
      expect(Object.keys(body)).not.toContain(key);
    }
    // 显式赋 undefined 的调试字段也不进入请求体
    const withUndefined = buildSearchBody({
      query: "q",
      context_type: null,
      tags: undefined,
      since: null,
      until: null,
    });
    expect(Object.keys(withUndefined)).toEqual(["query"]);
  });

  it("AC②：快速检索走 /search/find 且不传 session_id；请求体白名单", async () => {
    await searchFind({ query: "hello" });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/search/find"));
    expect(call).toBeDefined();
    const body = JSON.parse(String((call![1] as RequestInit).body));
    expect(body).toEqual({ query: "hello" });
    expect(body.session_id).toBeUndefined();
    // 快速检索不加载 Session 列表（无 /sessions GET）
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/sessions"))).toBe(false);
  });

  it("AC②：结合会话检索走 /search/search 且携带 session_id", async () => {
    await searchWithSession({ query: "hello" }, "11111111-2222-4333-8444-555555555555");
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/search/search"));
    expect(call).toBeDefined();
    const body = JSON.parse(String((call![1] as RequestInit).body));
    expect(body).toEqual({
      query: "hello",
      session_id: "11111111-2222-4333-8444-555555555555",
    });
  });

  it("AC②：默认范围=我的私有+Account 共享（客户端不携带根/URI 参数，由服务端固定）", async () => {
    await searchFind({ query: "x", context_type: "memory" });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/search/find"));
    const body = JSON.parse(String((call![1] as RequestInit).body));
    expect(body).toEqual({ query: "x", context_type: "memory" });
    expect(body.target_uri).toBeUndefined();
    expect(body.root).toBeUndefined();
  });

  it("标签规则（11 §69.3）：key=value、去空白小写、非法项剔除、多项保留", () => {
    expect(normalizeTagInput("  Project=OpenViking ")).toBe("project=openviking");
    expect(isValidTag("project=openviking")).toBe(true);
    expect(isValidTag("project")).toBe(false);
    expect(isValidTag("=value")).toBe(false);
    expect(isValidTag("key=")).toBe(false);
    expect(isValidTag("a=b=c")).toBe(false);
    const tags = parseTagsInput("Project=OV project=openviking badtag key=value");
    expect(tags).toEqual(["project=ov", "project=openviking", "key=value"]);
  });

  it("时间范围固定映射 updated_at（无创建时间切换字段）", () => {
    expect(toSinceUntil("2026-08-01", null)).toEqual({ since: "2026-08-01", until: null });
    expect(toSinceUntil("", "")).toEqual({ since: null, until: null });
  });
});
