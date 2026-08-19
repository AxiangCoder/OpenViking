/**
 * /platform/accounts 平台 Account 管理页集成测试（13 §89.2，P4-E4 AC②③④⑤⑥）。
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
    "account.manage.platform",
    "account.delete",
    "user.read.platform",
    "role.assign.platform",
    "user.password.reset.platform",
    "credential.read.platform",
    "credential.revoke.platform",
    "memory.read.platform",
    "session.read.platform",
    "resource.user_private.read.platform",
    "skill.user_private.read.platform",
    "resource.account_shared.read.platform",
    "resource.account_shared.write.platform",
    "resource.account_shared.delete.platform",
    "skill.account_shared.read.platform",
    "audit.read",
    "task.read.platform",
    "monitoring.read",
  ],
  can_switch_account: false,
  csrf_token: null,
};

const ACCT_ACTIVE = {
  id: "acc-1",
  code: "acme",
  name: "Acme Corp",
  status: "active",
  ov_account_id: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};
const ACCT_FAILED = {
  id: "acc-2",
  code: "beta",
  name: "Beta Inc",
  status: "failed",
  ov_account_id: null,
  created_at: "2026-08-02T00:00:00Z",
  updated_at: "2026-08-02T00:00:00Z",
};
const ACCT_SUSPENDED = {
  id: "acc-3",
  code: "gamma",
  name: "Gamma LLC",
  status: "suspended",
  ov_account_id: null,
  created_at: "2026-08-03T00:00:00Z",
  updated_at: "2026-08-03T00:00:00Z",
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

describe("/platform/accounts（13 §89.2，AC②③④⑤⑥）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: psaMe });
      }
      if (method === "GET" && url === "/api/platform/v1/platform/accounts") {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [ACCT_ACTIVE, ACCT_FAILED, ACCT_SUSPENDED], next_cursor: null },
        });
      }
      if (method === "POST" && url === "/api/platform/v1/platform/accounts") {
        const body = JSON.parse(String(init?.body)) as Record<string, string>;
        return jsonResponse(200, {
          status: "ok",
          result: {
            account: { ...ACCT_ACTIVE, id: "acc-new", code: body.account_code, name: body.account_name },
            first_admin: {
              id: "u-admin",
              username: body.admin_username,
              email: body.admin_email,
              role: "account_admin",
              ov_user_id: null,
              initial_password: "InitPass789",
            },
          },
        });
      }
      if (method === "GET" && url === "/api/platform/v1/platform/accounts/acc-1/deletion-preview") {
        return jsonResponse(200, {
          status: "ok",
          result: {
            resource_type: "account",
            resource_id: "acc-1",
            target_name: "Acme Corp",
            impacted: { users: 5, resources: 3, skills: 2 },
            recoverable: true,
            purge_after: null,
          },
        });
      }
      if (method === "DELETE" && url === "/api/platform/v1/platform/accounts/acc-1") {
        return jsonResponse(200, {
          status: "ok",
          result: {
            resource_type: "account",
            resource_id: "acc-1",
            deletion_job_id: "job-1",
            deleted_at: "2026-08-19T00:00:00Z",
            restore_until: "2026-09-18T00:00:00Z",
          },
        });
      }
      if (
        method === "POST" &&
        url === "/api/platform/v1/platform/accounts/acc-2/provisioning/retry"
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: { account_id: "acc-2", status: "provisioning", retried_events: 2 },
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

  it("列表渲染：名称/code/状态/成员数/创建时间；Actor 上下文明确、无 Account 切换入口（AC②③）", async () => {
    renderRouterAt("/platform/accounts");
    const activeRow = await screen.findByTestId("platform-account-row-acc-1");
    expect(activeRow).toHaveTextContent("Acme Corp");
    expect(activeRow).toHaveTextContent("acme");
    expect(within(activeRow).getByTestId("platform-account-status-acc-1")).toHaveTextContent("正常");
    expect(within(activeRow).getByTestId("platform-account-members-acc-1")).toHaveTextContent("—");
    expect(within(activeRow).getByTestId("platform-account-name-acc-1")).toHaveAttribute(
      "href",
      "/platform/accounts/acc-1/users",
    );
    expect(screen.getByTestId("platform-accounts-actor")).toHaveTextContent("平台管理员");
    // AC②：选择目标 Account 是管理浏览，不改变登录者身份
    expect(screen.getByTestId("platform-accounts-actor")).toHaveTextContent("不改变登录者身份");
    expect(screen.queryByText(/切换 Account|切换账号/)).not.toBeInTheDocument();
  });

  it("状态筛选作用于已加载列表（AC③）", async () => {
    renderRouterAt("/platform/accounts");
    await screen.findByTestId("platform-account-row-acc-1");
    const filter = screen.getByTestId("platform-account-status-filter");
    fireEvent.change(filter, { target: { value: "failed" } });
    expect(screen.queryByTestId("platform-account-row-acc-1")).not.toBeInTheDocument();
    expect(screen.getByTestId("platform-account-row-acc-2")).toBeInTheDocument();
    fireEvent.change(filter, { target: { value: "suspended" } });
    expect(screen.getByTestId("platform-account-row-acc-3")).toBeInTheDocument();
  });

  it("suspended 只展示不操作；failed 显示「重试开通」并幂等调用（AC③⑤）", async () => {
    renderRouterAt("/platform/accounts");
    const suspendedRow = await screen.findByTestId("platform-account-row-acc-3");
    // AC③：suspended 只展示不操作（无删除/重试按钮）
    expect(
      within(suspendedRow).queryByText("删除"),
    ).not.toBeInTheDocument();
    expect(
      within(suspendedRow).queryByText("重试开通"),
    ).not.toBeInTheDocument();

    // AC⑤：仅 failed 显示「重试开通」
    const failedRow = screen.getByTestId("platform-account-row-acc-2");
    const retryButton = within(failedRow).getByTestId("platform-account-retry-acc-2");
    expect(retryButton).toHaveTextContent("重试开通");
    fireEvent.click(retryButton);
    await screen.findByTestId("platform-accounts-notice");
    expect(screen.getByTestId("platform-accounts-notice")).toHaveTextContent("已重新提交 Beta Inc 的开通流程（状态 provisioning，重试 2 个事件）");
    const retryCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/platform/v1/platform/accounts/acc-2/provisioning/retry" &&
        init?.method === "POST",
    );
    expect(retryCall).toBeTruthy();
  });

  it("创建 Account：表单含首位 Admin 邮箱/显示名、无创建 PSA 入口、成功一次性展示初始密码（AC④）", async () => {
    renderRouterAt("/platform/accounts");
    await screen.findByTestId("platform-account-row-acc-1");
    fireEvent.click(screen.getByTestId("platform-account-create-open"));

    const form = screen.getByTestId("platform-account-create-form");
    expect(form).toBeInTheDocument();
    // AC④：无创建/重置 PSA 入口
    expect(screen.getByTestId("platform-account-create-role-note")).toHaveTextContent(
      "不提供创建或重置另一个 Platform Super Admin 的入口",
    );

    fireEvent.change(screen.getByTestId("platform-account-create-name"), {
      target: { value: "New Corp" },
    });
    fireEvent.change(screen.getByTestId("platform-account-create-code"), {
      target: { value: "newco" },
    });
    fireEvent.change(screen.getByTestId("platform-account-create-admin-email"), {
      target: { value: "admin@newco.example" },
    });
    fireEvent.change(screen.getByTestId("platform-account-create-admin-username"), {
      target: { value: "newadmin" },
    });
    fireEvent.change(screen.getByTestId("platform-account-create-admin-display-name"), {
      target: { value: "New Admin" },
    });
    fireEvent.submit(form);

    // AC④：一次性展示首位 Admin 初始密码
    const createdView = await screen.findByTestId("platform-account-created");
    expect(createdView).toHaveTextContent("Account 已创建");
    expect(screen.getByTestId("platform-account-created-secret")).toHaveTextContent("InitPass789");
    expect(screen.getByTestId("platform-account-created-subject")).toHaveTextContent(
      "首位 Admin：newadmin（admin@newco.example）｜ 角色：account_admin",
    );
    expect(screen.getByTestId("platform-account-created-role-note")).toHaveTextContent(
      "Account Admin 的创建与提升只能由 Platform Super Admin 执行",
    );

    const createCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === "/api/platform/v1/platform/accounts" && init?.method === "POST",
    );
    expect(createCall).toBeTruthy();
    const [, init] = createCall as [string, RequestInit];
    const sent = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(sent).toEqual({
      account_code: "newco",
      account_name: "New Corp",
      admin_email: "admin@newco.example",
      admin_username: "newadmin",
      admin_display_name: "New Admin",
    });
    // 无角色字段（不会创建 PSA）
    expect(sent).not.toHaveProperty("role");

    // 关闭后不可再取（初始密码只存在内存态，06 §13.9）
    fireEvent.click(screen.getByTestId("platform-account-created-close"));
    expect(screen.queryByText("InitPass789")).not.toBeInTheDocument();
    expect(await screen.findByTestId("platform-account-row-acc-1")).toBeInTheDocument();
  });

  it("创建失败：ACCOUNT_CODE_ALREADY_EXISTS 映射为表单内联错误", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return jsonResponse(200, { status: "ok", result: psaMe });
      if (init?.method === "POST" && url === "/api/platform/v1/platform/accounts") {
        return jsonResponse(409, { status: "error", error: { code: "ACCOUNT_CODE_ALREADY_EXISTS" } });
      }
      return jsonResponse(200, {
        status: "ok",
        result: { items: [ACCT_ACTIVE], next_cursor: null },
      });
    });
    renderRouterAt("/platform/accounts");
    await screen.findByTestId("platform-account-row-acc-1");
    fireEvent.click(screen.getByTestId("platform-account-create-open"));
    const form = screen.getByTestId("platform-account-create-form");
    fireEvent.change(screen.getByTestId("platform-account-create-name"), {
      target: { value: "Dup" },
    });
    fireEvent.change(screen.getByTestId("platform-account-create-code"), {
      target: { value: "acme" },
    });
    fireEvent.change(screen.getByTestId("platform-account-create-admin-email"), {
      target: { value: "a@example.com" },
    });
    fireEvent.change(screen.getByTestId("platform-account-create-admin-username"), {
      target: { value: "admin" },
    });
    fireEvent.submit(form);
    await screen.findByTestId("platform-account-create-error");
    expect(screen.getByTestId("platform-account-create-error")).toHaveTextContent(
      "该 Account code 已被使用",
    );
  });

  it("删除：deletion-preview 影响范围 + 确认后进入 30 天回收期（89.2，AC③）", async () => {
    renderRouterAt("/platform/accounts");
    const activeRow = await screen.findByTestId("platform-account-row-acc-1");
    fireEvent.click(within(activeRow).getByTestId("platform-account-delete-acc-1"));

    const impact = await screen.findByTestId("platform-account-delete-impact");
    expect(impact).toHaveTextContent("目标：Acme Corp（acme）");
    expect(impact).toHaveTextContent("5 个成员");
    expect(impact).toHaveTextContent("3 个 Resource");
    expect(impact).toHaveTextContent("2 个 Skill");
    expect(impact).toHaveTextContent("30 天回收期");

    fireEvent.click(screen.getByTestId("platform-account-delete-confirm"));
    await screen.findByTestId("platform-accounts-notice");
    expect(screen.getByTestId("platform-accounts-notice")).toHaveTextContent(
      "已删除 Acme Corp：进入 30 天回收期",
    );
  });

  it("AC①：非 PSA（Account Admin）访问被守卫阻止，不发起平台 API 请求", async () => {
    const adminMe: AuthMeResult = {
      ...psaMe,
      account: { id: "acc-1", code: "acme", name: "Acme Corp" },
      roles: ["account_admin"],
      permissions: ["user.read"],
    };
    setAuthStateForTest({ status: "authenticated", me: adminMe, sessionExpired: false });
    renderRouterAt("/platform/accounts");
    expect(await screen.findByTestId("shell-app")).toBeInTheDocument();
    expect(screen.queryByTestId("shell-platform")).not.toBeInTheDocument();
    const platformCall = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/platform/accounts"),
    );
    expect(platformCall).toBeUndefined();
  });
});
