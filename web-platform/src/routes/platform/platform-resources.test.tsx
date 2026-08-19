/**
 * /platform/accounts/{accountId}/resources 平台代管共享 Resource 管理页集成测试
 * （09 §38.2/§40.1、05 §12.6 平台表，P4-E4 AC②⑨；Skill 始终只读）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, type AuthMeResult } from "@/features/auth/auth-state";

const psaMe: AuthMeResult = {
  account: null,
  user: { id: "u-psa", ov_user_id: "ov-u-psa", display_name: "平台管理员", email: "psa@example.com" },
  roles: ["platform_super_admin"],
  permissions: [
    "account.read.platform",
    "resource.account_shared.read.platform",
    "resource.account_shared.write.platform",
    "resource.account_shared.delete.platform",
    "skill.account_shared.read.platform",
    "task.read.platform",
  ],
  can_switch_account: false,
  csrf_token: null,
};

const ACCT = {
  id: "acc-1",
  code: "acme",
  name: "Acme Corp",
  status: "active",
  ov_account_id: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const RES_ID = "3f3f3f3f-3f3f-3f3f-3f3f-3f3f3f3f3f3f";

const RES_ROW = {
  id: `res_${RES_ID}`,
  name: "Shared Doc",
  description: "共享文档",
  tags: ["type=doc"],
  visibility: "account_shared",
  source_type: "web",
  lifecycle_status: "active",
  version: 3,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-05T00:00:00Z",
  source_display: "example.com",
  watch: { state: "paused", interval_minutes: null },
  processing: { status: "active", stage: null, last_succeeded_at: null, last_error: null },
};

const RES_DETAIL = {
  ...RES_ROW,
  overview: "概览正文",
  content: { node_count: 5, size_bytes: 2048 },
  watch: { state: "active", interval_minutes: 30 },
};

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function renderRouterAt(path: string) {
  const router = createAppRouter({ history: createMemoryHistory({ initialEntries: [path] }) });
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("/platform/accounts/{id}/resources（09 §38.2/§40.1，AC②⑨）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: psaMe });
      }
      if (method === "GET" && url === "/api/platform/v1/platform/accounts") {
        return jsonResponse(200, { status: "ok", result: { items: [ACCT], next_cursor: null } });
      }
      if (method === "GET" && url === "/api/platform/v1/resources/capabilities") {
        return jsonResponse(200, {
          status: "ok",
          result: { upload: { max_files_per_batch: 5, max_file_size_bytes: 10 * 1024 * 1024 } },
        });
      }
      if (
        method === "GET" &&
        url === `/api/platform/v1/platform/accounts/acc-1/resources/${RES_ID}/nodes`
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [{ node_id: "n1", name: "a.txt", path: "a.txt", type: "file", size_bytes: 100, mime_type: "text/plain" }], next_cursor: null },
        });
      }
      if (
        method === "GET" &&
        url === `/api/platform/v1/platform/accounts/acc-1/resources/${RES_ID}/operations`
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: "op-1",
                operation_type: "resource_refresh",
                status: "succeeded",
                stage: null,
                initiated_by: "user",
                created_at: "2026-08-05T00:00:00Z",
                completed_at: "2026-08-05T00:01:00Z",
                cancellable: false,
                error: null,
                generation: 1,
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (
        method === "GET" &&
        url === `/api/platform/v1/platform/accounts/acc-1/resources/${RES_ID}`
      ) {
        return jsonResponse(200, { status: "ok", result: RES_DETAIL });
      }
      if (
        method === "GET" &&
        (url === "/api/platform/v1/platform/accounts/acc-1/resources" ||
          url.startsWith("/api/platform/v1/platform/accounts/acc-1/resources?"))
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [RES_ROW], next_cursor: null },
        });
      }
      if (
        method === "POST" &&
        url === "/api/platform/v1/platform/accounts/acc-1/resource-uploads"
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: { upload_id: "up-1", filename: "doc.md", mime_type: "text/markdown", size_bytes: 5, expires_at: "2026-08-19T01:00:00Z" },
        });
      }
      if (
        method === "PATCH" &&
        url === `/api/platform/v1/platform/accounts/acc-1/resources/${RES_ID}`
      ) {
        return jsonResponse(200, { status: "ok", result: { ...RES_ROW, name: "Renamed Doc" } });
      }
      if (
        method === "POST" &&
        url === `/api/platform/v1/platform/accounts/acc-1/resources/${RES_ID}/refresh`
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: { resource_id: RES_ID, operation_id: "op-refresh", status: "running" },
        });
      }
      if (
        method === "DELETE" &&
        url === `/api/platform/v1/platform/accounts/acc-1/resources/${RES_ID}`
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            resource_type: "resource",
            resource_id: RES_ID,
            deletion_job_id: "job-res",
            deleted_at: "2026-08-19T00:00:00Z",
            restore_until: "2026-09-18T00:00:00Z",
          },
        });
      }
      if (
        method === "POST" &&
        url === "/api/platform/v1/platform/accounts/acc-1/resources/imports"
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: { batch_id: "batch-1", items: [{ index: 0, resource_id: "r-1", error: null }] },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: psaMe, sessionExpired: false });
  });

  afterEach(() => {
    cleanup();
    setAuthStateForTest({ status: "idle", me: null, sessionExpired: false });
    configurePlatformClient({ fetchImpl: undefined as never });
  });

  it("列表渲染：目标 Account 上下文、行查看/删除、导入入口（AC②⑨）", async () => {
    renderRouterAt("/platform/accounts/acc-1/resources");
    const row = await screen.findByTestId(`platform-resource-row-res_${RES_ID}`);
    expect(row).toHaveTextContent("Shared Doc");
    expect(screen.getByTestId("platform-resources-account-context")).toHaveTextContent("Acme Corp");
    expect(screen.getByTestId("platform-resources-account-context")).toHaveTextContent("不改变登录者身份");
    expect(within(row).getByText("查看")).toBeInTheDocument();
    expect(within(row).getByTestId(`platform-resource-delete-res_${RES_ID}`)).toBeInTheDocument();
    expect(screen.getByTestId("platform-resource-import-open")).toHaveTextContent("新增 Resource");
  });

  it("删除：确认弹窗静态影响说明 + 删除进入 30 天回收期（AC⑨）", async () => {
    renderRouterAt("/platform/accounts/acc-1/resources");
    const row = await screen.findByTestId(`platform-resource-row-res_${RES_ID}`);
    fireEvent.click(within(row).getByTestId(`platform-resource-delete-res_${RES_ID}`));

    const impact = screen.getByTestId("platform-resource-delete-impact");
    expect(impact).toHaveTextContent("共享 Resource（平台代管）");
    expect(impact).toHaveTextContent("30 天回收期");
    fireEvent.click(screen.getByTestId("platform-resource-delete-confirm"));

    await screen.findByTestId("platform-resources-notice");
    expect(screen.getByTestId("platform-resources-notice")).toHaveTextContent(
      "已删除「Shared Doc」：进入 30 天回收期",
    );
    const deleteCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url) === `/api/platform/v1/platform/accounts/acc-1/resources/${RES_ID}` &&
        init?.method === "DELETE",
    );
    expect(deleteCall).toBeTruthy();
    expect((deleteCall as [string, RequestInit])[1].headers).toHaveProperty("If-Match", "3");
  });

  it("详情：编辑信息（PATCH）/Refresh/删除 动作齐全，节点与活动只读（AC⑨）", async () => {
    renderRouterAt(`/platform/accounts/acc-1/resources/${RES_ID}`);
    await screen.findByTestId("platform-resource-detail-name");
    expect(screen.getByTestId("platform-resource-detail-name")).toHaveTextContent("Shared Doc");
    expect(screen.getByTestId("platform-resource-detail-account")).toHaveTextContent("Acme Corp");

    // Refresh
    fireEvent.click(screen.getByTestId("platform-resource-refresh"));
    await screen.findByTestId("platform-resource-detail-notice");
    expect(screen.getByTestId("platform-resource-detail-notice")).toHaveTextContent("已提交刷新");

    // 编辑信息
    fireEvent.click(screen.getByTestId("platform-resource-edit-open"));
    const form = screen.getByTestId("platform-resource-edit-form");
    const nameInput = within(form).getByDisplayValue("Shared Doc");
    fireEvent.change(nameInput, { target: { value: "Renamed Doc" } });
    fireEvent.submit(form);
    await screen.findByTestId("platform-resource-detail-notice");
    expect(screen.getByTestId("platform-resource-detail-notice")).toHaveTextContent("已保存「Renamed Doc」的元数据");

    // 内容 tab：节点只读（无下载入口）
    fireEvent.click(screen.getByTestId("platform-resource-tab-content"));
    expect(await screen.findByTestId("platform-resource-nodes")).toHaveTextContent("a.txt");
    expect(screen.getByTestId("platform-resource-nodes-note")).toHaveTextContent("不提供单节点读取与下载");
    expect(screen.queryByText("下载")).not.toBeInTheDocument();

    // 活动 tab：只读（无取消按钮）
    fireEvent.click(screen.getByTestId("platform-resource-tab-activity"));
    expect(await screen.findByTestId("platform-resource-operations")).toHaveTextContent("刷新");
    expect(screen.queryByText("取消")).not.toBeInTheDocument();
  });

  it("新增 Resource：上传文件 → 导入目标 Account（09 §40.1，AC⑨）", async () => {
    renderRouterAt("/platform/accounts/acc-1/resources");
    await screen.findByTestId(`platform-resource-row-res_${RES_ID}`);
    fireEvent.click(screen.getByTestId("platform-resource-import-open"));

    expect(screen.getByTestId("platform-import-target")).toHaveTextContent("Acme Corp 共享 Resource（平台代管）");
    // 等待 Capabilities 加载完成后按钮才可用（09 §40.5 前端预检）
    await screen.findByText(/每批最多/);
    const fileInput = screen.getByLabelText("选择文件") as HTMLInputElement;
    const file = new File(["hello"], "doc.md", { type: "text/markdown" });
    fireEvent.change(fileInput, { target: { files: [file] } });
    fireEvent.click(screen.getByText("开始导入"));

    // 导入成功：弹窗关闭（与 P3-E4 同款交互：提交后关闭并刷新列表）
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByTestId("platform-import-target")).not.toBeInTheDocument();
    // 上传请求走平台 resource-uploads 路径
    const uploadCall = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/api/platform/v1/platform/accounts/acc-1/resource-uploads"),
    );
    expect(uploadCall).toBeTruthy();
    // 导入请求体携带 upload_id（上传 → 导入两步），POST 平台 imports 端点
    const importCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/platform/v1/platform/accounts/acc-1/resources/imports" &&
        String(init?.method) === "POST",
    );
    expect(importCall).toBeTruthy();
    const [, importInit] = importCall as [string, RequestInit];
    const sent = JSON.parse(String(importInit.body)) as { items: { upload_id: string }[] };
    expect(sent.items[0].upload_id).toBe("up-1");
  });
});
