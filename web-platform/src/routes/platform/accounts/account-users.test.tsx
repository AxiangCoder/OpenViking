/**
 * /platform/accounts/{accountId}/users 平台 Account 用户页集成测试
 * （13 §89.3，P4-E4 AC②⑥⑦）。
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
    "user.read.platform",
    "role.assign.platform",
    "user.password.reset.platform",
    "credential.read.platform",
    "memory.read.platform",
    "session.read.platform",
    "resource.user_private.read.platform",
    "skill.user_private.read.platform",
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

const ALICE = {
  id: "u-1",
  username: "alice",
  email: "alice@example.com",
  display_name: "Alice",
  status: "active",
  role: "user",
  ov_user_id: "ov-u-1",
  created_at: "2026-08-01T00:00:00Z",
  last_login_at: "2026-08-18T09:00:00Z",
};
const BOB = {
  id: "u-2",
  username: "bob",
  email: "bob@example.com",
  display_name: "Bob",
  status: "active",
  role: "account_admin",
  ov_user_id: "ov-u-2",
  created_at: "2026-08-02T00:00:00Z",
  last_login_at: null,
};
const PSA_USER = {
  id: "u-3",
  username: "psa2",
  email: "psa2@example.com",
  display_name: "Second PSA",
  status: "active",
  role: "platform_super_admin",
  ov_user_id: "ov-u-3",
  created_at: "2026-08-03T00:00:00Z",
  last_login_at: null,
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

describe("/platform/accounts/{id}/users（13 §89.3，AC②⑥⑦）", () => {
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
      if (method === "GET" && url === "/api/platform/v1/platform/accounts/acc-1/users") {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [ALICE, BOB, PSA_USER], next_cursor: null },
        });
      }
      if (method === "PUT" && url === "/api/platform/v1/platform/accounts/acc-1/users/u-1/role") {
        return jsonResponse(200, { status: "ok", result: { ...ALICE, role: "account_admin" } });
      }
      if (
        method === "POST" &&
        url === "/api/platform/v1/platform/accounts/acc-1/users/u-2/password/reset"
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: { new_password: "NewPass999", sessions_revoked: 2 },
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

  it("列表渲染 + 目标 Account 上下文明确、不改变 Actor（AC②）", async () => {
    renderRouterAt("/platform/accounts/acc-1/users");
    const aliceRow = await screen.findByTestId("platform-user-row-u-1");
    expect(aliceRow).toHaveTextContent("alice");
    expect(aliceRow).toHaveTextContent("Alice");
    expect(aliceRow).toHaveTextContent("alice@example.com");
    expect(screen.getByTestId("platform-users-account-context")).toHaveTextContent("Acme Corp");
    expect(screen.getByTestId("platform-users-account-context")).toHaveTextContent("不改变登录者身份");
    expect(screen.getByTestId("platform-user-row-u-2")).toHaveTextContent("Account Admin");
  });

  it("提升：仅 user → account_admin（user 行显示、account_admin/PSA 行不显示），确认后 PUT role（AC⑥）", async () => {
    renderRouterAt("/platform/accounts/acc-1/users");
    const aliceRow = await screen.findByTestId("platform-user-row-u-1");
    // user 行：可提升
    expect(within(aliceRow).getByTestId("platform-user-promote-u-1")).toHaveTextContent("提升为 Account Admin");
    // account_admin 行：不可提升（AC⑥）
    const bobRow = screen.getByTestId("platform-user-row-u-2");
    expect(within(bobRow).queryByText("提升为 Account Admin")).not.toBeInTheDocument();
    // PSA 行：不可提升
    const psaRow = screen.getByTestId("platform-user-row-u-3");
    expect(within(psaRow).queryByText("提升为 Account Admin")).not.toBeInTheDocument();

    fireEvent.click(within(aliceRow).getByTestId("platform-user-promote-u-1"));
    expect(screen.getByTestId("platform-promote-impact")).toHaveTextContent("user → account_admin");
    fireEvent.click(screen.getByTestId("platform-user-promote-confirm"));
    await screen.findByTestId("platform-users-notice");
    expect(screen.getByTestId("platform-users-notice")).toHaveTextContent("已将 Alice 提升为 Account Admin");
    const promoteCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/platform/v1/platform/accounts/acc-1/users/u-1/role" &&
        init?.method === "PUT",
    );
    expect(promoteCall).toBeTruthy();
  });

  it("平台级重置：PSA 目标无重置入口；成功后一次性展示新密码（AC⑥）", async () => {
    renderRouterAt("/platform/accounts/acc-1/users");
    const bobRow = await screen.findByTestId("platform-user-row-u-2");
    // account_admin：可重置（严格低级别，03 §8.3）
    expect(within(bobRow).getByTestId("platform-user-reset-u-2")).toHaveTextContent("重置密码");
    // PSA 目标：不可重置（AC⑥）
    const psaRow = screen.getByTestId("platform-user-row-u-3");
    expect(within(psaRow).queryByText("重置密码")).not.toBeInTheDocument();

    fireEvent.click(within(bobRow).getByTestId("platform-user-reset-u-2"));
    // 06 §13.9：退出登录设备提示
    expect(screen.getByText(/将使该用户所有网页登录设备退出/)).toBeInTheDocument();
    expect(screen.getByText(/不会删除 OpenViking 对话和记忆/)).toBeInTheDocument();
    expect(screen.getByText(/不会撤销该用户的 API Key/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("platform-user-reset-confirm"));

    const resetView = await screen.findByTestId("platform-user-reset");
    expect(resetView).toHaveTextContent("密码已重置");
    expect(screen.getByTestId("platform-user-reset-secret")).toHaveTextContent("NewPass999");
    expect(screen.getByTestId("platform-user-reset-subject")).toHaveTextContent("已撤销 2 个登录会话");
    expect(screen.getByTestId("platform-user-reset-subject")).toHaveTextContent("目标 Account：Acme Corp");
  });

  it("行入口：查看数据/API Keys 按权限展示，指向平台路由（AC⑦）", async () => {
    renderRouterAt("/platform/accounts/acc-1/users");
    const aliceRow = await screen.findByTestId("platform-user-row-u-1");
    expect(within(aliceRow).getByTestId("platform-user-data-u-1")).toHaveAttribute(
      "href",
      "/platform/accounts/acc-1/users/u-1/data",
    );
    expect(within(aliceRow).getByTestId("platform-user-keys-u-1")).toHaveAttribute(
      "href",
      "/platform/accounts/acc-1/users/u-1/api-keys",
    );
  });
});
