/**
 * /admin/audit 审计事件页测试（13 §87.2，06 §17，14 号计划 §98.8，P4-E3 AC②）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, type AuthMeResult } from "@/features/auth/auth-state";

const adminMe: AuthMeResult = {
  account: { id: "acc-1", code: "acme", name: "Acme Corp" },
  user: { id: "u-admin", ov_user_id: "ov-u-admin" },
  roles: ["account_admin"],
  permissions: ["audit.read"],
  can_switch_account: false,
  csrf_token: null,
};

const EV_USER_RESET = {
  id: "ev-1",
  occurred_at: "2026-08-18T09:00:00Z",
  request_id: "req-abc-111",
  account_id: "acc-1",
  actor_type: "user",
  actor_user_id: "aaaaaaaa-0000-0000-0000-000000000001",
  actor_account_id: "acc-1",
  actor_system_component: null,
  actor_session_id: "sess-secret-internal-1",
  authentication_method: "session",
  actor_credential_id: "cred-secret-internal-1",
  subject_account_id: "acc-1",
  subject_user_id: "bbbbbbbb-0000-0000-0000-000000000002",
  action: "user.password.reset",
  target_type: "user",
  target_id: "bbbbbbbb-0000-0000-0000-000000000002",
  target_visibility: "user_private",
  scope: "account",
  result: "success",
  reason: null,
  metadata: {
    target_email: "bob@example.com",
    initial_password_hash: "argon2$hash-secret-value",
    cookie: "session-token-value",
  },
};

const EV_SYSTEM_DENIED = {
  id: "ev-2",
  occurred_at: "2026-08-18T08:30:00Z",
  request_id: "req-abc-222",
  account_id: "acc-1",
  actor_type: "system",
  actor_user_id: null,
  actor_account_id: null,
  actor_system_component: "provisioning-worker",
  actor_session_id: null,
  authentication_method: "system",
  actor_credential_id: null,
  subject_account_id: "acc-1",
  subject_user_id: null,
  action: "provisioning.retry",
  target_type: "account",
  target_id: "acc-1",
  target_visibility: null,
  scope: "account",
  result: "denied",
  reason: "PROVISIONING_PENDING",
  metadata: null,
};

const EV_SUBJECT_ACCOUNT = {
  id: "ev-3",
  occurred_at: "2026-08-17T08:00:00Z",
  request_id: "req-abc-333",
  account_id: "acc-1",
  actor_type: "user",
  actor_user_id: "aaaaaaaa-0000-0000-0000-000000000001",
  actor_account_id: "acc-1",
  actor_system_component: null,
  actor_session_id: "sess-2",
  authentication_method: "session",
  actor_credential_id: null,
  subject_account_id: "acc-1",
  subject_user_id: "bbbbbbbb-0000-0000-0000-000000000002",
  action: "user.delete",
  target_type: "user",
  target_id: "bbbbbbbb-0000-0000-0000-000000000002",
  target_visibility: "user_private",
  scope: "account",
  result: "failed",
  reason: "LAST_ACCOUNT_ADMIN_REQUIRED",
  metadata: null,
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

describe("/admin/audit（13 §87.2，AC②）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: adminMe });
      }
      if (url.includes("/admin/audit-events")) {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [EV_USER_RESET, EV_SYSTEM_DENIED, EV_SUBJECT_ACCOUNT], next_cursor: null },
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

  it("AC②：列表展示时间/Actor/Subject/动作/Scope/结果/Request ID；跨用户事件同时含 Actor 与 Subject", async () => {
    renderRouterAt("/admin/audit");
    const table = await screen.findByTestId("admin-audit-table");

    // Actor（User 短 ID）与 Subject（User）同时展示（06 §17.2）
    expect(within(table).getByTestId("audit-actor-ev-1")).toHaveTextContent("User（aaaaaaaa）");
    expect(within(table).getByTestId("audit-subject-ev-1")).toHaveTextContent("User（bbbbbbbb）");
    // 系统组件 Actor
    expect(within(table).getByTestId("audit-actor-ev-2")).toHaveTextContent("provisioning-worker");
    // 动作 / 结果 / Request ID
    expect(within(table).getByText("user.password.reset")).toBeInTheDocument();
    expect(within(table).getByText("成功")).toBeInTheDocument();
    expect(within(table).getByText("拒绝")).toBeInTheDocument();
    expect(within(table).getByText("失败")).toBeInTheDocument();
    expect(within(table).getByTestId("audit-request-id-ev-1")).toHaveTextContent("req-abc-111");
  });

  it("AC②：详情不展示任何敏感字段（密码/Cookie/Key/Token/业务正文/会话与凭据内部 ID）", async () => {
    renderRouterAt("/admin/audit");
    await screen.findByTestId("admin-audit-table");

    const page = document.body.textContent ?? "";
    for (const forbidden of [
      "sess-secret-internal-1",
      "cred-secret-internal-1",
      "argon2",
      "hash-secret-value",
      "session-token-value",
      "bob@example.com",
      "PROVISIONING_PENDING",
      "LAST_ACCOUNT_ADMIN_REQUIRED",
      "target_email",
      "metadata",
      "actor_session_id",
      "actor_credential_id",
    ]) {
      expect(page).not.toContain(forbidden);
    }
  });

  it("AC②：按结果/动作/Actor/Subject/时间筛选（对已加载列表过滤）", async () => {
    renderRouterAt("/admin/audit");
    await screen.findByTestId("admin-audit-table");

    // 结果筛选：仅拒绝
    fireEvent.change(screen.getByTestId("audit-filter-result"), { target: { value: "denied" } });
    await screen.findByTestId("admin-audit-table");
    expect(screen.getByTestId("audit-actor-ev-2")).toBeInTheDocument();
    expect(screen.queryByTestId("audit-actor-ev-1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("audit-actor-ev-3")).not.toBeInTheDocument();

    // 动作筛选
    fireEvent.change(screen.getByTestId("audit-filter-result"), { target: { value: "all" } });
    fireEvent.change(screen.getByTestId("audit-filter-action"), {
      target: { value: "user.delete" },
    });
    await screen.findByTestId("admin-audit-table");
    expect(screen.getByTestId("audit-actor-ev-3")).toBeInTheDocument();
    expect(screen.queryByTestId("audit-actor-ev-1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("audit-actor-ev-2")).not.toBeInTheDocument();

    // Actor 筛选（系统组件名）
    fireEvent.change(screen.getByTestId("audit-filter-action"), { target: { value: "" } });
    fireEvent.change(screen.getByTestId("audit-filter-actor"), {
      target: { value: "provisioning" },
    });
    await screen.findByTestId("admin-audit-table");
    expect(screen.getByTestId("audit-actor-ev-2")).toBeInTheDocument();
    expect(screen.queryByTestId("audit-actor-ev-1")).not.toBeInTheDocument();

    // Subject 筛选（用户 ID 前缀）
    fireEvent.change(screen.getByTestId("audit-filter-actor"), { target: { value: "" } });
    fireEvent.change(screen.getByTestId("audit-filter-subject"), {
      target: { value: "bbbbbbbb" },
    });
    await screen.findByTestId("admin-audit-table");
    expect(screen.getByTestId("audit-actor-ev-1")).toBeInTheDocument();
    expect(screen.queryByTestId("audit-actor-ev-2")).not.toBeInTheDocument();

    // 时间筛选：仅 08:30 之后的事件（since = 2026-08-18T08:30 当天）
    fireEvent.change(screen.getByTestId("audit-filter-subject"), { target: { value: "" } });
    fireEvent.change(screen.getByTestId("audit-filter-since"), {
      target: { value: "2026-08-18" },
    });
    await screen.findByTestId("admin-audit-table");
    expect(screen.getByTestId("audit-actor-ev-1")).toBeInTheDocument();
    expect(screen.getByTestId("audit-actor-ev-2")).toBeInTheDocument();
    expect(screen.queryByTestId("audit-actor-ev-3")).not.toBeInTheDocument();

    // 清除筛选恢复全部
    fireEvent.click(screen.getByTestId("audit-filter-clear"));
    await screen.findByTestId("admin-audit-table");
    expect(screen.getByTestId("audit-actor-ev-1")).toBeInTheDocument();
    expect(screen.getByTestId("audit-actor-ev-3")).toBeInTheDocument();
  });

  it("空列表与筛选无结果提示", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: adminMe });
      }
      if (url.includes("/admin/audit-events")) {
        return jsonResponse(200, { status: "ok", result: { items: [], next_cursor: null } });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/admin/audit");
    expect(await screen.findByTestId("admin-audit-empty")).toBeInTheDocument();
  });
});
