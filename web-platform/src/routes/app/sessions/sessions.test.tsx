/**
 * Sessions 页集成测试（11 §70–§72，P3-E3 AC⑤⑥⑦）。
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
  permissions: ["session.read.self", "session.delete.self"],
  can_switch_account: false,
  csrf_token: null,
};

const SESSION_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";

const LIST_ITEM = {
  id: SESSION_ID,
  client_name: "Codex",
  title: "Codex #aabbccdd",
  sync_status: "active",
  commit_count: 2,
  message_count: 3,
  last_sync_at: "2026-08-19T10:00:00Z",
  updated_at: "2026-08-19T10:00:00Z",
  created_at: "2026-08-18T10:00:00Z",
};

const MESSAGES = [
  { role: "user", content: "帮我分析一下数据模型", sequence: 1, turn_id: null, created_at: "2026-08-18T10:01:00Z" },
  { role: "assistant", content: "好的，数据模型包含…", sequence: 2, turn_id: null, created_at: "2026-08-18T10:02:00Z" },
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

describe("Sessions 页（11 §70–72，AC⑤⑥⑦）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let impactResponse: unknown;

  beforeEach(() => {
    vi.clearAllMocks();
    impactResponse = {
      session_id: SESSION_ID,
      commit_count: 2,
      totals: { added: 1, updated: 0, deleted: 1 },
      items: [
        {
          commit_id: "c-1",
          commit_number: 1,
          phase2_status: "completed",
          phase2_error: null,
          message_count_at_commit: 3,
          created_at: "2026-08-18T11:00:00Z",
          completed_at: "2026-08-18T11:01:00Z",
          has_operations: true,
          diffs: [
            { memory_type: "preference", action: "add", after: "喜欢 PostgreSQL" },
            { memory_type: "preference", action: "delete", before: "旧偏好" },
          ],
        },
        {
          commit_id: "c-2",
          commit_number: 2,
          phase2_status: "pending",
          phase2_error: null,
          message_count_at_commit: 4,
          created_at: "2026-08-19T09:00:00Z",
          completed_at: null,
          has_operations: false,
          diffs: [],
        },
      ],
    };
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/sessions/") && url.includes("/memory-impact")) {
        return jsonResponse(200, { status: "ok", result: impactResponse });
      }
      if (url.includes("/sessions/") && url.includes("/messages")) {
        return jsonResponse(200, { status: "ok", result: { items: MESSAGES, next_cursor: null } });
      }
      if (url.includes("/sessions/")) {
        return jsonResponse(200, { status: "ok", result: { ...LIST_ITEM, pending_tokens: 0 } });
      }
      if (url.includes("/sessions")) {
        return jsonResponse(200, { status: "ok", result: { items: [LIST_ITEM], next_cursor: null } });
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

  it("AC⑤：双栏列表，无消息输入框/Composer/发送按钮；标题=客户端名+短标识", async () => {
    renderRouterAt("/app/sessions");
    await screen.findByTestId("sessions-sidebar");
    expect(await screen.findByText("Codex #aabbccdd")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/输入消息|发送消息/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /发送|停止生成|新建会话/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/模型/)).not.toBeInTheDocument();
    // 标题不出现 Viking URI / 完整 Session ID / 内部标识
    const page = document.body.textContent ?? "";
    expect(page).not.toContain("viking://");
    expect(page).not.toContain(SESSION_ID);
  });

  it("AC⑤：点击 Session 加载详情与完整消息历史，消息只读且无 Tool 原始 JSON", async () => {
    renderRouterAt("/app/sessions");
    await screen.findByText("Codex #aabbccdd");
    fireEvent.click(screen.getByTestId(`session-item-${SESSION_ID}`));
    expect(await screen.findByTestId("sessions-detail")).toBeInTheDocument();
    expect(screen.getByTestId("session-client-name")).toHaveTextContent("Codex");
    expect(screen.getByTestId("session-short-id")).toHaveTextContent("#aaaaaaaa");
    await waitFor(() => {
      expect(screen.getByTestId("session-message-1")).toHaveTextContent("帮我分析一下数据模型");
      expect(screen.getByTestId("session-message-2")).toHaveTextContent("好的，数据模型包含…");
    });
    // 消息 DTO 无 tool JSON/URI 字段可渲染（Tool 卡片脱敏，AC⑤）
    const messagesBlock = screen.getByTestId("session-messages");
    expect(messagesBlock.textContent).not.toContain("tool");
    expect(messagesBlock.textContent).not.toContain("viking://");
    // 无输入框
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("AC⑥：Memory Impact 区分 completed(有变更)/pending；统计与差异正确展示", async () => {
    renderRouterAt("/app/sessions");
    await screen.findByText("Codex #aabbccdd");
    fireEvent.click(screen.getByTestId(`session-item-${SESSION_ID}`));
    const impact = await screen.findByTestId("memory-impact");
    expect(within(impact).getByTestId("memory-impact-added")).toHaveTextContent("1");
    expect(within(impact).getByTestId("memory-impact-deleted")).toHaveTextContent("1");
    // commit 1：completed 且有变更 → 差异展示
    expect(await within(impact).findByTestId("impact-status-1")).toHaveTextContent("已产生变更");
    const diffs = within(impact).getAllByTestId("impact-diff");
    expect(diffs.length).toBe(2);
    expect(diffs[0]).toHaveTextContent("新增");
    expect(diffs[0]).toHaveTextContent("喜欢 PostgreSQL");
    expect(diffs[1]).toHaveTextContent("删除");
    expect(diffs[1]).toHaveTextContent("旧偏好");
    // commit 2：pending → 正在整理记忆
    expect(within(impact).getByTestId("impact-status-2")).toHaveTextContent("正在整理记忆");
    expect(within(impact).getByTestId("memory-impact-pending")).toHaveTextContent("正在整理记忆");
  });

  it("AC⑥：completed 且无变更 → 「本次未产生长期记忆变更」；failed → 可重试状态", async () => {
    impactResponse = {
      session_id: SESSION_ID,
      commit_count: 2,
      totals: { added: 0, updated: 0, deleted: 0 },
      items: [
        {
          commit_id: "c-3",
          commit_number: 1,
          phase2_status: "completed",
          phase2_error: null,
          message_count_at_commit: 3,
          created_at: "2026-08-18T11:00:00Z",
          completed_at: "2026-08-18T11:01:00Z",
          has_operations: false,
          diffs: [],
        },
        {
          commit_id: "c-4",
          commit_number: 2,
          phase2_status: "failed",
          phase2_error: "extract timeout",
          message_count_at_commit: 4,
          created_at: "2026-08-19T09:00:00Z",
          completed_at: null,
          has_operations: false,
          diffs: [],
        },
      ],
    };
    renderRouterAt("/app/sessions");
    await screen.findByText("Codex #aabbccdd");
    fireEvent.click(screen.getByTestId(`session-item-${SESSION_ID}`));
    const impact = await screen.findByTestId("memory-impact");
    expect(await within(impact).findByTestId("impact-status-1")).toHaveTextContent("无变更");
    expect(await within(impact).findByTestId("impact-no-changes")).toHaveTextContent("本次未产生长期记忆变更");
    fireEvent.click(within(impact).getByTestId("impact-commit-2"));
    expect(within(impact).getByTestId("impact-status-2")).toHaveTextContent("整理失败");
    expect(within(impact).getByTestId("impact-failed")).toBeInTheDocument();
  });

  it("AC⑦：软删弹窗展示影响与恢复截止，确认后 DELETE 并从列表移除", async () => {
    renderRouterAt("/app/sessions");
    await screen.findByText("Codex #aabbccdd");
    fireEvent.click(screen.getByTestId(`session-item-${SESSION_ID}`));
    await screen.findByTestId("sessions-detail");
    fireEvent.click(screen.getByTestId("session-delete-button"));
    const dialog = await screen.findByTestId("delete-session-dialog");
    expect(dialog).toHaveTextContent("Codex #aabbccdd");
    expect(dialog).toHaveTextContent("3"); // 消息数量
    expect(dialog).toHaveTextContent("2"); // Commit 数
    expect(dialog).toHaveTextContent("30 天");
    expect(dialog).toHaveTextContent("恢复");
    fireEvent.click(screen.getByTestId("session-delete-confirm"));
    await waitFor(() => {
      expect(screen.queryByText("Codex #aabbccdd")).not.toBeInTheDocument();
    });
    const deleteCall = fetchMock.mock.calls.find(
      ([u, init]) => String(u).endsWith(`/sessions/${SESSION_ID}`) && (init as RequestInit)?.method === "DELETE",
    );
    expect(deleteCall).toBeDefined();
    // 删除后无选中详情
    expect(screen.queryByTestId("sessions-detail")).not.toBeInTheDocument();
  });

  it("无选中 Session 时提示在客户端中连接 OpenViking，不出现聊天入口", async () => {
    renderRouterAt("/app/sessions");
    expect(await screen.findByTestId("sessions-none-selected")).toBeInTheDocument();
    expect(screen.getByText(/不提供消息发送/)).toBeInTheDocument();
  });
});
