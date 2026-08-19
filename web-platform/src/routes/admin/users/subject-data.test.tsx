/**
 * /admin/users/{id}/data 成员数据只读视图集成测试
 * （13 §84.2、14 号计划 §98.7，P4-E2 AC①②⑥⑦ 前端语义）。
 *
 * - AC① 固定 Actor/Subject 横幅、无身份替换入口；
 * - AC② 检索/Session/Resource/Skill 区块全部只读：无修改/导出/下载/Watch/
 *   发布/删除按钮；检索结果只跳成员只读详情、不跳查看者自己的 /app 分区；
 * - AC⑥ 越权动作 UI 无入口（前端侧断言）；
 * - 加载失败保留页面框架展示 Request ID 与重试（13 §84.3）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, type AuthMeResult } from "@/features/auth/auth-state";

const adminMe: AuthMeResult = {
  account: { id: "acc-1", code: "acme", name: "Acme Corp" },
  user: { id: "u-admin", ov_user_id: "ov-u-admin", display_name: "Admin Lee", email: "admin@acme.test" },
  roles: ["account_admin"],
  permissions: [
    "user.read",
    "memory.read.account",
    "session.read.account",
    "resource.user_private.read.account",
    "skill.user_private.read.account",
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

const USERS_RESULT = { items: [ALICE], next_cursor: null };

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
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

describe("/admin/users/{id}/data 成员数据只读视图（84.2，AC①②⑥）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: adminMe });
      }
      if (method === "GET" && url === "/api/platform/v1/admin/users") {
        return jsonResponse(200, { status: "ok", result: USERS_RESULT });
      }
      if (method === "POST" && url.endsWith("/search/find")) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                context_type: "memory",
                display_name: "关于 Alice 的偏好",
                abstract: "喜欢简洁的回复",
                match_reason: "关键词命中",
                visibility: "user_private",
                memory_type: "preference",
                ref_id: null,
              },
              {
                context_type: "resource",
                display_name: "Q3 报告",
                abstract: "季度报告",
                match_reason: "关键词命中",
                visibility: "user_private",
                ref_id: "11111111-2222-3333-4444-555555555555",
              },
              {
                context_type: "skill",
                display_name: "code-review",
                abstract: "代码评审助手",
                match_reason: "关键词命中",
                visibility: "user_private",
                ref_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
              },
            ],
          },
        });
      }
      if (method === "GET" && url === "/api/platform/v1/admin/users/u-1/sessions") {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: "s-1",
                client_name: "codex",
                title: "codex #abcd1234",
                sync_status: "active",
                commit_count: 1,
                message_count: 5,
                last_sync_at: "2026-08-18T10:00:00Z",
                updated_at: "2026-08-18T10:00:00Z",
                created_at: "2026-08-18T09:00:00Z",
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (method === "GET" && url === "/api/platform/v1/admin/users/u-1/sessions/s-1") {
        return jsonResponse(200, {
          status: "ok",
          result: {
            id: "s-1",
            client_name: "codex",
            title: "codex #abcd1234",
            sync_status: "active",
            commit_count: 1,
            message_count: 5,
            pending_tokens: 0,
            last_sync_at: "2026-08-18T10:00:00Z",
            updated_at: "2026-08-18T10:00:00Z",
            created_at: "2026-08-18T09:00:00Z",
          },
        });
      }
      if (method === "GET" && url === "/api/platform/v1/admin/users/u-1/sessions/s-1/messages") {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              { role: "user", content: "你好", sequence: 1, turn_id: null, created_at: "2026-08-18T09:00:01Z" },
            ],
            next_cursor: null,
          },
        });
      }
      if (method === "GET" && url === "/api/platform/v1/admin/users/u-1/sessions/s-1/memory-impact") {
        return jsonResponse(200, {
          status: "ok",
          result: {
            session_id: "s-1",
            commit_count: 1,
            totals: { added: 1, updated: 0, deleted: 0 },
            items: [
              {
                commit_id: "c-1",
                commit_number: 1,
                phase2_status: "completed",
                phase2_error: null,
                message_count_at_commit: 5,
                created_at: "2026-08-18T10:00:00Z",
                completed_at: "2026-08-18T10:00:05Z",
                has_operations: true,
                diffs: [{ memory_type: "preference", action: "add", before: null, after: "喜欢简洁回复" }],
              },
            ],
          },
        });
      }
      if (method === "GET" && url === "/api/platform/v1/admin/users/u-1/resources") {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: "res_11111111-2222-3333-4444-555555555555",
                visibility: "user_private",
                name: "Q3 报告",
                description: "季度报告",
                source_type: "web",
                source_display: "https://docs.example.com/q3",
                tags: [],
                lifecycle_status: "active",
                processing: {
                  state: "succeeded",
                  stage: "succeeded",
                  latest_operation_id: null,
                  last_succeeded_at: "2026-08-18T09:30:00Z",
                },
                watch: {
                  state: "not_configured",
                },
                version: 2,
                created_at: "2026-08-01T00:00:00Z",
                updated_at: "2026-08-18T09:30:00Z",
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (method === "GET" && url === "/api/platform/v1/admin/users/u-1/skills") {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                name: "code-review",
                description: "代码评审助手",
                tags: ["review=code"],
                visibility: "user_private",
                source_type: "online",
                has_auxiliary_files: false,
                status: "active",
                version: 1,
                created_at: "2026-08-10T00:00:00Z",
                updated_at: "2026-08-10T00:00:00Z",
                owner_user_id: "u-1",
              },
            ],
            next_cursor: null,
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

  it("AC①：固定 Actor/Subject 横幅，含 Account 上下文，无任何身份替换入口", async () => {
    renderRouterAt("/admin/users/u-1/data");
    const banner = await screen.findByTestId("subject-data-banner");
    expect(banner).toBeInTheDocument();
    expect(screen.getByTestId("subject-data-line")).toHaveTextContent(
      "以 Admin Lee 身份查看 Alice（alice@example.com）的数据",
    );
    expect(screen.getByTestId("subject-data-account")).toHaveTextContent("Acme Corp");

    // 不允许无痕替换身份：无 Actor/Subject/Account 切换控件
    expect(screen.queryByText(/切换 Account|切换身份|切换 Subject|切换用户/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/身份|Actor|Subject/)).not.toBeInTheDocument();
  });

  it("检索：提交白名单请求体，结果只读，Resource/Skill 只跳成员详情不跳 /app，Memory 打开抽屉（AC②⑥）", async () => {
    renderRouterAt("/admin/users/u-1/data");
    await screen.findByTestId("subject-data-banner");

    fireEvent.change(screen.getByTestId("member-search-query"), { target: { value: "报告" } });
    fireEvent.submit(screen.getByTestId("member-search-form"));

    const hits = await screen.findByTestId("member-search-hits");
    expect(within(hits).getByTestId("member-search-hit-memory")).toHaveTextContent("关于 Alice 的偏好");

    // AC⑥：结果链接只指向成员只读详情（/admin/users/...），不跳 /app
    const resourceHit = within(hits).getByTestId("member-search-hit-resource");
    expect(resourceHit).toHaveAttribute(
      "href",
      "/admin/users/u-1/resources/11111111-2222-3333-4444-555555555555",
    );
    const skillHit = within(hits).getByTestId("member-search-hit-skill");
    expect(skillHit).toHaveAttribute(
      "href",
      "/admin/users/u-1/skills/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    );
    expect(screen.queryByText(/\/app\/resources/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\/app\/skills/)).not.toBeInTheDocument();

    // 请求体仅白名单字段（无 target_uri/score_threshold 等）
    const searchCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url).endsWith("/search/find") && init?.method === "POST",
    );
    expect(searchCall).toBeTruthy();
    const [, init] = searchCall as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ query: "报告" });

    // Memory 只读抽屉：除关闭按钮外无任何动作（无编辑/删除/恢复/下载）
    fireEvent.click(within(hits).getByTestId("member-search-hit-memory"));
    const drawer = await screen.findByTestId("member-memory-drawer");
    expect(within(drawer).getByTestId("member-memory-drawer-type")).toHaveTextContent("preference");
    expect(within(drawer).queryAllByRole("button")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /编辑|删除|恢复|下载/ })).not.toBeInTheDocument();
  });

  it("Sessions：只读历史与 Memory Impact，无删除/恢复按钮（AC②）", async () => {
    renderRouterAt("/admin/users/u-1/data");
    await screen.findByTestId("subject-data-banner");
    fireEvent.click(screen.getByTestId("subject-tab-sessions"));

    const item = await screen.findByTestId("member-session-item-s-1");
    expect(item).toHaveTextContent("codex #abcd1234");
    fireEvent.click(item);

    expect(await screen.findByText("消息历史")).toBeInTheDocument();
    expect(await screen.findByTestId("memory-impact")).toBeInTheDocument();
    expect(screen.queryByTestId("session-delete-button")).not.toBeInTheDocument();
    expect(screen.queryByText(/删除 Session/)).not.toBeInTheDocument();
  });

  it("Resources：只读列表，无新增/导入/编辑/Refresh/Watch/发布/删除/下载入口（AC②⑥）", async () => {
    renderRouterAt("/admin/users/u-1/data");
    await screen.findByTestId("subject-data-banner");
    fireEvent.click(screen.getByTestId("subject-tab-resources"));

    const row = await screen.findByTestId("member-resource-row-res_11111111-2222-3333-4444-555555555555");
    expect(row).toHaveTextContent("Q3 报告");
    const viewLink = within(row).getByTestId("member-resource-view-res_11111111-2222-3333-4444-555555555555");
    expect(viewLink).toHaveAttribute(
      "href",
      "/admin/users/u-1/resources/11111111-2222-3333-4444-555555555555",
    );

    // AC②⑥：只读区块不出现任何管理动作（按钮/链接），无下载链接
    expect(
      screen.queryByRole("button", { name: /新增|导入|编辑|Refresh|自动同步|发布|删除/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /下载/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/复制链接/)).not.toBeInTheDocument();
  });

  it("Skills：只读列表 + 管理权限提示，无编辑/删除/恢复入口（10 §61.3，AC②⑥）", async () => {
    renderRouterAt("/admin/users/u-1/data");
    await screen.findByTestId("subject-data-banner");
    fireEvent.click(screen.getByTestId("subject-tab-skills"));

    const list = await screen.findByTestId("skills-list");
    expect(within(list).getByText("code-review")).toBeInTheDocument();
    expect(screen.getByTestId("skills-list-hint")).toHaveTextContent("只读预览");
    // AC⑥：无编辑/删除/恢复入口（按钮）
    expect(
      screen.queryByRole("button", { name: /编辑|删除|恢复|上传|新建/ }),
    ).not.toBeInTheDocument();
  });

  it("用户列表加载失败时保留页面框架，展示错误与重试（13 §84.3）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return jsonResponse(200, { status: "ok", result: adminMe });
      if (init?.method === "GET" && url === "/api/platform/v1/admin/users") {
        return jsonResponse(
          500,
          { status: "error", error: { code: "INTERNAL", message: "db down" } },
          { "x-request-id": "req-xyz" },
        );
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/admin/users/u-1/data");
    const errorBox = await screen.findByTestId("subject-identity-error");
    expect(errorBox).toHaveTextContent("db down");
    expect(errorBox).toHaveTextContent("req-xyz");
    // 横幅保留（Subject 以用户 ID 兜底），重试可用
    expect(screen.getByTestId("subject-data-banner")).toHaveTextContent("用户 u-1");
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return jsonResponse(200, { status: "ok", result: adminMe });
      if (init?.method === "GET" && url === "/api/platform/v1/admin/users") {
        return jsonResponse(200, { status: "ok", result: USERS_RESULT });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    fireEvent.click(screen.getByTestId("subject-identity-retry"));
    await waitFor(() =>
      expect(screen.getByTestId("subject-data-subject")).toHaveTextContent("Alice"),
    );
  });
});
