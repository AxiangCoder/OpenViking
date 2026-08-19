/**
 * 路由树与 Guard 集成测试（14 号计划 §98.1 AC①/②/⑥，06 §13.2/§13.4）。
 */

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "./router";
import {
  setAuthStateForTest,
  type AuthMeResult,
} from "./features/auth/auth-state";

const userMe: AuthMeResult = {
  account: { id: "acc-1" },
  user: { id: "u-1", ov_user_id: "ov-u-1" },
  roles: ["user"],
  permissions: ["session.read.self", "resource.user_private.read.self"],
  can_switch_account: false,
  csrf_token: null,
};

const adminMe: AuthMeResult = {
  ...userMe,
  roles: ["account_admin"],
  permissions: [
    "user.read",
    "audit.read",
    "role.read",
    "task.read.account_shared",
    "monitoring.read",
    "resource.account_shared.write.account",
  ],
};

const psaMe: AuthMeResult = {
  ...userMe,
  account: null,
  roles: ["platform_super_admin"],
  permissions: ["account.read.platform", "audit.read", "task.read.platform"],
};

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

/** §13.2 全部正式路由（含 /admin /platform 索引重定向与 /app 等父级布局路径）。 */
const EXPECTED_ROUTES: string[] = [
  "/login",
  "/app",
  "/app/search",
  "/app/resources",
  "/app/resources/private",
  "/app/resources/private/$resourceId",
  "/app/resources/shared",
  "/app/resources/shared/$resourceId",
  "/app/skills",
  "/app/skills/private",
  "/app/skills/private/new",
  "/app/skills/private/$skillId",
  "/app/skills/shared",
  "/app/skills/shared/$skillId",
  "/app/sessions",
  "/app/activity",
  "/app/recycle-bin",
  "/app/profile",
  "/app/profile/api-keys",
  "/app/profile/connections",
  "/admin",
  "/admin/users",
  "/admin/users/$userId/data",
  "/admin/users/$userId/resources/$resourceId",
  "/admin/users/$userId/api-keys",
  "/admin/shared-resources",
  "/admin/shared-resources/$resourceId",
  "/admin/shared-skills",
  "/admin/shared-skills/new",
  "/admin/shared-skills/$skillId",
  "/admin/users/$userId/skills/$skillId",
  "/admin/roles",
  "/admin/audit",
  "/admin/activity",
  "/admin/monitoring",
  "/admin/recycle-bin",
  "/admin/settings",
  "/platform",
  "/platform/accounts",
  "/platform/accounts/$accountId/users",
  "/platform/accounts/$accountId/resources",
  "/platform/accounts/$accountId/resources/$resourceId",
  "/platform/accounts/$accountId/skills",
  "/platform/accounts/$accountId/skills/$skillId",
  "/platform/accounts/$accountId/users/$userId/skills/$skillId",
  "/platform/accounts/$accountId/users/$userId/data",
  "/platform/accounts/$accountId/users/$userId/resources/$resourceId",
  "/platform/accounts/$accountId/users/$userId/api-keys",
  "/platform/audit",
  "/platform/activity",
  "/platform/monitoring",
  "/platform/recycle-bin",
  "/oauth/consent",
  "/oauth/verify",
];

describe("路由树（06 §13.2）", () => {
  it("覆盖全部正式路由（AC⑥），无额外产品路由", () => {
    const router = createAppRouter({ history: createMemoryHistory() });
    const actual = new Set(
      Object.keys(router.routesByPath).map((p) =>
        p.length > 1 && p.endsWith("/") ? p.slice(0, -1) : p,
      ),
    );
    for (const expected of EXPECTED_ROUTES) {
      expect(actual.has(expected)).toBe(true);
    }
    // 不允许多出未经设计的路由（冻结点保护）
    const extra = [...actual].filter((p) => !EXPECTED_ROUTES.includes(p) && p !== "/");
    expect(extra).toEqual([]);
  });
});

