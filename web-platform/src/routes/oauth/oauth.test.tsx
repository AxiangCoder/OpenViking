/**
 * OAuth 授权页集成测试（13 §83.2–83.3，06 §13.8，P3-E2 AC⑤⑥）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import {
  setAuthStateForTest,
  setCsrfToken,
  type AuthMeResult,
} from "@/features/auth/auth-state";

const me: AuthMeResult = {
  account: { id: "acc-1", name: "Acme" },
  user: { id: "u-1", ov_user_id: "ov-u-1" },
  roles: ["user"],
  permissions: ["integration.oauth.authorize.self"],
  can_switch_account: false,
  csrf_token: null,
};

const PENDING_INFO = {
  client_id: "client-registered-id",
  client_name: "Codex (registered)",
  redirect_uri_host: "codex.example.com",
  scopes: ["mcp"],
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

describe("MCP OAuth 授权页（13 §83，AC⑤⑥）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async () =>
      jsonResponse(200, { status: "ok", result: PENDING_INFO }),
    );
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me, sessionExpired: false });
  });

  afterEach(() => {
    setCsrfToken(null);
  });

  it("consent：展示服务端登记的 Client 名称/Client ID/回调 host/Scope/当前 Account，并提示权限限制（83.2，AC⑤）", async () => {
    renderRouterAt("/oauth/consent?pending=pending-1");
    expect(await screen.findByTestId("oauth-client-name")).toHaveTextContent("Codex (registered)");
    expect(screen.getByTestId("oauth-client-id")).toHaveTextContent("client-registered-id");
    expect(screen.getByTestId("oauth-redirect-host")).toHaveTextContent("codex.example.com");
    expect(screen.getByTestId("oauth-scopes")).toHaveTextContent("mcp");
    expect(screen.getByTestId("oauth-account")).toHaveTextContent("Acme");
    expect(screen.getByTestId("oauth-warning")).toHaveTextContent("该客户端将以你的身份运行，并受你当前角色权限限制");
  });

  it("consent：pending 读取后从地址栏剥离，不驻留 URL（AC⑥）", async () => {
    renderRouterAt("/oauth/consent?pending=pending-1");
    await screen.findByTestId("oauth-client-name");
    expect(globalThis.location.href).not.toContain("pending-1");
  });

  it("consent：允许 → POST authorize（pending_id + 登录 Session），无任何 Key/密码输入（AC⑤）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/oauth/pending/pending-1")) {
        return jsonResponse(200, { status: "ok", result: PENDING_INFO });
      }
      if (url.includes("/oauth/authorize") && method === "POST") {
        return jsonResponse(200, { status: "ok", result: {} });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });
    renderRouterAt("/oauth/consent?pending=pending-1");
    await screen.findByTestId("oauth-client-name");

    expect(screen.queryByTestId("oauth-approve")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("oauth-approve"));

    expect(await screen.findByTestId("oauth-done")).toHaveTextContent("授权成功");
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/oauth/authorize"));
    const body = JSON.parse(String((call![1] as RequestInit).body));
    expect(body).toEqual({ pending_id: "pending-1", decision: "approve" });
    // 页面无密码/Key 输入
    expect(screen.queryByPlaceholderText(/password|api key/i)).not.toBeInTheDocument();
  });

  it("consent：拒绝 → POST authorize decision=reject → 提示已拒绝", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/oauth/pending/pending-1")) {
        return jsonResponse(200, { status: "ok", result: PENDING_INFO });
      }
      if (url.includes("/oauth/authorize") && method === "POST") {
        return jsonResponse(200, { status: "ok", result: {} });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });
    renderRouterAt("/oauth/consent?pending=pending-1");
    await screen.findByTestId("oauth-client-name");
    fireEvent.click(screen.getByTestId("oauth-reject"));
    expect(await screen.findByTestId("oauth-done")).toHaveTextContent("已拒绝");
  });

  it("consent：缺少 pending → 提示重新发起，不发请求", async () => {
    renderRouterAt("/oauth/consent");
    expect(await screen.findByTestId("oauth-error")).toHaveTextContent("授权请求不完整或已失效");
    expect(fetchMock.mock.calls.length).toBe(0);
  });

  it("consent：pending 失效（404）→ 过期提示", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } }),
    );
    renderRouterAt("/oauth/consent?pending=stale");
    expect(await screen.findByTestId("oauth-expired")).toBeInTheDocument();
  });

  it("verify：输入 display code → 展示待授权信息 → 允许；code 只进请求体、不进 URL（AC⑤⑥）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/oauth/authorize") && method === "POST") {
        const body = JSON.parse(String(init?.body));
        if (body.decision === "approve") {
          return jsonResponse(200, { status: "ok", result: {} });
        }
        return jsonResponse(200, {
          status: "ok",
          result: {
            client_id: "client-registered-id",
            client_name: "Codex (registered)",
            redirect_uri_host: "codex.example.com",
            scopes: ["mcp"],
          },
        });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });
    renderRouterAt("/oauth/verify");
    const input = await screen.findByTestId("oauth-verify-code");
    expect(input).toBeInTheDocument();

    fireEvent.change(input, { target: { value: "ABC123" } });
    fireEvent.click(screen.getByTestId("oauth-verify-lookup"));

    expect(await screen.findByTestId("oauth-verify-client")).toHaveTextContent("Codex (registered)");
    expect(screen.getByTestId("oauth-verify-client-id")).toHaveTextContent("client-registered-id");
    expect(screen.getByTestId("oauth-verify-warning")).toHaveTextContent("该客户端将以你的身份运行");
    expect(globalThis.location.href).not.toContain("ABC123");

    fireEvent.click(screen.getByTestId("oauth-verify-approve"));
    expect(await screen.findByTestId("oauth-verify-done")).toHaveTextContent("授权成功");

    const authorizeCalls = fetchMock.mock.calls.filter(([u]) => String(u).includes("/oauth/authorize"));
    expect(authorizeCalls.length).toBe(2);
    const previewBody = JSON.parse(String((authorizeCalls[0]![1] as RequestInit).body));
    const approveBody = JSON.parse(String((authorizeCalls[1]![1] as RequestInit).body));
    expect(previewBody).toEqual({ code: "ABC123" });
    expect(approveBody).toEqual({ code: "ABC123", decision: "approve" });
  });

  it("verify：不要求任何 Key/密码输入（AC⑤）", async () => {
    renderRouterAt("/oauth/verify");
    await screen.findByTestId("oauth-verify-code");
    expect(screen.queryByPlaceholderText(/password|api key/i)).not.toBeInTheDocument();
  });
});
