/**
 * /admin/users 用户管理页集成测试（13 §85，14 号计划 §98.6，P4-E1 AC①-⑥⑧）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
  permissions: [
    "user.read",
    "user.create",
    "user.update",
    "user.disable",
    "user.password.reset.account",
    "user.delete",
  ],
  can_switch_account: false,
  csrf_token: null,
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
  display_name: null,
  status: "disabled",
  role: "user",
  ov_user_id: "ov-u-2",
  created_at: "2026-08-02T00:00:00Z",
  last_login_at: null,
};

const CAROL = {
  id: "u-3",
  username: "carol",
  email: "carol@example.com",
  display_name: "Carol",
  status: "active",
  role: "account_admin",
  ov_user_id: "ov-u-3",
  created_at: "2026-08-03T00:00:00Z",
  last_login_at: "2026-08-19T08:00:00Z",
};

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

const LIST_RESULT = { items: [ALICE, BOB, CAROL], next_cursor: null };

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

describe("/admin/users 用户管理页（13 §85，AC①-⑥⑧）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: adminMe });
      }
      if (method === "GET" && url === "/api/platform/v1/admin/users") {
        return jsonResponse(200, { status: "ok", result: LIST_RESULT });
      }
      if (method === "POST" && url === "/api/platform/v1/admin/users") {
        const body = JSON.parse(String(init?.body)) as Record<string, string>;
        return jsonResponse(200, {
          status: "ok",
          result: {
            id: "u-new",
            username: body.username,
            email: body.email,
            display_name: body.display_name ?? null,
            status: "active",
            role: "user",
            ov_user_id: null,
            created_at: "2026-08-19T00:00:00Z",
            last_login_at: null,
            initial_password: "InitPass123",
          },
        });
      }
      if (method === "POST" && url === "/api/platform/v1/admin/users/u-1/disable") {
        return jsonResponse(200, {
          status: "ok",
          result: { id: "u-1", status: "disabled", sessions_revoked: 2, keys_revoked: 3 },
        });
      }
      if (method === "PATCH" && url === "/api/platform/v1/admin/users/u-2") {
        return jsonResponse(200, { status: "ok", result: { ...BOB, status: "active" } });
      }
      if (method === "POST" && url === "/api/platform/v1/admin/users/u-1/password/reset") {
        return jsonResponse(200, {
          status: "ok",
          result: { new_password: "NewPass456", sessions_revoked: 1 },
        });
      }
      if (method === "GET" && url === "/api/platform/v1/admin/users/u-1/deletion-preview") {
        return jsonResponse(200, {
          status: "ok",
          result: {
            resource_type: "user",
            resource_id: "u-1",
            target_name: "Alice",
            impacted: { login_sessions: 2, api_keys: 3 },
            recoverable: true,
            purge_after: null,
          },
        });
      }
      if (method === "DELETE" && url === "/api/platform/v1/admin/users/u-1") {
        return jsonResponse(200, {
          status: "ok",
          result: {
            resource_type: "user",
            resource_id: "u-1",
            deletion_job_id: "job-1",
            deleted_at: "2026-08-19T00:00:00Z",
            restore_until: "2026-09-18T00:00:00Z",
          },
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

  it("列表渲染：code/显示名/邮箱/状态/角色，Account 固定上下文无切换入口（85.1，AC①）", async () => {
    renderRouterAt("/admin/users");
    const aliceRow = await screen.findByTestId("admin-user-row-u-1");
    expect(aliceRow).toHaveTextContent("alice");
    expect(aliceRow).toHaveTextContent("Alice");
    expect(aliceRow).toHaveTextContent("alice@example.com");
    expect(within(aliceRow).getByTestId("admin-user-status-u-1")).toHaveTextContent("正常");
    expect(screen.getByTestId("admin-user-row-u-2")).toHaveTextContent("已禁用");
    expect(screen.getByTestId("admin-user-row-u-3")).toHaveTextContent("Account Admin");

    // AC①：Account 固定来自登录 Session（/auth/me），无切换入口
    expect(screen.getByTestId("admin-fixed-account")).toHaveTextContent("Acme Corp");
    expect(screen.queryByText(/切换 Account|切换账号/)).not.toBeInTheDocument();
  });

  it("状态筛选作用于已加载列表（85.1）", async () => {
    renderRouterAt("/admin/users");
    await screen.findByTestId("admin-user-row-u-1");
    const filter = screen.getByTestId("admin-user-status-filter");
    fireEvent.change(filter, { target: { value: "disabled" } });
    expect(screen.queryByTestId("admin-user-row-u-1")).not.toBeInTheDocument();
    expect(screen.getByTestId("admin-user-row-u-2")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-user-row-u-3")).not.toBeInTheDocument();

    fireEvent.change(filter, { target: { value: "active" } });
    expect(screen.getByTestId("admin-user-row-u-1")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-user-row-u-2")).not.toBeInTheDocument();
  });

  it("创建：表单无角色选择（固定 user）、成功后一次性展示初始密码且关闭后不可再取（85.2，AC②③）", async () => {
    renderRouterAt("/admin/users");
    await screen.findByTestId("admin-user-row-u-1");
    fireEvent.click(screen.getByTestId("admin-user-create-open"));

    const form = screen.getByTestId("admin-user-create-form");
    // AC③：表单无角色选择，不能创建 account_admin
    expect(within(form).queryByRole("combobox")).not.toBeInTheDocument();
    expect(within(form).queryByText("account_admin")).not.toBeInTheDocument();
    expect(screen.getByText(/角色固定为/)).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("admin-user-create-email"), {
      target: { value: "newbie@example.com" },
    });
    fireEvent.change(screen.getByTestId("admin-user-create-username"), {
      target: { value: "newbie" },
    });
    fireEvent.change(screen.getByTestId("admin-user-create-display-name"), {
      target: { value: "New User" },
    });
    fireEvent.submit(form);

    // AC②：一次性初始密码展示
    const createdView = await screen.findByTestId("admin-user-created");
    expect(createdView).toHaveTextContent("用户已创建");
    expect(screen.getByTestId("admin-user-created-secret")).toHaveTextContent("InitPass123");
    expect(screen.getByTestId("admin-user-created-subject")).toHaveTextContent("角色：user");
    // AC⑧：交接收限制提示
    expect(screen.getByTestId("admin-user-created-handover")).toHaveTextContent("创建者可能长期知晓该密码");

    // 请求体仅 email/username/display_name，无角色字段；带 Idempotency-Key（05 §12.2）
    const createCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === "/api/platform/v1/admin/users" && init?.method === "POST",
    );
    expect(createCall).toBeTruthy();
    const [, init] = createCall as [string, RequestInit];
    const sent = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(sent).not.toHaveProperty("role");
    expect(sent).not.toHaveProperty("roles");
    expect(init.headers).toHaveProperty("Idempotency-Key");

    // AC②：关闭后不可再取（初始密码只存在内存态）
    fireEvent.click(screen.getByTestId("admin-user-created-close"));
    expect(screen.queryByText("InitPass123")).not.toBeInTheDocument();
    expect(await screen.findByTestId("admin-user-row-u-1")).toBeInTheDocument();
  });

  it("创建失败：EMAIL_ALREADY_EXISTS 映射为表单内联错误（85.6）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return jsonResponse(200, { status: "ok", result: adminMe });
      if (init?.method === "POST" && url === "/api/platform/v1/admin/users") {
        return jsonResponse(409, {
          status: "error",
          error: { code: "EMAIL_ALREADY_EXISTS", message: "email exists" },
        });
      }
      if (init?.method === "GET" && url === "/api/platform/v1/admin/users") {
        return jsonResponse(200, { status: "ok", result: LIST_RESULT });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/admin/users");
    await screen.findByTestId("admin-user-row-u-1");
    fireEvent.click(screen.getByTestId("admin-user-create-open"));
    const form = screen.getByTestId("admin-user-create-form");
    fireEvent.change(screen.getByTestId("admin-user-create-email"), {
      target: { value: "dup@example.com" },
    });
    fireEvent.change(screen.getByTestId("admin-user-create-username"), {
      target: { value: "dup" },
    });
    fireEvent.submit(form);
    expect(await screen.findByTestId("admin-user-create-error")).toHaveTextContent("邮箱");
  });

  it("禁用：确认弹窗提示会话与 Key 立即失效、对话记忆不受影响（85.3，AC⑤）", async () => {
    renderRouterAt("/admin/users");
    await screen.findByTestId("admin-user-row-u-1");
    fireEvent.click(screen.getByTestId("admin-user-disable-u-1"));
    expect(screen.getByRole("dialog", { name: "禁用用户确认" })).toBeInTheDocument();
    expect(screen.getByText(/该用户所有登录会话立即失效/)).toBeInTheDocument();
    expect(screen.getByText(/该用户全部 API Key 立即拒绝/)).toBeInTheDocument();
    expect(screen.getByText(/OpenViking 对话与记忆不受影响/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("admin-user-disable-confirm"));
    expect(await screen.findByTestId("admin-users-notice")).toHaveTextContent("已禁用 Alice");
    const disableCall = fetchMock.mock.calls.find(
      ([url]) => String(url).endsWith("/admin/users/u-1/disable"),
    );
    expect(disableCall).toBeTruthy();
  });

  it("启用：PATCH status=active（05 §12.6，user.update）", async () => {
    renderRouterAt("/admin/users");
    await screen.findByTestId("admin-user-row-u-2");
    fireEvent.click(screen.getByTestId("admin-user-enable-u-2"));
    expect(await screen.findByTestId("admin-users-notice")).toHaveTextContent("已启用");
    const patchCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === "/api/platform/v1/admin/users/u-2" && init?.method === "PATCH",
    );
    expect(patchCall).toBeTruthy();
    const [, init] = patchCall as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ status: "active" });
  });

  it("分级重置：按钮只对严格低级别目标显示（85.4，AC④）", async () => {
    renderRouterAt("/admin/users");
    await screen.findByTestId("admin-user-row-u-1");
    // 同级（account_admin 目标）不显示重置按钮
    expect(screen.queryByTestId("admin-user-reset-u-3")).not.toBeInTheDocument();
    // 低级别（user 目标）显示
    expect(screen.getByTestId("admin-user-reset-u-1")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("admin-user-reset-u-1"));
    expect(screen.getByRole("dialog", { name: "重置密码确认" })).toBeInTheDocument();
    expect(screen.getByText(/将使该用户所有网页登录设备退出/)).toBeInTheDocument();
    expect(screen.getByText(/不会删除 OpenViking 对话和记忆/)).toBeInTheDocument();
    expect(screen.getByText(/不会撤销该用户的 API Key/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("admin-user-reset-confirm"));
    // AC④⑧：成功后一次性展示新密码 + 交接收限制提示
    const resetView = await screen.findByTestId("admin-user-reset");
    expect(screen.getByTestId("admin-user-reset-secret")).toHaveTextContent("NewPass456");
    expect(screen.getByTestId("admin-user-reset-handover")).toHaveTextContent("创建者可能长期知晓该密码");
    expect(resetView).toHaveTextContent("已撤销 1 个登录会话");

    fireEvent.click(screen.getByTestId("admin-user-reset-close"));
    expect(screen.queryByText("NewPass456")).not.toBeInTheDocument();
  });

  it("删除：预览展示影响范围、只确认/取消不重输密码（85.5，AC⑥）", async () => {
    renderRouterAt("/admin/users");
    await screen.findByTestId("admin-user-row-u-1");
    fireEvent.click(screen.getByTestId("admin-user-delete-u-1"));

    const impact = await screen.findByTestId("admin-user-delete-impact");
    expect(impact).toHaveTextContent("目标：Alice");
    expect(impact).toHaveTextContent("所属 Account：Acme Corp");
    expect(impact).toHaveTextContent("预计影响：2 个登录会话、3 个 API Key");
    expect(impact).toHaveTextContent("30 天回收期");

    // AC⑥：只确认/取消，不重输密码或 Account 名称
    expect(screen.queryByPlaceholderText(/密码/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/密码/)).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "删除用户确认" })).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("admin-user-delete-confirm"));
    expect(await screen.findByTestId("admin-users-notice")).toHaveTextContent("30 天回收期");
    expect(screen.getByTestId("admin-users-notice")).toHaveTextContent("恢复截止");
    const deleteCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === "/api/platform/v1/admin/users/u-1" && init?.method === "DELETE",
    );
    expect(deleteCall).toBeTruthy();
  });

  it("加载失败保留页面框架，展示 Request ID 与重试（13 §84.3）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return jsonResponse(200, { status: "ok", result: adminMe });
      if (init?.method === "GET" && url === "/api/platform/v1/admin/users") {
        return jsonResponse(
          500,
          { status: "error", error: { code: "INTERNAL", message: "db down" } },
          { "x-request-id": "req-123" },
        );
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/admin/users");
    const errorBox = await screen.findByTestId("admin-users-load-error");
    expect(errorBox).toHaveTextContent("db down");
    expect(errorBox).toHaveTextContent("req-123");

    // 重试成功后恢复列表
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return jsonResponse(200, { status: "ok", result: adminMe });
      if (init?.method === "GET" && url === "/api/platform/v1/admin/users") {
        return jsonResponse(200, { status: "ok", result: LIST_RESULT });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    fireEvent.click(screen.getByTestId("admin-users-retry"));
    expect(await screen.findByTestId("admin-user-row-u-1")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-users-load-error")).not.toBeInTheDocument();
  });

  it("行操作：查看数据（Subject 视图）与 API Keys 入口按权限展示（85.1，P4-E2 AC⑥）", async () => {
    const memberAdmin: AuthMeResult = {
      ...adminMe,
      permissions: [
        ...adminMe.permissions,
        "memory.read.account",
        "session.read.account",
        "resource.user_private.read.account",
        "skill.user_private.read.account",
        "credential.read.account",
      ],
    };
    setAuthStateForTest({ status: "authenticated", me: memberAdmin, sessionExpired: false });
    renderRouterAt("/admin/users");
    await screen.findByTestId("admin-user-row-u-1");
    // Account Admin 拥有成员只读与凭证读取权限 → 显示两个入口
    expect(screen.getByTestId("admin-user-data-u-1")).toHaveAttribute("href", "/admin/users/u-1/data");
    expect(screen.getByTestId("admin-user-keys-u-1")).toHaveAttribute("href", "/admin/users/u-1/api-keys");
  });

  it("行操作：无成员只读权限时不显示查看数据入口（越权动作 UI 无入口，AC⑥）", async () => {
    const limitedMe: AuthMeResult = {
      ...adminMe,
      permissions: ["user.read", "user.disable"],
    };
    setAuthStateForTest({ status: "authenticated", me: limitedMe, sessionExpired: false });
    renderRouterAt("/admin/users");
    await screen.findByTestId("admin-user-row-u-1");
    expect(screen.queryByTestId("admin-user-data-u-1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("admin-user-keys-u-1")).not.toBeInTheDocument();
    // 生命周期动作仍可见（权限保留）
    expect(screen.getByTestId("admin-user-disable-u-1")).toBeInTheDocument();
  });

  it("普通 User 角色无管理页权限时 Guard 阻止（前端侧 AC① 复核）", async () => {
    const userMe: AuthMeResult = {
      account: { id: "acc-1" },
      user: { id: "u-9", ov_user_id: "ov-u-9" },
      roles: ["user"],
      permissions: ["session.read.self"],
      can_switch_account: false,
      csrf_token: null,
    };
    setAuthStateForTest({ status: "authenticated", me: userMe, sessionExpired: false });
    renderRouterAt("/admin/users");
    expect(await screen.findByTestId("shell-app")).toBeInTheDocument();
    expect(screen.queryByTestId("shell-admin")).not.toBeInTheDocument();
  });
});