describe("路由 Guard（AC①）", () => {
  it("未登录访问 /app → 跳 /login 并回跳", async () => {
    setAuthStateForTest({ status: "unauthenticated", me: null, sessionExpired: false });
    renderRouterAt("/app");
    expect(await screen.findByTestId("login-page")).toBeInTheDocument();
    expect(screen.getByTestId("login-redirect").textContent).toContain("/app");
  });

  it("未登录访问 /oauth/consent → 跳 /login，回跳目标=同源授权路由（AC①）", async () => {
    setAuthStateForTest({ status: "unauthenticated", me: null, sessionExpired: false });
    renderRouterAt("/oauth/consent");
    expect(await screen.findByTestId("login-page")).toBeInTheDocument();
    expect(screen.getByTestId("login-redirect").textContent).toContain("/oauth/consent");
    expect(screen.getByTestId("login-redirect").textContent).toContain("仅回跳同源授权路由");
  });

  it("会话过期回登录页并提示（AC③）", async () => {
    setAuthStateForTest({ status: "unauthenticated", me: null, sessionExpired: true });
    renderRouterAt("/app/sessions");
    expect(await screen.findByTestId("login-page")).toBeInTheDocument();
    expect(screen.getByText(/登录会话已过期/)).toBeInTheDocument();
  });

  it("已登录（user）访问 /app → 渲染 app 外壳与首页", async () => {
    setAuthStateForTest({ status: "authenticated", me: userMe, sessionExpired: false });
    renderRouterAt("/app");
    expect(await screen.findByTestId("shell-app")).toBeInTheDocument();
    expect(screen.getAllByText("首页").length).toBeGreaterThanOrEqual(1);
  });

  it("普通 User 访问 /admin → 阻止并回默认入口（06 §13.2，07 §18.4）", async () => {
    setAuthStateForTest({ status: "authenticated", me: userMe, sessionExpired: false });
    renderRouterAt("/admin/users");
    expect(await screen.findByTestId("shell-app")).toBeInTheDocument();
    expect(screen.queryByTestId("shell-admin")).not.toBeInTheDocument();
  });

  it("Account Admin 访问 /admin → 渲染 admin 外壳", async () => {
    setAuthStateForTest({ status: "authenticated", me: adminMe, sessionExpired: false });
    renderRouterAt("/admin/users");
    expect(await screen.findByTestId("shell-admin")).toBeInTheDocument();
    expect(screen.getAllByText("用户管理").length).toBeGreaterThanOrEqual(1);
  });

  it("Account Admin 访问 /platform → 阻止（仅 PSA，06 §13.2）", async () => {
    setAuthStateForTest({ status: "authenticated", me: adminMe, sessionExpired: false });
    renderRouterAt("/platform/accounts");
    expect(await screen.findByTestId("shell-app")).toBeInTheDocument();
    expect(screen.queryByTestId("shell-platform")).not.toBeInTheDocument();
  });

  it("PSA 访问 /platform → 渲染 platform 外壳", async () => {
    setAuthStateForTest({ status: "authenticated", me: psaMe, sessionExpired: false });
    renderRouterAt("/platform/accounts");
    expect(await screen.findByTestId("shell-platform")).toBeInTheDocument();
    expect(screen.getAllByText("Accounts").length).toBeGreaterThanOrEqual(1);
  });
});

describe("导航与按钮按权限隐藏/禁用（AC②）", () => {
  it("User 侧边栏：无 /admin、/platform 入口，无 Account 切换入口（07 §21 条目 14）", async () => {
    setAuthStateForTest({ status: "authenticated", me: userMe, sessionExpired: false });
    renderRouterAt("/app");
    const sidebar = within(await screen.findByTestId("sidebar-app"));
    expect(sidebar.queryByText("用户管理")).not.toBeInTheDocument();
    expect(sidebar.queryByText("Accounts")).not.toBeInTheDocument();
    expect(sidebar.queryByText(/切换 Account|切换账号/)).not.toBeInTheDocument();
  });

  it("User 侧边栏：无权限的共享导航不显示（session.read.self 存在则 Sessions 显示）", async () => {
    setAuthStateForTest({ status: "authenticated", me: userMe, sessionExpired: false });
    renderRouterAt("/app");
    const sidebar = within(await screen.findByTestId("sidebar-app"));
    expect(sidebar.getByText("Sessions")).toBeInTheDocument();
    expect(sidebar.queryByText("共享 Resource")).not.toBeInTheDocument();
  });

  it("共享 Resource 页：普通 User 无管理入口并提示管理员维护；Admin 可见新增（06 §13.3，AC①）", async () => {
    setAuthStateForTest({ status: "authenticated", me: userMe, sessionExpired: false });
    renderRouterAt("/app/resources/shared");
    await screen.findByTestId("shell-app");
    expect(await screen.findByText("共享内容由 Account 管理员维护")).toBeInTheDocument();
    expect(screen.queryByText("新增 Resource")).not.toBeInTheDocument();

    setAuthStateForTest({ status: "authenticated", me: adminMe, sessionExpired: false });
    renderRouterAt("/app/resources/shared");
    expect(await screen.findByText("新增 Resource")).toBeInTheDocument();
  });

  it("Admin 侧边栏按权限过滤（user.read 缺失则不显示用户管理）", async () => {
    const meWithoutUserRead: AuthMeResult = {
      ...psaMe,
      roles: ["account_admin"],
      permissions: ["audit.read"],
    };
    setAuthStateForTest({ status: "authenticated", me: meWithoutUserRead, sessionExpired: false });
    renderRouterAt("/admin");
    const sidebar = within(await screen.findByTestId("sidebar-admin"));
    expect(sidebar.queryByText("用户管理")).not.toBeInTheDocument();
    expect(sidebar.getByText("审计")).toBeInTheDocument();
  });
});
