/**
 * Resource 产品数据层单测（09 §46，05 §12.5，14 号计划 §98.4）。
 *
 * - 路径/方法/请求体与后端 resources.py 契约逐项一致（09 §46.2/§46.3）；
 * - Scope 由函数入参固定（/me 私有、/account 共享），请求不提交
 *   visibility/URI（AC①：入口决定归属，AC⑧：URL 无内部标识符）；
 * - URL 路径参数为裸 UUID（`res_` DTO 前缀被剥离，AC⑧）；
 * - 上传使用 multipart FormData；写请求携带 Idempotency-Key。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { configurePlatformClient } from "@/lib/platform-client";
import {
  cancelResourceOperation,
  configureWatch,
  deleteResource,
  deleteWatch,
  fetchCapabilities,
  fetchDeletionPreview,
  fetchResourceDetail,
  fetchWatchConfig,
  importResources,
  listRecycleBin,
  listResourceNodes,
  listResourceOperations,
  listResources,
  pauseWatch,
  patchResource,
  publishResource,
  readResourceNode,
  refreshResource,
  replaceResource,
  resourceNodeDownloadUrl,
  restoreResource,
  resumeWatch,
  retryResource,
  searchResourceNodes,
  toUrlResourceId,
  triggerWatch,
  uploadResourceFile,
} from "@/features/resources";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("resources 数据层（09 §46）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async () => jsonResponse(200, { status: "ok", result: null }));
    configurePlatformClient({ fetchImpl: fetchMock, csrfTokenProvider: () => "csrf-test" });
  });

  afterEach(() => {
    configurePlatformClient({ fetchImpl: undefined, csrfTokenProvider: undefined });
  });

  it("toUrlResourceId：`res_` 前缀剥离，URL 只接受裸 UUID（AC⑧）", () => {
    expect(toUrlResourceId("res_00000000-0000-0000-0000-000000000001")).toBe(
      "00000000-0000-0000-0000-000000000001",
    );
    expect(toUrlResourceId("00000000-0000-0000-0000-000000000002")).toBe(
      "00000000-0000-0000-0000-000000000002",
    );
  });

  it("listResources private：GET /me/resources，无 visibility/URI 参数（AC①⑧）", async () => {
    await listResources("private");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/me/resources?limit=50");
    expect(init.method).toBe("GET");
  });

  it("listResources shared：GET /account/resources（09 §46.3 读对普通 User 开放）", async () => {
    await listResources("shared", { sourceType: "web", status: "active", cursor: "c-1" });
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "/api/platform/v1/account/resources?source_type=web&status=active&limit=50&cursor=c-1",
    );
  });

  it("fetchCapabilities：GET /resources/capabilities（AC②：限制取自服务端）", async () => {
    await fetchCapabilities();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/resources/capabilities");
    expect(init.method).toBe("GET");
  });

  it("uploadResourceFile：multipart FormData，路径 /me/resource-uploads（09 §46.1）", async () => {
    const file = new File(["content"], "a.pdf", { type: "application/pdf" });
    await uploadResourceFile("private", file);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/me/resource-uploads");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    const form = init.body as FormData;
    expect(form.get("file")).toBeInstanceOf(File);
    const headers = init.headers as Record<string, string>;
    // multipart：不设置 JSON Content-Type（浏览器自动带 boundary）
    expect(headers["Content-Type"]).toBeUndefined();
    expect(headers["X-CSRF-Token"]).toBeDefined();
  });

  it("uploadResourceFile shared：/account/resource-uploads（共享入口固定归属，AC①）", async () => {
    const file = new File(["c"], "a.md", { type: "text/markdown" });
    await uploadResourceFile("shared", file);
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe(
      "/api/platform/v1/account/resource-uploads",
    );
  });

  it("importResources：POST imports，body 仅白名单字段、无 visibility（AC①）", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(200, {
        status: "ok",
        result: { batch_id: "b-1", items: [{ resource_id: "u-1", operation_id: "o-1", error: null }] },
      }),
    );
    await importResources(
      "private",
      [
        { uploadId: "up-1", name: "文档", tags: ["type=doc"] },
        { sourceUrl: "https://example.com/doc", isGit: false },
      ],
      "import-k-1",
    );
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/me/resources/imports");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe("import-k-1");
    const sent = JSON.parse(String(init.body)) as {
      items: Record<string, unknown>[];
    };
    expect(sent.items[0]).toEqual({ upload_id: "up-1", name: "文档", tags: ["type=doc"] });
    expect(sent.items[1]).toEqual({ source_url: "https://example.com/doc" });
    for (const item of sent.items) {
      expect("visibility" in item).toBe(false);
      expect("uri" in item).toBe(false);
      expect("processing_mode" in item).toBe(false);
    }
  });

  it("fetchResourceDetail：GET /me/resources/{uuid}，res_ 前缀剥离", async () => {
    await fetchResourceDetail("private", "res_00000000-0000-0000-0000-000000000001");
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/me/resources/00000000-0000-0000-0000-000000000001");
  });

  it("patchResource：PATCH 携带请求体 version 乐观锁（09 §42.4，AC④）", async () => {
    await patchResource("shared", "res_u-1", {
      displayName: "新名称",
      description: "desc",
      tags: ["type=doc"],
      version: 3,
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/account/resources/u-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({
      display_name: "新名称",
      description: "desc",
      tags: ["type=doc"],
      version: 3,
    });
  });

  it("replaceResource：POST /replace（09 §43.1）", async () => {
    await replaceResource("private", "res_u-2", "up-9");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/me/resources/u-2/replace");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ upload_id: "up-9" });
  });

  it("refreshResource：POST /refresh（09 §43.1）", async () => {
    await refreshResource("private", "res_u-2");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/me/resources/u-2/refresh");
    expect(init.method).toBe("POST");
  });

  it("retryResource：POST /retry（09 §46.2：upload_id 或 source_url 二选一）", async () => {
    await retryResource("private", "res_u-3", { sourceUrl: "https://example.com/x" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/me/resources/u-3/retry");
    expect(JSON.parse(String(init.body))).toEqual({ source_url: "https://example.com/x" });
  });

  it("publishResource：POST /me/resources/{id}/publish + Idempotency-Key（09 §44.1，AC⑦）", async () => {
    await publishResource("res_u-4", "publish-k-1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/me/resources/u-4/publish");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe("publish-k-1");
  });

  it("watch 组：GET/PUT/POST pause|resume|trigger/DELETE（09 §43.2，AC⑤）", async () => {
    await fetchWatchConfig("private", "res_u-5");
    expect((fetchMock.mock.calls[0] as [string, RequestInit])[0]).toBe(
      "/api/platform/v1/me/resources/u-5/watch",
    );
    await configureWatch("private", "res_u-5", 360);
    let [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/me/resources/u-5/watch");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ interval_minutes: 360 });
    await pauseWatch("private", "res_u-5");
    [url, init] = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/me/resources/u-5/watch/pause");
    expect(init.method).toBe("POST");
    await resumeWatch("private", "res_u-5");
    [url, init] = fetchMock.mock.calls[3] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/me/resources/u-5/watch/resume");
    await triggerWatch("shared", "res_u-5");
    [url, init] = fetchMock.mock.calls[4] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/account/resources/u-5/watch/trigger");
    await deleteWatch("private", "res_u-5");
    [url, init] = fetchMock.mock.calls[5] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/me/resources/u-5/watch");
    expect(init.method).toBe("DELETE");
  });

  it("operations：GET /operations；cancel：POST /operations/{id}/cancel（09 §43.3）", async () => {
    await listResourceOperations("private", "res_u-6");
    expect((fetchMock.mock.calls[0] as [string, RequestInit])[0]).toBe(
      "/api/platform/v1/me/resources/u-6/operations",
    );
    await cancelResourceOperation("shared", "res_u-6", "op-1");
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/account/resources/u-6/operations/op-1/cancel");
    expect(init.method).toBe("POST");
  });

  it("nodes：list/read/download URL（09 §42.3，AC③：node_id 不透明、根固定）", async () => {
    await listResourceNodes("private", "res_u-7", "node-abc");
    expect((fetchMock.mock.calls[0] as [string, RequestInit])[0]).toBe(
      "/api/platform/v1/me/resources/u-7/nodes?node_id=node-abc",
    );
    await readResourceNode("private", "res_u-7", "node-abc");
    expect((fetchMock.mock.calls[1] as [string, RequestInit])[0]).toBe(
      "/api/platform/v1/me/resources/u-7/nodes/node-abc",
    );
    expect(resourceNodeDownloadUrl("private", "res_u-7", "node-abc")).toBe(
      "/api/platform/v1/me/resources/u-7/nodes/node-abc/download",
    );
  });

  it("searchResourceNodes：POST /search 仅 query/limit（AC⑧：不透传调试参数）", async () => {
    await searchResourceNodes("private", "res_u-8", "关键词");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/me/resources/u-8/search");
    expect(JSON.parse(String(init.body))).toEqual({ query: "关键词", limit: 10 });
  });

  it("删除：GET deletion-preview；DELETE 携带版本乐观锁（09 §45.1/§45.4，AC⑥）", async () => {
    await fetchDeletionPreview("private", "res_u-9");
    expect((fetchMock.mock.calls[0] as [string, RequestInit])[0]).toBe(
      "/api/platform/v1/me/resources/u-9/deletion-preview",
    );
    await deleteResource("shared", "res_u-9", 5);
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/account/resources/u-9");
    expect(init.method).toBe("DELETE");
    expect((init.headers as Record<string, string>)["If-Match"]).toBe("5");
    expect(init.body).toBeUndefined();
  });

  it("recycle-bin：GET 列表 + POST restore（05 §12.5，09 §45.3，AC⑥）", async () => {
    await listRecycleBin();
    expect((fetchMock.mock.calls[0] as [string, RequestInit])[0]).toBe(
      "/api/platform/v1/recycle-bin",
    );
    await restoreResource("job-1");
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("/api/platform/v1/recycle-bin/job-1/restore");
    expect(init.method).toBe("POST");
  });
});
