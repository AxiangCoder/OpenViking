/**
 * 统一检索页集成测试（11 §69，06 §13.7，P3-E3 AC②③④）。
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
  permissions: ["session.read.self", "memory.read.self", "resource.user_private.read.self"],
  can_switch_account: false,
  csrf_token: null,
};

const RESOURCE_ID = "11111111-2222-4333-8444-555555555555";
const SKILL_ID = "22222222-3333-4444-8555-666666666666";

const HITS = [
  {
    context_type: "memory",
    display_name: "项目偏好：使用 PostgreSQL",
    abstract: "用户偏好使用 PostgreSQL 作为数据库",
    match_reason: "包含关键词 PostgreSQL",
    visibility: "user_private",
    memory_type: "preference",
  },
  {
    context_type: "resource",
    display_name: "数据库设计文档",
    abstract: "v0.1 数据模型与迁移说明",
    match_reason: "包含关键词 数据库",
    visibility: "user_private",
    ref_id: RESOURCE_ID,
  },
  {
    context_type: "skill",
    display_name: "代码审查助手",
    abstract: "用于代码审查的 Skill",
    match_reason: "包含关键词 审查",
    visibility: "account_shared",
    ref_id: SKILL_ID,
  },
];

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

/** Guard 为异步 beforeLoad：先等待页面挂载完成。 */
async function renderSearchPage(path = "/app/search") {
  const router = renderRouterAt(path);
  await screen.findByTestId("search-form");
  return router;
}

async function submitSearch(fetchMock: ReturnType<typeof vi.fn>) {
  fireEvent.change(screen.getByTestId("search-query"), { target: { value: "postgresql" } });
  fireEvent.click(screen.getByTestId("search-submit"));
  await waitFor(() => {
    expect(
      fetchMock.mock.calls.some(([u]) => String(u).includes("/search/")),
    ).toBe(true);
  });
}

