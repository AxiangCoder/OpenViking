/**
 * Resource 详情页集成测试（09 §42-§45，P3-E4 AC③④⑤⑥⑦⑧）。
 * 渲染完整路由树 + 详情路由，mock fetch 数据层。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, type AuthMeResult } from "@/features/auth/auth-state";

const RESOURCE_ID = "00000000-0000-0000-0000-000000000001";
const DETAIL_PATH = `/app/resources/private/${RESOURCE_ID}`;

const SUMMARY_BASE = {
  id: `res_${RESOURCE_ID}`,
  visibility: "user_private",
  name: "产品需求文档",
  description: "供产品讨论检索",
  source_type: "web",
  source_display: "example.com/docs",
  tags: ["type=requirement", "project=openviking"],
  lifecycle_status: "active",
  processing: {
    state: "succeeded",
    stage: "succeeded",
    latest_operation_id: null,
    last_succeeded_at: "2026-08-18T08:00:00Z",
  },
  watch: { state: "not_configured" },
  version: 3,
  created_at: "2026-08-18T07:50:00Z",
  updated_at: "2026-08-18T08:00:00Z",
};

function detailOf(overrides: Record<string, unknown> = {}) {
  return {
    ...SUMMARY_BASE,
    overview: null,
    content: { node_count: 4, size_bytes: 4096 },
    current_operation: null,
    ...overrides,
  };
}

const CAPABILITIES = {
  source_types: ["upload", "web", "git"] as const,
  upload: { max_files_per_batch: 3, max_file_size_bytes: 10 * 1024 * 1024, ttl_minutes: 15, accepts_archives: false },
  watch: { enabled: true, interval_presets_minutes: [60, 360, 1440], manual_refresh_min_interval_seconds: 300 },
  git: { ignore_dirs_max: 50, include_exclude_max: 20, processing_mode_fixed: "semantic_and_vectors" },
};

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const PERMS = [
  "resource.user_private.read.self",
  "resource.user_private.write.self",
  "resource.user_private.delete.self",
  "resource.account_shared.write.account",
  "resource.account_shared.read.account",
  "task.read.self",
  "task.cancel.self",
];

function makeAuth(roles: string[] = ["user"], permissions: string[] = PERMS): AuthMeResult {
  return {
    account: { id: "acc-1", name: "测试Account" },
    user: { id: "u-1", ov_user_id: "ov-u-1" },
    roles,
    permissions,
    can_switch_account: false,
    csrf_token: null,
  };
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

/** 默认 mock：capabilities + 详情 + 节点 + operations + deletion-preview。 */
function defaultMock(fetchMock: ReturnType<typeof vi.fn>, detail = detailOf()) {
  fetchMock.mockImplementation(async (url: string) => {
    const path = String(url);
    if (path.endsWith("/resources/capabilities")) {
      return jsonResponse(200, { status: "ok", result: CAPABILITIES });
    }
    if (path.includes(`/me/resources/${RESOURCE_ID}/nodes`)) {
      if (path.endsWith("/nodes")) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              { node_id: "n-1", name: "readme.md", path: "readme.md", type: "file", size_bytes: 2048, mime_type: "text/markdown" },
              { node_id: "n-2", name: "docs", path: "docs", type: "dir", size_bytes: 0, mime_type: null },
            ],
            next_cursor: null,
          },
        });
      }
      return jsonResponse(200, {
        status: "ok",
        result: { node_id: "n-1", name: "readme.md", path: "readme.md", size_bytes: 2048, mime_type: "text/markdown", preview: "# 标题\n正文内容" },
      });
    }
    if (path.includes(`/me/resources/${RESOURCE_ID}/operations`)) {
      return jsonResponse(200, {
        status: "ok",
        result: {
          items: [
            {
              id: "op-1",
              operation_type: "resource_import",
              status: "succeeded",
              stage: "succeeded",
              initiated_by: "user",
              created_at: "2026-08-18T08:00:00Z",
              completed_at: "2026-08-18T08:01:00Z",
              cancellable: false,
              error: null,
              generation: 1,
            },
          ],
          next_cursor: null,
        },
      });
    }
    if (path.includes("/deletion-preview")) {
      return jsonResponse(200, {
        status: "ok",
        result: {
          resource_id: RESOURCE_ID,
          name: "产品需求文档",
          visibility: "user_private",
          content: { node_count: 4, size_bytes: 4096 },
          watch: { configured: true, will_be_paused: true },
          inflight_cancelled: ["op-9"],
          restore_until: "2026-09-18T08:00:00Z",
          shared_impact_note: null,
        },
      });
    }
    if (path.endsWith(`/me/resources/${RESOURCE_ID}`)) {
      return jsonResponse(200, { status: "ok", result: detail });
    }
    return jsonResponse(200, { status: "ok", result: null });
  });
}

