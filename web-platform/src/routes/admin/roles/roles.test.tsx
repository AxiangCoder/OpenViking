/**
 * /admin/roles 角色与权限页测试（13 §87.1，03 §9.2/9.3，14 号计划 §98.8，P4-E3 AC①）。
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
  permissions: ["role.read"],
  can_switch_account: false,
  csrf_token: null,
};

const ROLE_USER = {
  id: "r-user",
  code: "user",
  name: "user",
  description: "普通用户",
  ov_base_role: "user",
  rank: 1,
  is_system: true,
  status: "active",
  permissions: ["session.read.self", "task.read.self"],
};

const ROLE_ADMIN = {
  id: "r-admin",
  code: "account_admin",
  name: "account_admin",
  description: "Account 管理员",
  ov_base_role: "admin",
  rank: 2,
  is_system: true,
  status: "active",
  permissions: ["role.read", "audit.read", "task.cancel.account_shared", "session.read.self"],
};

const ROLE_PSA = {
  id: "r-psa",
  code: "platform_super_admin",
  name: "platform_super_admin",
  description: "平台超级管理员",
  ov_base_role: null,
  rank: 3,
  is_system: true,
  status: "active",
  permissions: ["role.read", "audit.read", "account.read.platform"],
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

describe("/admin/roles（13 §87.1，AC①）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: adminMe });
      }
      if (url === "/api/platform/v1/admin/roles") {
        return jsonResponse(200, { status: "ok", result: [ROLE_PSA, ROLE_ADMIN, ROLE_USER] });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: adminMe, sessionExpired: false });
  });

  afterEach(() => {
    cleanup();
  });

  it("AC①：只读展示三内置角色（名称/code/说明/权限数），无创建/编辑/删除/分配入口", async () => {
    renderRouterAt("/admin/roles");
    await screen.findByTestId("admin-roles-list");

    // 名称出现在角色行与矩阵列头（均只读展示）
    expect(screen.getAllByText("Platform Super Admin").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Account Admin").length).toBeGreaterThan(0);
    expect(screen.getAllByText("User").length).toBeGreaterThan(0);
    expect(screen.getByTestId("admin-role-row-platform_super_admin")).toBeInTheDocument();
    expect(screen.getByTestId("admin-role-row-account_admin")).toBeInTheDocument();
    expect(screen.getByTestId("admin-role-row-user")).toBeInTheDocument();

    const page = document.body.textContent ?? "";
    expect(page).toContain("v0.1 仅提供三个内置角色");
    expect(page).toContain("无任何管理入口");

    // 无任何角色管理操作入口（说明文案除外）：无管理按钮、无表单、无写请求
    for (const forbidden of ["新建角色", "角色分配", "权限分配"]) {
      expect(page).not.toContain(forbidden);
    }
    expect(document.querySelector("form")).toBeNull();
    expect(fetchMock.mock.calls.every(([, init]) => (init?.method ?? "GET") === "GET")).toBe(true);
  });

  it("AC①：权限矩阵按 domain 分区展示，正确标记角色是否持有权限", async () => {
    renderRouterAt("/admin/roles");
    await screen.findByTestId("admin-role-matrix");

    expect(screen.getByTestId("matrix-domain-role")).toBeInTheDocument();
    expect(screen.getByTestId("matrix-domain-session")).toBeInTheDocument();
    expect(screen.getByTestId("matrix-domain-audit")).toBeInTheDocument();

    // user 角色持有 session.read.self；account_admin 额外持有 role.read/audit.read
    expect(screen.getByTestId("matrix-row-session.read.self")).toBeInTheDocument();
    expect(screen.getByTestId("matrix-row-role.read")).toBeInTheDocument();
    expect(screen.getByTestId("matrix-row-account.read.platform")).toBeInTheDocument();
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
    renderRouterAt("/admin/roles");
    const error = await screen.findByTestId("admin-roles-load-error");
    expect(error).toHaveTextContent("boom");
    expect(screen.getByTestId("admin-roles-retry")).toBeInTheDocument();
  });
});
