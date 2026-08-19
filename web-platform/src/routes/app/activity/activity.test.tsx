/**
 * Activity 页集成测试（06 §13.7，09 §43.3，P3-E3 AC⑩）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, setCsrfToken, type AuthMeResult } from "@/features/auth/auth-state";

const me: AuthMeResult = {
  account: { id: "acc-1", code: "acme", name: "Acme" },
  user: { id: "u-1", ov_user_id: "ov-u-1", display_name: "Alice", email: "alice@example.com" },
  roles: ["user"],
  permissions: ["task.read.self", "task.cancel.self"],
  can_switch_account: false,
  csrf_token: null,
};

const OP_RUNNING = {
  id: "op-running-1",
  operation_type: "resource_import",
  status: "running",
  stage: "processing",
  initiated_by: "user",
  created_at: "2026-08-19T09:00:00Z",
  completed_at: null,
  cancellable: true,
  error: null,
  generation: 1,
};

const OP_FAILED = {
  id: "op-failed-1",
  operation_type: "resource_refresh",
  status: "failed",
  stage: null,
  initiated_by: "system",
  created_at: "2026-08-19T08:00:00Z",
  completed_at: "2026-08-19T08:01:00Z",
  cancellable: false,
  error: { code: "RESOURCE_SYNC_FAILED", summary: "远程内容获取超时", retryable: true },
  generation: 2,
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

describe("Activity 页（06 §13.7，AC⑩）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/activity/") && String((init as RequestInit)?.method) === "POST") {
        return jsonResponse(200, {
          status: "ok",
          result: { operation_id: "op-running-1", status: "cancelling" },
        });
      }
      if (url.includes("/activity")) {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [OP_RUNNING, OP_FAILED], next_cursor: null },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me, sessionExpired: false });
  });

  afterEach(() => {
    setCsrfToken(null);
    cleanup();
  });

  it("AC⑩：仅展示脱敏 Operation 摘要（类型/状态/阶段/错误摘要），无原始 Task ID/堆栈/Worker 路径", async () => {
    renderRouterAt("/app/activity");
    await screen.findByTestId("activity-table");
    expect(screen.getByText("导入 Resource")).toBeInTheDocument();
    expect(screen.getByText("刷新 Resource")).toBeInTheDocument();
    expect(screen.getByText("远程内容获取超时")).toBeInTheDocument();
    const page = document.body.textContent ?? "";
    for (const forbidden of [
      "ov_operation_id",
      "ov-operation",
      "task_id",
      "Task ID",
      "stack",
      "worker",
      "Worker",
      "/var/",
      "/home/",
    ]) {
      expect(page).not.toContain(forbidden);
    }
    // 可取消按钮只对 cancellable 项显示
    expect(screen.getByTestId("cancel-button-op-running-1")).toBeInTheDocument();
    expect(screen.queryByTestId("cancel-button-op-failed-1")).not.toBeInTheDocument();
  });

  it("AC⑩：取消弹窗展示任务类型/当前状态/影响（06 §13.7）", async () => {
    renderRouterAt("/app/activity");
    await screen.findByTestId("activity-table");
    fireEvent.click(screen.getByTestId("cancel-button-op-running-1"));
    const dialog = await screen.findByTestId("cancel-dialog");
    expect(dialog).toHaveTextContent("导入 Resource");
    expect(dialog).toHaveTextContent("执行中");
    expect(dialog).toHaveTextContent("目标 Resource");
    expect(dialog).toHaveTextContent("取消后该导入将停止");
  });

  it("AC⑩：确认取消 → POST /activity/{id}/cancel → 状态进入 cancelling 且按钮消失", async () => {
    renderRouterAt("/app/activity");
    await screen.findByTestId("activity-table");
    fireEvent.click(screen.getByTestId("cancel-button-op-running-1"));
    await screen.findByTestId("cancel-dialog");
    fireEvent.click(screen.getByTestId("cancel-confirm"));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([u, init]) => String(u).includes("/activity/op-running-1/cancel") && (init as RequestInit)?.method === "POST",
      );
      expect(call).toBeDefined();
    });
    await waitFor(() => {
      const row = screen.getByTestId("activity-item-op-running-1");
      expect(within(row).getByTestId("activity-status")).toHaveTextContent("取消中");
    });
    expect(screen.queryByTestId("cancel-button-op-running-1")).not.toBeInTheDocument();
  });

  it("取消失败（不可取消）→ 错误提示与列表刷新", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/activity/") && String((init as RequestInit)?.method) === "POST") {
        return jsonResponse(409, {
          status: "error",
          error: { code: "RESOURCE_OPERATION_NOT_CANCELLABLE" },
        });
      }
      if (url.includes("/activity")) {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [OP_RUNNING, OP_FAILED], next_cursor: null },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/app/activity");
    await screen.findByTestId("activity-table");
    fireEvent.click(screen.getByTestId("cancel-button-op-running-1"));
    await screen.findByTestId("cancel-dialog");
    fireEvent.click(screen.getByTestId("cancel-confirm"));
    expect(await screen.findByTestId("activity-action-error")).toHaveTextContent("不可取消");
  });

  it("空列表提示", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input).includes("/activity")) {
        return jsonResponse(200, { status: "ok", result: { items: [], next_cursor: null } });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/app/activity");
    expect(await screen.findByTestId("activity-empty")).toBeInTheDocument();
  });
});
