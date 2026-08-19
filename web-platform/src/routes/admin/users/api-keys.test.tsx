/**
 * /admin/users/{id}/api-keys 成员 API Key 元数据页集成测试
 * （13 §84.2、05 §12.6，14 号计划 §98.7，P4-E2 AC③⑥）。
 *
 * - AC③：只显示元数据与掩码，无明文、无代创建入口；
 * - 撤销：确认弹窗 → DELETE；KEY_NOT_FOUND 幂等成功；
 * - AC⑥：越权动作（创建/取明文）UI 无入口。
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
  user: { id: "u-admin", ov_user_id: "ov-u-admin", display_name: "Admin Lee" },
  roles: ["account_admin"],
  permissions: ["user.read", "credential.read.account", "credential.revoke.account"],
  can_switch_account: false,
  csrf_token: null,
};

const adminReadOnly: AuthMeResult = {
  ...adminMe,
  permissions: ["user.read", "credential.read.account"],
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

const KEY_ACTIVE = {
  id: "k-1",
  name: "dev key",
  key_last_four: "abcd",
  status: "active",
  expires_at: "2027-01-01T00:00:00Z",
  last_used_at: "2026-08-18T10:00:00Z",
  created_at: "2026-08-01T00:00:00Z",
  revoked_at: null,
};

const KEY_REVOKED = {
  id: "k-2",
  name: "old key",
  key_last_four: "wxyz",
  status: "revoked",
  expires_at: null,
  last_used_at: null,
  created_at: "2026-07-01T00:00:00Z",
  revoked_at: "2026-08-05T00:00:00Z",
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

describe("/admin/users/{id}/api-keys 成员 API Key 元数据（AC③⑥）", () => {
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
      if (init?.method === "GET" && url === "/api/platform/v1/admin/users/u-1/api-keys") {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [KEY_ACTIVE, KEY_REVOKED], next_cursor: null },
        });
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

  it("AC③：只展示元数据与掩码，无明文、无代创建入口（05 §12.6）", async () => {
    renderRouterAt("/admin/users/u-1/api-keys");
    expect(await screen.findByTestId("api-key-row-k-1")).toBeInTheDocument();
    expect(screen.getByTestId("api-key-name-k-1")).toHaveTextContent("dev key");
    expect(screen.getByTestId("api-key-mask-k-1")).toHaveTextContent("••••••••abcd");

    // 无明文展示
    expect(screen.queryByText(/ovk_u\./)).not.toBeInTheDocument();
    expect(screen.queryByText("abcd", { exact: true })).not.toBeInTheDocument();

    // AC⑥：管理员不能代创建 Key，页面无任何创建入口
    expect(screen.queryByRole("button", { name: /创建|新建/ })).not.toBeInTheDocument();
    expect(screen.getByTestId("api-keys-readonly-note")).toHaveTextContent("不能代用户创建 Key");

    // 已撤销 Key 显示状态与撤销时间
    const revokedRow = screen.getByTestId("api-key-row-k-2");
    expect(within(revokedRow).getByText("已撤销")).toBeInTheDocument();
    expect(within(revokedRow).getByText(/2026\/8\/5/)).toBeInTheDocument();
    // 已撤销 Key 无撤销按钮
    expect(screen.queryByTestId("api-key-revoke-k-2")).not.toBeInTheDocument();
  });

  it("撤销：确认弹窗 → DELETE；成功后更新列表（AC③）", async () => {
    renderRouterAt("/admin/users/u-1/api-keys");
    await screen.findByTestId("api-key-row-k-1");
    fireEvent.click(screen.getByTestId("api-key-revoke-k-1"));

    const dialog = screen.getByRole("dialog", { name: "撤销 API Key 确认" });
    expect(dialog).toHaveTextContent("dev key");
    expect(dialog).toHaveTextContent("管理员不能代用户创建 Key");

    fireEvent.click(screen.getByTestId("api-key-revoke-confirm"));
    expect(await screen.findByTestId("api-keys-notice")).toHaveTextContent("已撤销 API Key「dev key」");

    const revokeCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/platform/v1/admin/users/u-1/api-keys/k-1" && init?.method === "DELETE",
    );
    expect(revokeCall).toBeTruthy();
  });

  it("撤销幂等：KEY_NOT_FOUND 视为已撤销成功（05 §12.4 语义，AC③）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return jsonResponse(200, { status: "ok", result: adminMe });
      if (init?.method === "GET" && url === "/api/platform/v1/admin/users") {
        return jsonResponse(200, { status: "ok", result: USERS_RESULT });
      }
      if (init?.method === "GET" && url === "/api/platform/v1/admin/users/u-1/api-keys") {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [KEY_ACTIVE], next_cursor: null },
        });
      }
      if (init?.method === "DELETE" && url === "/api/platform/v1/admin/users/u-1/api-keys/k-1") {
        return jsonResponse(404, { status: "error", error: { code: "KEY_NOT_FOUND" } });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/admin/users/u-1/api-keys");
    await screen.findByTestId("api-key-row-k-1");
    fireEvent.click(screen.getByTestId("api-key-revoke-k-1"));
    fireEvent.click(screen.getByTestId("api-key-revoke-confirm"));
    expect(await screen.findByTestId("api-keys-notice")).toHaveTextContent("已撤销 API Key");
    expect(screen.queryByTestId("api-keys-action-error")).not.toBeInTheDocument();
  });

  it("AC⑥：无 credential.revoke.account 权限时不显示撤销按钮", async () => {
    setAuthStateForTest({ status: "authenticated", me: adminReadOnly, sessionExpired: false });
    renderRouterAt("/admin/users/u-1/api-keys");
    await screen.findByTestId("api-key-row-k-1");
    expect(screen.queryByTestId("api-key-revoke-k-1")).not.toBeInTheDocument();
    // 读取元数据不受影响
    expect(screen.getByTestId("api-key-mask-k-1")).toHaveTextContent("••••••••abcd");
  });
});
