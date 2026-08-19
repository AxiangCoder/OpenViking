/**
 * 登录页集成测试（13 §80，P3-E2 AC①②）。
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

const userMe: AuthMeResult = {
  account: { id: "acc-1" },
  user: { id: "u-1", ov_user_id: "ov-u-1" },
  roles: ["user"],
  permissions: ["session.read.self"],
  can_switch_account: false,
  csrf_token: null,
};

const psaMe: AuthMeResult = {
  ...userMe,
  account: null,
  roles: ["platform_super_admin"],
  permissions: ["account.read.platform"],
};

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function renderRouterAt(path: string) {
  const router = createAppRouter({ history: createMemoryHistory({ initialEntries: [path] }) });
  const queryClient = new QueryClient();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router, unmount: result.unmount };
}

async function submitLogin(email: string, password: string) {
  await screen.findByTestId("login-email");
  fireEvent.change(screen.getByTestId("login-email"), { target: { value: email } });
  fireEvent.change(screen.getByTestId("login-password"), { target: { value: password } });
  fireEvent.click(screen.getByTestId("login-submit"));
}

describe("登录页（13 §80，AC①②）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "unauthenticated", me: null, sessionExpired: false });
  });

  afterEach(() => {
    setCsrfToken(null);
  });

  it("只提供邮箱+密码，无注册/找回密码/企业登录入口（80.2，AC①）", async () => {
    renderRouterAt("/login");
    expect(await screen.findByTestId("login-email")).toBeInTheDocument();
    expect(screen.getByTestId("login-password")).toBeInTheDocument();
    expect(screen.queryByText(/注册|找回密码|企业登录|激活/i)).not.toBeInTheDocument();
  });

  it("登录成功：POST /auth/login → 内存 CSRF → 刷新 /auth/me → 回跳目标（AC①）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/auth/login")) {
        return jsonResponse(200, { status: "ok", result: { csrf_token: "csrf-new" } });
      }
      if (url.includes("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: userMe });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });

    renderRouterAt("/login?redirect=/app/sessions");
    await submitLogin("alice@example.com", "password-123");

    await waitFor(() => {
      expect(screen.getByTestId("shell-app")).toBeInTheDocument();
    });
    const loginCall = fetchMock.mock.calls.find(([u]) => String(u).includes("/auth/login"));
    expect(loginCall).toBeDefined();
    expect((loginCall![1] as RequestInit).body).toContain('"email":"alice@example.com"');
    expect((loginCall![1] as RequestInit).body).toContain('"password":"password-123"');
    expect(getCsrfToken()).toBe("csrf-new");
  });

  it("PSA 登录后进入默认入口 /platform/accounts（80.1，AC②）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/auth/login")) {
        return jsonResponse(200, { status: "ok", result: { csrf_token: "csrf-p" } });
      }
      return jsonResponse(200, { status: "ok", result: psaMe });
    });

    renderRouterAt("/login");
    await submitLogin("root@example.com", "password-123");

    await waitFor(() => {
      expect(screen.getByTestId("shell-platform")).toBeInTheDocument();
    });
  });

  it("凭证错误：统一「邮箱或密码不正确」，不发 /auth/me（防枚举，AC①）", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(401, { status: "error", error: { code: "LOGIN_FAILED" } }),
    );

    renderRouterAt("/login");
    await submitLogin("alice@example.com", "wrong");

    expect(await screen.findByTestId("login-error")).toHaveTextContent("邮箱或密码不正确");
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/auth/me"))).toBe(false);
  });

  it("限流（Retry-After）：提示稍后重试，按钮进入冷却并禁用（80.5）", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(
        401,
        { status: "error", error: { code: "LOGIN_FAILED" } },
        { "Retry-After": "30" },
      ),
    );

    renderRouterAt("/login");
    await submitLogin("alice@example.com", "wrong");

    expect(await screen.findByTestId("login-error")).toHaveTextContent("尝试过多，请稍后再试");
    const submit = screen.getByTestId("login-submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect(submit.textContent).toContain("30");
  });

  it("ACCOUNT_SUSPENDED/USER_DISABLED/PROVISIONING_* 状态文案（80.5）", async () => {
    const cases: Array<[string, string]> = [
      ["ACCOUNT_SUSPENDED", "账号已暂停，请联系管理员"],
      ["USER_DISABLED", "账号已停用，请联系管理员"],
      ["PROVISIONING_PENDING", "账号正在开通，请联系管理员"],
      ["PROVISIONING_FAILED", "账号开通失败，请联系管理员"],
    ];
    for (const [code, expected] of cases) {
      fetchMock.mockImplementation(async () =>
        jsonResponse(401, { status: "error", error: { code } }),
      );
      renderRouterAt("/login");
      await submitLogin("alice@example.com", "x");
      expect(await screen.findByTestId("login-error")).toHaveTextContent(expected);
      cleanup();
    }
  });

  it("网络错误：保留输入并显示可重试文案（80.5）", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    renderRouterAt("/login");
    await submitLogin("alice@example.com", "x");

    expect(await screen.findByTestId("login-error")).toHaveTextContent("网络异常");
    expect((screen.getByTestId("login-email") as HTMLInputElement).value).toBe("alice@example.com");
  });

  it("已登录访问 /login → 跳默认入口 /app（AC②）", async () => {
    setAuthStateForTest({ status: "authenticated", me: userMe, sessionExpired: false });
    renderRouterAt("/login");
    await waitFor(() => {
      expect(screen.getByTestId("shell-app")).toBeInTheDocument();
    });
  });

  it("已登录访问 /login?redirect=… → 回跳该目标（AC②）", async () => {
    setAuthStateForTest({ status: "authenticated", me: userMe, sessionExpired: false });
    renderRouterAt("/login?redirect=/app/sessions");
    await waitFor(() => {
      expect(screen.getByTestId("shell-app")).toBeInTheDocument();
    });
  });

  it("会话过期提示（AC③ 既有语义保留）", async () => {
    setAuthStateForTest({ status: "unauthenticated", me: null, sessionExpired: true });
    renderRouterAt("/login?reason=session_expired");
    expect(await screen.findByText(/登录会话已过期/)).toBeInTheDocument();
  });

  it("退出所有设备后回登录页提示（logged_out）", async () => {
    renderRouterAt("/login?reason=logged_out");
    expect(await screen.findByText(/已安全退出所有设备/)).toBeInTheDocument();
  });
});
