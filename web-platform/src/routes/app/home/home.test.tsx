/**
 * 首页集成测试（06 §13.7，05 §12.5 dashboard，P3-E3 AC①⑨）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, setCsrfToken, type AuthMeResult } from "@/features/auth/auth-state";

const me: AuthMeResult = {
  account: { id: "acc-1", code: "acme", name: "Acme" },
  user: { id: "u-1", ov_user_id: "ov-u-1", display_name: "Alice", email: "alice@example.com" },
  roles: ["user"],
  permissions: ["session.read.self", "task.read.self"],
  can_switch_account: false,
  csrf_token: null,
};

const DASHBOARD = {
  generated_at: "2026-08-19T12:00:00Z",
  summary: {
    sessions: 2,
    messages: 12,
    resources_private: 3,
    skills_private: 1,
    resources_account_shared: 4,
    skills_account_shared: 2,
    sessions_in_recycle: 1,
    commits_by_phase2: { pending: 0, running: 1, completed: 5, failed: 1 },
    sessions_with_commit_failed: 1,
  },
  recent_activity: [
    {
      id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      client_name: "Codex",
      sync_status: "active",
      commit_count: 2,
      message_count: 7,
      updated_at: "2026-08-19T10:00:00Z",
    },
  ],
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

describe("首页（06 §13.7，AC①⑨）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input).includes("/dashboard")) {
        return jsonResponse(200, { status: "ok", result: DASHBOARD });
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

  it("AC①：首页仅请求 /dashboard，展示内容数量/最近 Session/失败摘要", async () => {
    renderRouterAt("/app");
    await screen.findByTestId("home-page");
    expect(screen.getByTestId("count-sessions")).toHaveTextContent("2");
    expect(screen.getByTestId("count-messages")).toHaveTextContent("12");
    expect(screen.getByTestId("count-resources")).toHaveTextContent("3");
    expect(screen.getByTestId("count-skills")).toHaveTextContent("1");
    expect(screen.getByTestId("count-shared-resources")).toHaveTextContent("4");
    expect(screen.getByTestId("count-shared-skills")).toHaveTextContent("2");
    // 最近 Session（客户端名 + 短标识）
    expect(screen.getByText("Codex #aaaaaaaa")).toBeInTheDocument();
    // 失败摘要
    expect(screen.getByTestId("failures-commits")).toHaveTextContent("1 个 Commit");
    expect(screen.getByTestId("failures-sessions")).toHaveTextContent("1 个 Session");
    // 未请求任何底层状态端点（Queue/锁/模型/VectorDB）
    const allUrls = fetchMock.mock.calls.map(([u]) => String(u));
    for (const url of allUrls) {
      for (const forbidden of ["/queue", "/tasks", "/observer", "/monitoring", "/model", "/vectordb", "/vdb"]) {
        expect(url).not.toContain(forbidden);
      }
    }
  });

  it("AC①：页面不展示 Queue/锁/模型/VectorDB 等底层状态文案", async () => {
    renderRouterAt("/app");
    await screen.findByTestId("home-page");
    const page = document.body.textContent ?? "";
    for (const forbidden of ["Queue", "队列", "锁", "模型", "VectorDB", "向量库", "embedding"]) {
      expect(page).not.toContain(forbidden);
    }
  });

  it("AC⑨：首页与全局导航无 Account 切换入口", async () => {
    renderRouterAt("/app");
    await screen.findByTestId("home-page");
    for (const label of ["切换 Account", "切换账号", "switch account"]) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/accounts"))).toBe(false);
    // 侧边栏与顶栏均无 Account 切换控件
    const shell = document.querySelector('[data-testid="shell-app"]');
    expect(shell).not.toBeNull();
    expect(shell!.textContent).not.toContain("切换");
  });

  it("无失败时展示「当前没有失败的处理任务」", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input).includes("/dashboard")) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            ...DASHBOARD,
            summary: {
              ...DASHBOARD.summary,
              commits_by_phase2: { pending: 0, running: 0, completed: 5, failed: 0 },
              sessions_with_commit_failed: 0,
            },
          },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/app");
    await screen.findByTestId("home-page");
    expect(await screen.findByText("当前没有失败的处理任务。")).toBeInTheDocument();
    expect(screen.queryByTestId("failures-commits")).not.toBeInTheDocument();
  });
});
