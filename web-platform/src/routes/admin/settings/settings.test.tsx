/**
 * /admin/settings 页测试（06 §13.2，14 号计划 §98.6，P4-E1 AC⑦）。
 *
 * v0.1 仅展示本 Account 基本信息占位；Account 固定来自登录 Session（AC①）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import {
  setAuthStateForTest,
  type AuthMeResult,
} from "@/features/auth/auth-state";

const adminMe: AuthMeResult = {
  account: { id: "acc-1", code: "acme", name: "Acme Corp" },
  user: { id: "u-admin", ov_user_id: "ov-u-admin" },
  roles: ["account_admin"],
  permissions: ["user.read"],
  can_switch_account: false,
  csrf_token: null,
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

describe("/admin/settings（AC⑦）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: adminMe });
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

  it("仅展示本 Account 基本信息占位，无功能入口（06 §13.2，AC⑦）", async () => {
    renderRouterAt("/admin/settings");
    const page = await screen.findByTestId("admin-settings-page");
    expect(page).toHaveTextContent("Account 设置");
    expect(page).toHaveTextContent("仅展示本 Account 基本信息占位");
    expect(screen.getByTestId("admin-settings-account")).toHaveTextContent("Acme Corp");
    expect(screen.getByTestId("admin-settings-account")).toHaveTextContent("acme");
    expect(screen.getByTestId("admin-settings-account")).toHaveTextContent("acc-1");
    expect(screen.getByTestId("admin-settings-fixed-account")).toHaveTextContent("无切换入口");
    // 不承载正式功能：无表单、无保存/编辑按钮
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
    expect(screen.queryByText(/保存|编辑/)).not.toBeInTheDocument();
  });
});
