/**
 * /admin/users/{id}/resources/{resourceId} 成员 Resource 只读预览集成测试
 * （13 §84.2、05 §12.6，14 号计划 §98.7，P4-E2 AC②⑥）。
 *
 * - 只读概览：无编辑信息/替换/Refresh/Watch/发布/删除/下载/导出按钮（AC②）；
 * - 非法产品 ID 404 语义（lib/links.ts 契约，AC⑥）；
 * - 加载失败保留页面框架，展示 Request ID 与重试（13 §84.3）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, type AuthMeResult } from "@/features/auth/auth-state";

const adminMe: AuthMeResult = {
  account: { id: "acc-1", code: "acme", name: "Acme Corp" },
  user: { id: "u-admin", ov_user_id: "ov-u-admin", display_name: "Admin Lee" },
  roles: ["account_admin"],
  permissions: ["user.read", "resource.user_private.read.account"],
  can_switch_account: false,
  csrf_token: null,
};

const ALICE = {
  id: "u-1",
  username: "alice",
  email: "alice@example.com",
  display_name: "Alice",
  status: "active",
  role: "user",
  ov_user_id: "ov-u-1",
  created_at: "2026-08-01T00:00:00Z",
  last_login_at: null,
};

const USERS_RESULT = { items: [ALICE], next_cursor: null };

const DETAIL = {
  id: "res_11111111-2222-3333-4444-555555555555",
  visibility: "user_private",
  name: "Q3 报告",
  description: "季度报告",
  source_type: "web",
  source_display: "https://docs.example.com/q3",
  tags: ["report=q3"],
  lifecycle_status: "active",
  processing: {
    state: "succeeded",
    stage: "succeeded",
    latest_operation_id: "op-1",
    last_succeeded_at: "2026-08-18T09:30:00Z",
  },
  watch: { state: "active", interval_minutes: 60, last_run_at: "2026-08-18T09:00:00Z" },
  version: 2,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-18T09:30:00Z",
  overview: "# Q3 季度报告\n营收与支出概览。",
  content: { node_count: 12, size_bytes: 34567 },
  current_operation: null,
};

const RESOURCE_PATH =
  "/api/platform/v1/admin/users/u-1/resources/11111111-2222-3333-4444-555555555555";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function renderRouterAt(path: string) {
  const router = createAppRouter({ history: createMemoryHistory({ initialEntries: [path] }) });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("/admin/users/{id}/resources/{resourceId} 成员 Resource 只读预览（AC②⑥）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: adminMe });
      }
      if (init?.method === "GET" && url === "/api/platform/v1/admin/users") {
        return jsonResponse(200, { status: "ok", result: USERS_RESULT });
      }
      if (init?.method === "GET" && url === RESOURCE_PATH) {
        return jsonResponse(200, { status: "ok", result: DETAIL });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: adminMe, sessionExpired: false });
  });

  afterEach(() => {
    cleanup();
    setAuthStateForTest({ status: "idle", me: null, sessionExpired: false });
  });

  it("AC②：只读概览展示全部预览信息，无任何管理/导出/下载/Watch/发布/删除按钮", async () => {
    renderRouterAt(
      "/admin/users/u-1/resources/11111111-2222-3333-4444-555555555555",
    );
    expect(await screen.findByTestId("member-resource-name")).toHaveTextContent("Q3 报告");
    expect(screen.getByTestId("member-resource-content")).toHaveTextContent("12 个节点");
    expect(screen.getByTestId("member-resource-watch")).toHaveTextContent("每 60 分钟");
    expect(screen.getByTestId("member-resource-overview")).toHaveTextContent("Q3 季度报告");
    expect(screen.getByTestId("member-resource-readonly-note")).toHaveTextContent("不提供修改、替换、Refresh、Watch、发布、删除、下载或导出");

    // AC②⑥：无按钮/链接形式的管理入口
    expect(
      screen.queryByRole("button", { name: /编辑|替换|Refresh|自动同步|发布|删除|下载|导出|复制链接/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /下载/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId("node-tree")).not.toBeInTheDocument();
  });

  it("AC⑥：非法产品 ID 显示 404 语义，不发起详情请求", async () => {
    renderRouterAt("/admin/users/u-1/resources/not-a-product-id");
    expect(await screen.findByTestId("member-resource-invalid-id")).toHaveTextContent("404");
    const calls = fetchMock.mock.calls.filter(([url]) => String(url).includes("/resources/not-a-product-id"));
    expect(calls).toHaveLength(0);
  });

  it("加载失败保留页面框架，展示 Request ID 与重试（13 §84.3）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return jsonResponse(200, { status: "ok", result: adminMe });
      if (init?.method === "GET" && url === "/api/platform/v1/admin/users") {
        return jsonResponse(200, { status: "ok", result: USERS_RESULT });
      }
      if (init?.method === "GET" && url === RESOURCE_PATH) {
        return jsonResponse(
          500,
          { status: "error", error: { code: "INTERNAL", message: "storage down" } },
          { "x-request-id": "req-r1" },
        );
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/admin/users/u-1/resources/11111111-2222-3333-4444-555555555555");
    const errorBox = await screen.findByTestId("member-resource-load-error");
    expect(errorBox).toHaveTextContent("storage down");
    expect(errorBox).toHaveTextContent("req-r1");

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return jsonResponse(200, { status: "ok", result: adminMe });
      if (init?.method === "GET" && url === "/api/platform/v1/admin/users") {
        return jsonResponse(200, { status: "ok", result: USERS_RESULT });
      }
      if (init?.method === "GET" && url === RESOURCE_PATH) {
        return jsonResponse(200, { status: "ok", result: DETAIL });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    fireEvent.click(screen.getByTestId("member-resource-retry"));
    expect(await screen.findByTestId("member-resource-name")).toHaveTextContent("Q3 报告");
    expect(screen.queryByTestId("member-resource-load-error")).not.toBeInTheDocument();
  });
});
