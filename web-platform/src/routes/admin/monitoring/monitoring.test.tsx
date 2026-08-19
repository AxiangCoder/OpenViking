/**
 * /admin/monitoring 业务健康摘要页测试（13 §87.4，08 §28.4，14 号计划 §98.8，P4-E3 AC④）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, type AuthMeResult } from "@/features/auth/auth-state";

const adminMe: AuthMeResult = {
  account: { id: "acc-1", code: "acme", name: "Acme Corp" },
  user: { id: "u-admin", ov_user_id: "ov-u-admin" },
  roles: ["account_admin"],
  permissions: ["monitoring.read"],
  can_switch_account: false,
  csrf_token: null,
};

const MONITORING = {
  generated_at: "2026-08-19T10:00:00Z",
  scope: "account",
  summary: {
    accounts: 1,
    active_accounts: 1,
    users: 5,
    active_users: 4,
    content_refs_active_by_type: { resource: 12, skill: 3 },
    deletion_jobs_in_recycle: 2,
    pending_uploads: 1,
    open_operations: 7,
  },
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

describe("/admin/monitoring（13 §87.4，AC④）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: adminMe });
      }
      if (url.includes("/admin/monitoring")) {
        return jsonResponse(200, { status: "ok", result: MONITORING });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: adminMe, sessionExpired: false });
  });

  afterEach(() => {
    cleanup();
  });

  it("AC④：仅展示业务摘要（共享内容/任务/待处理上传/待清理数量），Account 上下文固定", async () => {
    renderRouterAt("/admin/monitoring");
    await screen.findByTestId("admin-monitoring-summary");

    expect(screen.getByTestId("monitoring-content-refs")).toHaveTextContent("15");
    expect(screen.getByTestId("monitoring-content-refs")).toHaveTextContent("Resource 12");
    expect(screen.getByTestId("monitoring-content-refs")).toHaveTextContent("Skill 3");
    expect(screen.getByTestId("monitoring-open-operations")).toHaveTextContent("7");
    expect(screen.getByTestId("monitoring-pending-uploads")).toHaveTextContent("1");
    expect(screen.getByTestId("monitoring-deletion-jobs")).toHaveTextContent("2");
    expect(screen.getByTestId("monitoring-users")).toHaveTextContent("4/5");
    expect(screen.getByTestId("admin-monitoring-account")).toHaveTextContent("Acme Corp");
  });

  it("AC④：不展示底层组件状态（Queue/锁/模型/VectorDB/文件系统/原始请求日志）", async () => {
    renderRouterAt("/admin/monitoring");
    await screen.findByTestId("admin-monitoring-summary");

    // 页面说明区明确声明不展示的底层状态类别
    const page = document.body.textContent ?? "";
    expect(page).toContain("不展示 Queue、锁、模型、VectorDB、文件系统与原始请求日志等底层状态");
    // 底层具体组件/内部概念不以数据或标签形式出现（仅说明文案中的分类词除外）
    for (const forbidden of ["Redis", "snapshot", "filesystem", "request log", "dead-letter", "pg_stat", "VectorDB 使用率"]) {
      expect(page).not.toContain(forbidden);
    }
    // 只读页：无任何写请求
    expect(fetchMock.mock.calls.every(([, init]) => (init?.method ?? "GET") === "GET")).toBe(true);
  });

  it("加载失败：保留页面框架、展示 Request ID 与重试", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: adminMe });
      }
      return jsonResponse(500, {
        status: "error",
        error: { code: "INTERNAL", message: "boom" },
      });
    });
    renderRouterAt("/admin/monitoring");
    const error = await screen.findByTestId("admin-monitoring-load-error");
    expect(error).toHaveTextContent("boom");
    expect(screen.getByTestId("admin-monitoring-retry")).toBeInTheDocument();
  });
});