describe("统一检索页（11 §69，AC②③④）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/search/find")) {
        return jsonResponse(200, { status: "ok", result: { items: HITS } });
      }
      if (url.includes("/search/search")) {
        return jsonResponse(200, { status: "ok", result: { items: HITS } });
      }
      if (url.includes("/sessions")) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: "33333333-4444-4555-8666-777777777777",
                client_name: "Codex",
                title: "Codex #abcdef12",
                sync_status: "active",
                commit_count: 1,
                message_count: 5,
                last_sync_at: null,
                updated_at: "2026-08-19T10:00:00Z",
                created_at: "2026-08-18T10:00:00Z",
              },
            ],
            next_cursor: null,
          },
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

  it("AC②：默认快速检索，检索前不加载 Session 列表；表单含模式/类型/标签/时间筛选", async () => {
    await renderSearchPage();
    expect(screen.getByTestId("search-mode")).toHaveValue("find");
    expect(screen.queryByTestId("search-session-select")).not.toBeInTheDocument();
    // 默认模式不加载 Session
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/sessions"))).toBe(false);
    expect(screen.getByTestId("search-context-type")).toBeInTheDocument();
    expect(screen.getByTestId("search-tags")).toBeInTheDocument();
    expect(screen.getByTestId("search-since")).toBeInTheDocument();
    expect(screen.getByTestId("search-until")).toBeInTheDocument();
  });

  it("AC②：切换到结合会话检索才加载自己的未删除 Session，请求体带 session_id", async () => {
    await renderSearchPage();
    fireEvent.change(screen.getByTestId("search-mode"), { target: { value: "with-session" } });
    await waitFor(() => {
      expect(screen.getByTestId("search-session-select")).toBeInTheDocument();
    });
    const sessionCall = fetchMock.mock.calls.find(([u]) => String(u).includes("/sessions"));
    expect(sessionCall).toBeDefined();
    const sessionCallBody = (sessionCall![1] as RequestInit).method;
    expect(sessionCallBody).toBe("GET"); // 仅只读列表，无请求体
    await screen.findByText("Codex #abcdef12");

    fireEvent.change(screen.getByTestId("search-session-select"), {
      target: { value: "33333333-4444-4555-8666-777777777777" },
    });
    fireEvent.change(screen.getByTestId("search-query"), { target: { value: "hello" } });
    fireEvent.click(screen.getByTestId("search-submit"));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([u]) => String(u).includes("/search/search")),
      ).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/search/search"));
    const body = JSON.parse(String((call![1] as RequestInit).body));
    expect(body.session_id).toBe("33333333-4444-4555-8666-777777777777");
  });

  it("AC②：未选 Session 提交结合会话检索 → 前端拦截提示", async () => {
    await renderSearchPage();
    fireEvent.change(screen.getByTestId("search-mode"), { target: { value: "with-session" } });
    await screen.findByTestId("search-session-select");
    fireEvent.change(screen.getByTestId("search-query"), { target: { value: "hello" } });
    fireEvent.click(screen.getByTestId("search-submit"));
    expect(await screen.findByTestId("search-error")).toHaveTextContent("选择");
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/search/"))).toBe(false);
  });

  it("AC③：快速检索请求体仅含白名单字段（含标签/时间筛选），无调试字段", async () => {
    await renderSearchPage();
    fireEvent.change(screen.getByTestId("search-query"), { target: { value: "postgresql" } });
    fireEvent.change(screen.getByTestId("search-context-type"), { target: { value: "resource" } });
    fireEvent.change(screen.getByTestId("search-tags"), { target: { value: "project=openviking" } });
    fireEvent.change(screen.getByTestId("search-since"), { target: { value: "2026-08-01" } });
    fireEvent.change(screen.getByTestId("search-until"), { target: { value: "2026-08-31" } });
    fireEvent.click(screen.getByTestId("search-submit"));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([u]) => String(u).includes("/search/find")),
      ).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/search/find"));
    const body = JSON.parse(String((call![1] as RequestInit).body));
    expect(body).toEqual({
      query: "postgresql",
      context_type: "resource",
      tags: ["project=openviking"],
      since: "2026-08-01",
      until: "2026-08-31",
    });
    for (const key of ["target_uri", "filter", "score_threshold", "level", "include_provenance", "limit", "node_limit", "time_field"]) {
      expect(Object.keys(body)).not.toContain(key);
    }
  });

  it("AC④：结果列表展示类型/名称/归属/摘要；当前筛选显示在结果区上方", async () => {
    await renderSearchPage();
    fireEvent.change(screen.getByTestId("search-tags"), { target: { value: "project=openviking" } });
    await submitSearch(fetchMock);
    expect(await screen.findByTestId("search-hit-list")).toBeInTheDocument();
    expect(screen.getByText("数据库设计文档")).toBeInTheDocument();
    expect(screen.getByText("代码审查助手")).toBeInTheDocument();
    expect(screen.getByText("项目偏好：使用 PostgreSQL")).toBeInTheDocument();
    expect(screen.getAllByText("我的").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Account 共享").length).toBeGreaterThan(0);
    expect(screen.getByTestId("search-filters-text")).toHaveTextContent("标签=project=openviking");
  });

  it("AC④：不展示 URI/Score/层级/Query Plan/Provenance/Relations", async () => {
    await renderSearchPage();
    await submitSearch(fetchMock);
    await screen.findByTestId("search-hit-list");
    const page = document.body.textContent ?? "";
    for (const forbidden of ["viking://", "score", "L0", "L1", "L2", "query_plan", "Query Plan", "provenance", "Provenance", "relations", "Relations"]) {
      expect(page).not.toContain(forbidden);
    }
  });

  it("AC④：Resource/Skill 结果跳产品详情（跳转参数契约 P3-E1 冻结：URL 仅产品 ID）", async () => {
    let router = await renderSearchPage();
    await submitSearch(fetchMock);
    await screen.findByTestId("search-hit-list");
    fireEvent.click(screen.getAllByTestId("search-hit-resource")[0]);
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(
        "/app/resources/private/11111111-2222-4333-8444-555555555555",
      );
    });

    cleanup();
    fetchMock.mockClear();
    router = await renderSearchPage();
    await submitSearch(fetchMock);
    await screen.findByTestId("search-hit-list");
    fireEvent.click(screen.getAllByTestId("search-hit-skill")[0]);
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(
        "/app/skills/shared/22222222-3333-4444-8555-666666666666",
      );
    });
  });

  it("AC④：Memory 只读抽屉展示类型/摘要/匹配原因；无编辑/删除/下载；无二次读取请求", async () => {
    await renderSearchPage();
    await submitSearch(fetchMock);
    await screen.findByTestId("search-hit-list");
    fireEvent.click(screen.getAllByTestId("search-hit-memory")[0]);
    const drawer = await screen.findByTestId("memory-drawer");
    expect(drawer).toHaveTextContent("preference");
    expect(drawer).toHaveTextContent("用户偏好使用 PostgreSQL 作为数据库");
    expect(drawer).toHaveTextContent("包含关键词 PostgreSQL");
    // 无编辑/删除/恢复/下载动作按钮（仅提示文案，无任何动作入口）
    for (const action of ["编辑", "删除", "恢复", "下载"]) {
      expect(within(drawer).queryByRole("button", { name: new RegExp(action) })).toBeNull();
    }
    // Memory 抽屉不额外调用 content/read（11 §69.4）
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/content/"))).toBe(false);
  });

  it("Session 已删除/无权限 → SESSION_NOT_FOUND 提示，不泄露对象归属", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/search/search")) {
        return jsonResponse(404, { status: "error", error: { code: "SESSION_NOT_FOUND" } });
      }
      if (url.includes("/sessions")) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: "33333333-4444-4555-8666-777777777777",
                client_name: "Codex",
                title: "Codex #abcdef12",
                sync_status: "active",
                commit_count: 1,
                message_count: 5,
                last_sync_at: null,
                updated_at: "2026-08-19T10:00:00Z",
                created_at: "2026-08-18T10:00:00Z",
              },
            ],
            next_cursor: null,
          },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    await renderSearchPage();
    fireEvent.change(screen.getByTestId("search-mode"), { target: { value: "with-session" } });
    await screen.findByText("Codex #abcdef12");
    fireEvent.change(screen.getByTestId("search-session-select"), {
      target: { value: "33333333-4444-4555-8666-777777777777" },
    });
    fireEvent.change(screen.getByTestId("search-query"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("search-submit"));
    expect(await screen.findByTestId("search-error")).toHaveTextContent("不存在或已被删除");
  });

  it("检索引擎不可用 → SEARCH_UNAVAILABLE 可重试错误", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/search/find")) {
        return jsonResponse(503, { status: "error", error: { code: "SEARCH_UNAVAILABLE" } });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    await renderSearchPage();
    fireEvent.change(screen.getByTestId("search-query"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("search-submit"));
    expect(await screen.findByTestId("search-error")).toHaveTextContent("检索引擎暂不可用");
  });
});
