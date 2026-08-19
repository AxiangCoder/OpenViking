/**
 * /admin/activity 共享任务页测试（13 §87.3，05 §12.6，14 号计划 §98.8，P4-E3 AC③）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, type AuthMeResult } from "@/features/auth/auth-state";

const adminMe: AuthMeResult = {
  account: { id: "acc-1", code: "acme", name: "Acme Corp" },
  user: { id: "u-admin", ov_user_id: "ov-u-admin" },
  roles: ["account_admin"],
  permissions: ["task.read.account_shared", "task.cancel.account_shared"],
  can_switch_account: false,
  csrf_token: null,
};

// 后端 /admin/activity 聚合 DTO 为扁平 error 字段（aggregates._operation_dto）
const ADMIN_OP_RUNNING = {
  id: "op-shared-1",
  operation_type: "resource_import",
  status: "running",
  stage: "processing",
  target_type: "resource",
  target_id: "res-shared-1",
  target_visibility: "account_shared",
  generation: 1,
  cancellable: true,
  initiated_by: "user",
  error_code: null,
  error_summary: null,
  retryable: false,
  created_at: "2026-08-19T09:00:00Z",
  completed_at: null,
};

const ADMIN_OP_FAILED = {
  id: "op-shared-2",
  operation_type: "resource_refresh",
  status: "failed",
  stage: null,
  target_type: "resource",
  target_id: "res-shared-2",
  target_visibility: "account_shared",
  generation: 2,
  cancellable: false,
  initiated_by: "system",
  error_code: "RESOURCE_SYNC_FAILED",
  error_summary: "远程内容获取超时",
  retryable: true,
  created_at: "2026-08-19T08:00:00Z",
  completed_at: "2026-08-19T08:01:00Z",
};

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
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

describe("/admin/activity（13 §87.3，AC③）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: adminMe });
      }
      if (method === "POST" && url.includes("/admin/activity/op-shared-1/cancel")) {
        return jsonResponse(200, {
          status: "ok",
          result: { operation_id: "op-shared-1", status: "cancelling" },
        });
      }
      if (method === "GET" && url.includes("/admin/activity")) {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [ADMIN_OP_RUNNING, ADMIN_OP_FAILED], next_cursor: null },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: adminMe, sessionExpired: false });
  });

  afterEach(() => {
    cleanup();
  });

  it("AC③：复用共享组件渲染共享任务摘要，扁平 error 字段规整为嵌套 error", async () => {
    renderRouterAt("/admin/activity");
    const table = await screen.findByTestId("activity-table");

    expect(within(table).getByText("导入 Resource")).toBeInTheDocument();
    expect(within(table).getByText("刷新 Resource")).toBeInTheDocument();
    expect(within(table).getByText("远程内容获取超时")).toBeInTheDocument();
    expect(screen.getByTestId("cancel-button-op-shared-1")).toBeInTheDocument();
    expect(screen.queryByTestId("cancel-button-op-shared-2")).not.toBeInTheDocument();
  });

  it("AC③：不展示原始 Task ID/堆栈/Worker 路径；仅展示共享任务（页面声明）", async () => {
    renderRouterAt("/admin/activity");
    await screen.findByTestId("activity-table");

    const page = document.body.textContent ?? "";
    // 页面声明仅共享任务范围（04 §10.12）
    expect(page).toContain("仅展示当前 Account 共享对象任务");
    expect(page).toContain("不包含原始 Task ID、内部堆栈与 Worker 路径");
    // 原始内部标识不得以可读形式出现（说明性文案中的词语除外）
    for (const forbidden of [
      "ov_operation_id",
      "ov-operation",
      "task_id",
      "stack",
      "/var/",
      "/home/",
      "res-shared-1",
      "target_id",
    ]) {
      expect(page).not.toContain(forbidden);
    }
    // 表格内不含任何 Worker/Task 字样（仅表头与说明区外）
    const table = document.querySelector("[data-testid='activity-table']")?.textContent ?? "";
    for (const forbidden of ["Task ID", "worker", "Worker", "stack", "ov_"]) {
      expect(table).not.toContain(forbidden);
    }
  });

  it("AC③：确认取消 → POST /admin/activity/{id}/cancel → 状态进入取消中", async () => {
    renderRouterAt("/admin/activity");
    await screen.findByTestId("activity-table");
    fireEvent.click(screen.getByTestId("cancel-button-op-shared-1"));
    await screen.findByTestId("cancel-dialog");
    fireEvent.click(screen.getByTestId("cancel-confirm"));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([u, init]) =>
          String(u).includes("/admin/activity/op-shared-1/cancel") && (init as RequestInit)?.method === "POST",
      );
      expect(call).toBeDefined();
    });
    await waitFor(() => {
      const row = screen.getByTestId("activity-item-op-shared-1");
      expect(within(row).getByTestId("activity-status")).toHaveTextContent("取消中");
    });
  });

  it("空列表提示", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: adminMe });
      }
      if (url.includes("/admin/activity")) {
        return jsonResponse(200, { status: "ok", result: { items: [], next_cursor: null } });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/admin/activity");
    expect(await screen.findByTestId("activity-empty")).toBeInTheDocument();
  });
});
