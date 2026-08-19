/**
 * 个人设置页集成测试（13 §81，P3-E2 AC⑧⑨）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import {
  getCsrfToken,
  setAuthStateForTest,
  setCsrfToken,
  type AuthMeResult,
} from "@/features/auth/auth-state";
import { leaveToLogin } from "@/features/auth/session";

vi.mock("@/features/auth/session", () => ({
  leaveToLogin: vi.fn(),
}));

const me: AuthMeResult = {
  account: { id: "acc-1", code: "acme", name: "Acme" },
  user: { id: "u-1", ov_user_id: "ov-u-1", display_name: "Alice", email: "alice@example.com" },
  roles: ["user"],
  permissions: ["session.read.self", "credential.read.self"],
  can_switch_account: false,
  csrf_token: null,
};

const SESSION_SUMMARY = {
  session_id: "s-1",
  last_seen_at: "2026-08-19T10:00:00Z",
  created_at: "2026-08-18T10:00:00Z",
  ip_hash: "ab12cd34",
  user_agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X) …",
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

describe("个人设置页（13 §81，AC⑧⑨）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/auth/me/session-summary")) {
        return jsonResponse(200, { status: "ok", result: SESSION_SUMMARY });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me, sessionExpired: false });
  });

  afterEach(() => {
    setCsrfToken(null);
  });

  it("基本信息只读展示：显示名/邮箱/所属 Account（81.2），无 Account 切换入口", async () => {
    renderRouterAt("/app/profile");
    expect(await screen.findByTestId("profile-display-name")).toHaveTextContent("Alice");
    expect(screen.getByTestId("profile-email")).toHaveTextContent("alice@example.com");
    expect(screen.getByTestId("profile-account")).toHaveTextContent("Acme");
    expect(screen.queryByText(/切换 Account|切换账号/)).not.toBeInTheDocument();
  });

  it("登录设备区：仅当前 Session 脱敏摘要（IP hash + User-Agent 截断），无单会话列表（81.4，AC⑨）", async () => {
    renderRouterAt("/app/profile");
    const summary = await screen.findByTestId("profile-session-summary");
    expect(summary).toHaveTextContent("ab12cd34");
    expect(summary).toHaveTextContent("Mozilla/5.0");
    expect(screen.queryByText(/session_id|s-1/)).toBeNull();
    expect(screen.queryByText(/全部会话|会话列表|单会话撤销/)).not.toBeInTheDocument();
  });

  it("改密必填旧密码：留空提示（81.3，AC⑧）", async () => {
    renderRouterAt("/app/profile");
    await screen.findByTestId("profile-session-summary");
    fireEvent.change(screen.getByTestId("password-new"), { target: { value: "new-password-123" } });
    fireEvent.change(screen.getByTestId("password-confirm"), { target: { value: "new-password-123" } });
    fireEvent.submit(screen.getByTestId("password-form"));
    expect(await screen.findByTestId("password-error")).toHaveTextContent("请输入当前密码");
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/password/change"))).toBe(false);
  });

  it("改密：新旧密码不一致前端拦截（不发请求）", async () => {
    renderRouterAt("/app/profile");
    await screen.findByTestId("profile-session-summary");
    fireEvent.change(screen.getByTestId("password-old"), { target: { value: "old-password" } });
    fireEvent.change(screen.getByTestId("password-new"), { target: { value: "new-password-123" } });
    fireEvent.change(screen.getByTestId("password-confirm"), { target: { value: "different-456" } });
    fireEvent.click(screen.getByTestId("password-submit"));
    expect(await screen.findByTestId("password-error")).toHaveTextContent("两次输入的新密码不一致");
  });

  it("改密成功：提交 old+new、Session 轮换（新 CSRF 入内存）、提示重新登录（81.3，AC⑧）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/auth/me/session-summary")) {
        return jsonResponse(200, { status: "ok", result: SESSION_SUMMARY });
      }
      if (url.includes("/password/change")) {
        return jsonResponse(200, {
          status: "ok",
          result: { csrf_token: "csrf-rotated", session_rotated: true },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/app/profile");
    await screen.findByTestId("profile-session-summary");
    fireEvent.change(screen.getByTestId("password-old"), { target: { value: "old-password" } });
    fireEvent.change(screen.getByTestId("password-new"), { target: { value: "new-password-123" } });
    fireEvent.change(screen.getByTestId("password-confirm"), { target: { value: "new-password-123" } });
    fireEvent.click(screen.getByTestId("password-submit"));

    expect(await screen.findByTestId("password-changed-notice")).toBeInTheDocument();
    const changeCall = fetchMock.mock.calls.find(([u]) => String(u).includes("/password/change"));
    expect(changeCall).toBeDefined();
    const body = JSON.parse(String((changeCall![1] as RequestInit).body));
    expect(body.old_password).toBe("old-password");
    expect(body.new_password).toBe("new-password-123");
    expect(getCsrfToken()).toBe("csrf-rotated");

    fireEvent.click(screen.getByText("重新登录"));
    expect(leaveToLogin).toHaveBeenCalledWith("password_changed");
  });

  it("改密错误旧密码（LOGIN_FAILED）→ 当前密码不正确", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/auth/me/session-summary")) {
        return jsonResponse(200, { status: "ok", result: SESSION_SUMMARY });
      }
      if (url.includes("/password/change")) {
        return jsonResponse(401, { status: "error", error: { code: "LOGIN_FAILED" } });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/app/profile");
    await screen.findByTestId("profile-session-summary");
    fireEvent.change(screen.getByTestId("password-old"), { target: { value: "wrong-password" } });
    fireEvent.change(screen.getByTestId("password-new"), { target: { value: "new-password-123" } });
    fireEvent.change(screen.getByTestId("password-confirm"), { target: { value: "new-password-123" } });
    fireEvent.click(screen.getByTestId("password-submit"));
    expect(await screen.findByTestId("password-error")).toHaveTextContent("当前密码不正确");
  });

  it("退出所有设备：确认弹窗 → logout-all → 跳登录页（81.4，AC⑧）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/auth/me/session-summary")) {
        return jsonResponse(200, { status: "ok", result: SESSION_SUMMARY });
      }
      if (url.includes("/logout-all")) {
        return jsonResponse(200, { status: "ok", result: { sessions_revoked: 2 } });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/app/profile");
    await screen.findByTestId("profile-session-summary");
    fireEvent.click(screen.getByTestId("logout-all-button"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("logout-all-confirm"));

    await waitFor(() => {
      expect(leaveToLogin).toHaveBeenCalledWith("logged_out");
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/logout-all"));
    expect(call).toBeDefined();
  });

  it("Profile 子页不进入路由守卫（登录即可访问），API Keys/Connections 入口存在", async () => {
    renderRouterAt("/app/profile/api-keys");
    expect(await screen.findByTestId("api-keys-page")).toBeInTheDocument();
    cleanup();
    renderRouterAt("/app/profile/connections");
    expect(await screen.findByTestId("connections-page")).toBeInTheDocument();
  });
});
