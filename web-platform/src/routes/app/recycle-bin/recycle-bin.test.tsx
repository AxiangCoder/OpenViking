/**
 * 回收站（我的）页集成测试（05 §12.5，11 §72，06 §14.6，P3-E3 AC⑦⑧）。
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
  permissions: ["session.delete.self", "resource.user_private.delete.self"],
  can_switch_account: false,
  csrf_token: null,
};

const SESSION_JOB = {
  id: "job-session-1",
  resource_type: "session",
  resource_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
  target_name: "Codex #aabbccdd",
  deleted_at: "2026-08-19T10:00:00Z",
  purge_after: "2026-09-18T10:00:00Z",
  status: "pending",
  restore_allowed: true,
  restore_permission: "session.delete.self",
  restored_at: null,
};

const RESOURCE_JOB = {
  id: "job-resource-1",
  resource_type: "resource",
  resource_id: "11111111-2222-4333-8444-555555555555",
  target_name: "设计文档",
  deleted_at: "2026-08-18T10:00:00Z",
  purge_after: "2026-09-17T10:00:00Z",
  status: "pending",
  restore_allowed: true,
  restore_permission: "resource.user_private.delete.self",
  restored_at: null,
};

const NOT_RESTORABLE = {
  id: "job-hidden-1",
  resource_type: "skill",
  resource_id: "22222222-3333-4444-8555-666666666666",
  target_name: "他人私有 Skill",
  deleted_at: "2026-08-17T10:00:00Z",
  purge_after: "2026-09-16T10:00:00Z",
  status: "pending",
  restore_allowed: false,
  restore_permission: null,
  restored_at: null,
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

describe("回收站（我的）页（05 §12.5，AC⑦⑧）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/recycle-bin/") && String((init as RequestInit)?.method) === "POST") {
        return jsonResponse(200, {
          status: "ok",
          result: {
            resource_type: "session",
            resource_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            deletion_job_id: "job-session-1",
            deleted_at: "2026-08-19T10:00:00Z",
            restore_until: "2026-09-18T10:00:00Z",
          },
        });
      }
      if (url.includes("/recycle-bin")) {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [SESSION_JOB, RESOURCE_JOB, NOT_RESTORABLE], next_cursor: null },
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

  it("AC⑧：仅展示可恢复对象（restore_allowed=false 的不显示）；按类型分组", async () => {
    renderRouterAt("/app/recycle-bin");
    expect(await screen.findByTestId("recycle-item-session")).toBeInTheDocument();
    expect(screen.getByTestId("recycle-item-resource")).toBeInTheDocument();
    expect(screen.queryByText("他人私有 Skill")).not.toBeInTheDocument();
    expect(screen.queryByTestId("recycle-item-skill")).not.toBeInTheDocument();
    // 按类型分组
    expect(screen.getByText("对话 Session")).toBeInTheDocument();
    expect(screen.getByText("Resource")).toBeInTheDocument();
  });

  it("AC⑧：恢复按钮按对象类型携带权限码（无权限项不显示恢复按钮）", async () => {
    const adminMe: AuthMeResult = {
      ...me,
      roles: ["user"],
      permissions: ["resource.user_private.delete.self"], // 无 session.delete.self
    };
    setAuthStateForTest({ status: "authenticated", me: adminMe, sessionExpired: false });
    renderRouterAt("/app/recycle-bin");
    await screen.findByTestId("recycle-item-resource");
    expect(screen.getByTestId("restore-button-resource-11111111-2222-4333-8444-555555555555")).toBeInTheDocument();
    // session 无权限 → 按钮隐藏为「无恢复权限」
    expect(screen.queryByTestId("restore-button-session-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")).not.toBeInTheDocument();
    expect(screen.getAllByText("无恢复权限").length).toBeGreaterThan(0);
  });

  it("AC⑧：恢复确认弹窗展示恢复截止；确认后 POST 恢复并刷新列表", async () => {
    renderRouterAt("/app/recycle-bin");
    await screen.findByTestId("recycle-item-session");
    fireEvent.click(screen.getByTestId("restore-button-session-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"));
    const dialog = await screen.findByTestId("restore-dialog");
    expect(dialog).toHaveTextContent("对话 Session");
    expect(dialog).toHaveTextContent("Codex #aabbccdd");
    expect(within(dialog).getByTestId("restore-dialog-deadline").textContent).toMatch(
      /\d{4}\/\d{1,2}\/\d{1,2}/,
    );
    fireEvent.click(screen.getByTestId("restore-confirm"));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([u, init]) => String(u).includes("/recycle-bin/job-session-1/restore") && (init as RequestInit)?.method === "POST",
      );
      expect(call).toBeDefined();
    });
    // 恢复后列表刷新（再次 GET 回收站）
    await waitFor(() => {
      const getCalls = fetchMock.mock.calls.filter(([u]) =>
        String(u).includes("/recycle-bin") && !String(u).includes("/restore"),
      );
      expect(getCalls.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("AC⑧：RESTORE_WINDOW_EXPIRED → 提示恢复窗口已过期并刷新", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/recycle-bin/") && String((init as RequestInit)?.method) === "POST") {
        return jsonResponse(409, { status: "error", error: { code: "RESTORE_WINDOW_EXPIRED" } });
      }
      if (url.includes("/recycle-bin")) {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [SESSION_JOB, RESOURCE_JOB, NOT_RESTORABLE], next_cursor: null },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/app/recycle-bin");
    await screen.findByTestId("recycle-item-session");
    fireEvent.click(screen.getByTestId("restore-button-session-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"));
    await screen.findByTestId("restore-dialog");
    fireEvent.click(screen.getByTestId("restore-confirm"));
    expect(await screen.findByTestId("recycle-bin-action-error")).toHaveTextContent(
      "恢复窗口已过期",
    );
    // 弹窗关闭并触发列表刷新
    await waitFor(() => {
      expect(screen.queryByTestId("restore-dialog")).not.toBeInTheDocument();
    });
  });

  it("AC⑦：Session 软删后出现在回收站，属主可自助恢复（弹窗含影响提示）", async () => {
    renderRouterAt("/app/recycle-bin");
    await screen.findByTestId("recycle-item-session");
    fireEvent.click(screen.getByTestId("restore-button-session-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"));
    const dialog = await screen.findByTestId("restore-dialog");
    // 恢复不回滚已产生的 Memory 变更（11 §72）
    expect(dialog).toHaveTextContent("不会回滚");
  });

  it("空回收站提示", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input).includes("/recycle-bin")) {
        return jsonResponse(200, { status: "ok", result: { items: [], next_cursor: null } });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/app/recycle-bin");
    expect(await screen.findByTestId("recycle-bin-empty")).toBeInTheDocument();
  });
});