describe("Resource 详情页（09 §42-§45，AC③-⑧）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    configurePlatformClient({ fetchImpl: fetchMock, csrfTokenProvider: () => "csrf" });
  });

  afterEach(() => {
    configurePlatformClient({ fetchImpl: undefined, csrfTokenProvider: undefined });
  });

  it("AC⑧：页面不出现 Viking URI / 原始 Task ID；展示脱敏来源与状态", async () => {
    defaultMock(fetchMock);
    setAuthStateForTest({ status: "authenticated", me: makeAuth(), sessionExpired: false });
    renderRouterAt(DETAIL_PATH);
    expect(await screen.findByText("产品需求文档")).toBeInTheDocument();
    expect(screen.getAllByText(/example\.com\/docs/).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/viking:\/\//)).not.toBeInTheDocument();
    expect(screen.queryByText(/原始 Task|task_id|task id/i)).not.toBeInTheDocument();
  });

  it("AC⑧：非法产品 ID → 404 语义（不接受 URI 类标识符）", async () => {
    setAuthStateForTest({ status: "authenticated", me: makeAuth(), sessionExpired: false });
    renderRouterAt("/app/resources/private/viking://user/ov-u-1/resources/x");
    // 路由层 404（非产品 ID 无法进入详情）；页面上不出现 URI 原文泄露
    const notFound = await screen.findByTestId("not-found-page");
    expect(notFound).toBeInTheDocument();
    expect(screen.getByText(/未找到页面（404）/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("viking://user");
  });

  it("AC⑤：RESOURCE_BUSY——current_operation 运行中时 Refresh 按钮禁用且显示正在同步", async () => {
    defaultMock(fetchMock, detailOf({
      current_operation: {
        id: "op-9",
        operation_type: "resource_refresh",
        status: "running",
        stage: "fetching",
        initiated_by: "user",
        created_at: null,
        completed_at: null,
        cancellable: true,
        error: null,
        generation: 2,
      },
    }));
    setAuthStateForTest({ status: "authenticated", me: makeAuth(), sessionExpired: false });
    renderRouterAt(DETAIL_PATH);
    const refreshButton = await screen.findByRole("button", { name: /处理中/ });
    expect(refreshButton).toBeDisabled();
    expect(await screen.findByText(/正在同步/)).toBeInTheDocument();
  });

  it("AC⑤：上传文件不能 Watch——自动同步标签页显示不可用说明", async () => {
    defaultMock(fetchMock, detailOf({ source_type: "upload", source_display: "a.pdf", watch: { state: "not_configured" } }));
    setAuthStateForTest({ status: "authenticated", me: makeAuth(), sessionExpired: false });
    renderRouterAt(DETAIL_PATH);
    await screen.findByText("产品需求文档");
    fireEvent.click(screen.getByRole("button", { name: "自动同步" }));
    expect(await screen.findByText(/上传文件不支持自动同步/)).toBeInTheDocument();
  });

  it("AC③：内容树根固定、预览纯文本、下载链接指向节点接口", async () => {
    defaultMock(fetchMock);
    setAuthStateForTest({ status: "authenticated", me: makeAuth(), sessionExpired: false });
    renderRouterAt(DETAIL_PATH);
    await screen.findByText("产品需求文档");
    fireEvent.click(screen.getByRole("button", { name: "内容" }));
    expect(await screen.findByText(/readme\.md/)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/readme\.md/));
    const preview = await screen.findByTestId("node-preview");
    expect(preview.textContent).toContain("# 标题");
    expect(preview.textContent).toContain("正文内容");
    const download = within(preview).getByRole("link", { name: "下载" });
    expect(download).toHaveAttribute(
      "href",
      `/api/platform/v1/me/resources/${RESOURCE_ID}/nodes/n-1/download`,
    );
  });

  it("AC④：元数据冲突 RESOURCE_VERSION_CONFLICT → 重新加载并提示再次提交", async () => {
    defaultMock(fetchMock);
    setAuthStateForTest({ status: "authenticated", me: makeAuth(), sessionExpired: false });
    renderRouterAt(DETAIL_PATH);
    await screen.findByText("产品需求文档");
    fireEvent.click(screen.getByRole("button", { name: "编辑信息" }));
    const nameInput = await screen.findByDisplayValue("产品需求文档");
    fireEvent.change(nameInput, { target: { value: "新名称" } });

    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const path = String(url);
      if (path.endsWith("/resources/capabilities")) {
        return jsonResponse(200, { status: "ok", result: CAPABILITIES });
      }
      if (path.endsWith(`/me/resources/${RESOURCE_ID}`) && init?.method === "PATCH") {
        return jsonResponse(409, {
          status: "error",
          error: { code: "RESOURCE_VERSION_CONFLICT", message: "conflict" },
        });
      }
      if (path.endsWith(`/me/resources/${RESOURCE_ID}`)) {
        return jsonResponse(200, { status: "ok", result: detailOf({ version: 4 }) });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText(/版本冲突/)).toBeInTheDocument();
    expect(await screen.findByText(/已重新加载最新信息/)).toBeInTheDocument();
  });

  it("AC⑥：删除预览弹窗完整要素（节点数/大小/Watch 暂停/在途取消/30 天截止）", async () => {
    defaultMock(fetchMock);
    setAuthStateForTest({ status: "authenticated", me: makeAuth(), sessionExpired: false });
    renderRouterAt(DETAIL_PATH);
    await screen.findByText("产品需求文档");
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    const preview = await screen.findByTestId("delete-preview");
    expect(within(preview).getByText("产品需求文档")).toBeInTheDocument();
    expect(within(preview).getByText(/4 个文件/)).toBeInTheDocument();
    expect(within(preview).getByText(/立即暂停/)).toBeInTheDocument();
    expect(within(preview).getByText(/1 个任务将被请求取消/)).toBeInTheDocument();
    expect(within(preview).getByText(/30 天回收期/)).toBeInTheDocument();
    // 确认删除 → DELETE 携带 If-Match 版本
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => {
      const deleteCall = fetchMock.mock.calls.find(
        (call) => String(call[0]) === `/api/platform/v1/me/resources/${RESOURCE_ID}` && call[1]?.method === "DELETE",
      );
      expect(deleteCall).toBeDefined();
      expect((deleteCall![1].headers as Record<string, string>)["If-Match"]).toBe("3");
    });
  });

  it("AC⑦：普通 User 的私有详情无发布入口（09 §44.1）", async () => {
    defaultMock(fetchMock);
    setAuthStateForTest({ status: "authenticated", me: makeAuth(["user"]), sessionExpired: false });
    renderRouterAt(DETAIL_PATH);
    await screen.findByText("产品需求文档");
    expect(screen.queryByRole("button", { name: "发布为共享" })).not.toBeInTheDocument();
  });

  it("AC⑦：Account Admin 发布成功 → 跳转新共享详情（原对象保留语义）", async () => {    setAuthStateForTest({ status: "authenticated", me: makeAuth(["account_admin"]), sessionExpired: false });
    fetchMock.mockImplementation(async (url: string) => {
      const path = String(url);
      if (path.endsWith("/resources/capabilities")) {
        return jsonResponse(200, { status: "ok", result: CAPABILITIES });
      }
      if (path.endsWith(`/me/resources/${RESOURCE_ID}/publish`)) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            resource_id: "res_22222222-0000-0000-0000-000000000002",
            visibility: "account_shared",
            source_resource_id: RESOURCE_ID,
          },
        });
      }
      if (path.endsWith(`/me/resources/${RESOURCE_ID}`)) {
        return jsonResponse(200, { status: "ok", result: detailOf() });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    const router = renderRouterAt(DETAIL_PATH);
    const publishButton = await screen.findByRole("button", { name: "发布为共享" });
    fireEvent.click(publishButton);
    const dialog = await screen.findByRole("dialog", { name: "发布为共享" });
    expect(within(dialog).getByText(/不复制私有 Watch 配置/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "确认发布" }));
    await waitFor(() => {
      const pubCall = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/publish"));
      expect(pubCall).toBeDefined();
      expect(router.state.location.pathname).toBe(
        "/app/resources/shared/22222222-0000-0000-0000-000000000002",
      );
    });
  });

  it("共享详情：普通 User 只读——无编辑/Refresh/替换/删除按钮（06 §13.3，AC①）", async () => {
    setAuthStateForTest({
      status: "authenticated",
      me: makeAuth(["user"], ["resource.account_shared.read.account"]),
      sessionExpired: false,
    });
    fetchMock.mockImplementation(async (url: string) => {
      const path = String(url);
      if (path.endsWith("/resources/capabilities")) {
        return jsonResponse(200, { status: "ok", result: CAPABILITIES });
      }
      if (path.endsWith(`/account/resources/${RESOURCE_ID}`)) {
        return jsonResponse(200, {
          status: "ok",
          result: detailOf({ visibility: "account_shared", source_type: "git", source_display: "github.com/org/repo" }),
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt(`/app/resources/shared/${RESOURCE_ID}`);
    expect(await screen.findByText("产品需求文档")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑信息" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Refresh/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
    // 自动同步标签可读但无写动作
    fireEvent.click(screen.getByRole("button", { name: "自动同步" }));
    expect(await screen.findByText(/无写权限，自动同步配置只读/)).toBeInTheDocument();
  });

  it("共享详情：Account Admin 可见编辑/Refresh/删除（与 /admin 同能力，P4-E3 复用）", async () => {
    setAuthStateForTest({
      status: "authenticated",
      me: makeAuth(["account_admin"], [
        "resource.account_shared.read.account",
        "resource.account_shared.write.account",
        "resource.account_shared.delete.account",
      ]),
      sessionExpired: false,
    });
    fetchMock.mockImplementation(async (url: string) => {
      const path = String(url);
      if (path.endsWith("/resources/capabilities")) {
        return jsonResponse(200, { status: "ok", result: CAPABILITIES });
      }
      if (path.endsWith(`/account/resources/${RESOURCE_ID}`)) {
        return jsonResponse(200, {
          status: "ok",
          result: detailOf({ visibility: "account_shared" }),
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt(`/app/resources/shared/${RESOURCE_ID}`);
    expect(await screen.findByText("产品需求文档")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "编辑信息" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Refresh/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除" })).toBeInTheDocument();
  });

  it("AC⑤：上传来源「替换文件」→ 上传后 POST /replace（09 §43.1）", async () => {
    setAuthStateForTest({ status: "authenticated", me: makeAuth(), sessionExpired: false });
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const path = String(url);
      if (path.endsWith("/resources/capabilities")) {
        return jsonResponse(200, { status: "ok", result: CAPABILITIES });
      }
      if (path.endsWith("/me/resource-uploads")) {
        const file = (init?.body as FormData).get("file") as File;
        return jsonResponse(200, {
          status: "ok",
          result: { upload_id: "up-new", filename: file.name, mime_type: "text/plain", size_bytes: 1, expires_at: "" },
        });
      }
      if (path.endsWith(`/me/resources/${RESOURCE_ID}/replace`)) {
        return jsonResponse(200, {
          status: "ok",
          result: { resource_id: RESOURCE_ID, operation_id: "op-9", status: "succeeded" },
        });
      }
      if (path.endsWith(`/me/resources/${RESOURCE_ID}`)) {
        return jsonResponse(200, {
          status: "ok",
          result: detailOf({ source_type: "upload", source_display: "a.pdf", watch: { state: "not_configured" } }),
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt(DETAIL_PATH);
    await screen.findByText("产品需求文档");
    fireEvent.click(screen.getByRole("button", { name: "替换文件" }));
    const input = screen.getByLabelText("选择替换文件") as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File([new Uint8Array(16)], "b.pdf", { type: "application/pdf" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "上传并替换" }));
    await waitFor(() => {
      const replaceCall = fetchMock.mock.calls.find((call) =>
        String(call[0]).endsWith(`/me/resources/${RESOURCE_ID}/replace`),
      );
      expect(replaceCall).toBeDefined();
      expect(JSON.parse(String(replaceCall![1].body))).toEqual({ upload_id: "up-new" });
    });
    expect(await screen.findByText(/已提交替换/)).toBeInTheDocument();
  });

  it("活动：可取消任务显示取消按钮，取消前确认弹窗展示目标/类型/状态/影响（06 §13.7）", async () => {    setAuthStateForTest({ status: "authenticated", me: makeAuth(), sessionExpired: false });
    fetchMock.mockImplementation(async (url: string) => {
      const path = String(url);
      if (path.endsWith("/resources/capabilities")) {
        return jsonResponse(200, { status: "ok", result: CAPABILITIES });
      }
      if (path.endsWith(`/me/resources/${RESOURCE_ID}/operations`)) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: "op-9",
                operation_type: "resource_refresh",
                status: "running",
                stage: "fetching",
                initiated_by: "user",
                created_at: "2026-08-18T08:00:00Z",
                completed_at: null,
                cancellable: true,
                error: null,
                generation: 2,
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (path.endsWith(`/me/resources/${RESOURCE_ID}/operations/op-9/cancel`)) {
        return jsonResponse(200, { status: "ok", result: { operation_id: "op-9", status: "cancelling" } });
      }
      if (path.endsWith(`/me/resources/${RESOURCE_ID}`)) {
        return jsonResponse(200, { status: "ok", result: detailOf() });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt(DETAIL_PATH);
    await screen.findByText("产品需求文档");
    fireEvent.click(screen.getByRole("button", { name: "活动" }));
    fireEvent.click(await screen.findByRole("button", { name: "取消" }));
    const dialog = await screen.findByRole("dialog", { name: "取消任务" });
    expect(dialog.textContent).toContain("产品需求文档");
    expect(dialog.textContent).toContain("刷新");
    expect(dialog.textContent).toContain("处理中");
    fireEvent.click(screen.getByRole("button", { name: "确认取消" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        `/api/platform/v1/me/resources/${RESOURCE_ID}/operations/op-9/cancel`,
        expect.objectContaining({ method: "POST" }),
      );
    });
  });
});
