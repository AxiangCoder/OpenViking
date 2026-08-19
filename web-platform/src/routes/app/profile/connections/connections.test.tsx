/**
 * OAuth 连接页集成测试（13 §83.1，P3-E2 AC⑤⑦）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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
  account: { id: "acc-1" },
  user: { id: "u-1", ov_user_id: "ov-u-1" },
  roles: ["user"],
  permissions: ["integration.oauth.read.self", "integration.oauth.revoke.self"],
  can_switch_account: false,
  csrf_token: null,
};

const GRANT_A = {
  id: "g-1",
  client_id: "client-abc",
  client_name: "Codex",
  scope: "mcp",
  status: "active",
  created_at: "2026-08-18T10:00:00Z",
  last_used_at: "2026-08-19T09:00:00Z",
};

const GRANT_B = {
  id: "g-2",
  client_id: "client-def",
  client_name: "OpenClaw",
  scope: "mcp",
  status: "active",
  created_at: "2026-08-17T10:00:00Z",
  last_used_at: null,
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

describe("OAuth 连接页（13 §83.1，AC⑤⑦）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async () =>
      jsonResponse(200, { status: "ok", result: [GRANT_A, GRANT_B] }),
    );
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me, sessionExpired: false });
  });

  afterEach(() => {
    setCsrfToken(null);
  });

  it("列表展示 Client 名称/Scope/状态/授权时间/最近使用时间", async () => {
    renderRouterAt("/app/profile/connections");
    expect(await screen.findByTestId("grant-row-g-1")).toHaveTextContent("Codex");
    expect(screen.getByTestId("grant-row-g-1")).toHaveTextContent("mcp");
    expect(screen.getByTestId("grant-row-g-1")).toHaveTextContent("client-abc");
    expect(screen.getByTestId("grant-row-g-2")).toHaveTextContent("OpenClaw");
  });

  it("单独撤销一个 Grant：确认弹窗文案 → DELETE → 不影响其他 Grant（AC⑦）", async () => {
    let revoked = false;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/oauth-grants/g-1") && method === "DELETE") {
        revoked = true;
        return jsonResponse(200, { status: "ok" });
      }
      return jsonResponse(200, {
        status: "ok",
        result: revoked ? [{ ...GRANT_A, status: "revoked" }, GRANT_B] : [GRANT_A, GRANT_B],
      });
    });

    renderRouterAt("/app/profile/connections");
    await screen.findByTestId("grant-row-g-1");
    fireEvent.click(screen.getByTestId("grant-revoke-g-1"));
    expect(screen.getByRole("dialog")).toHaveTextContent("将断开该客户端");
    fireEvent.click(screen.getByTestId("grant-revoke-confirm"));

    await waitFor(() => {
      expect(screen.getByTestId("grant-row-g-1")).toHaveTextContent("revoked");
    });
    expect(screen.getByTestId("grant-row-g-2")).toBeInTheDocument();
    const deleteCall = fetchMock.mock.calls.find(([u, i]) =>
      String(u).includes("/oauth-grants/g-1") && i?.method === "DELETE",
    );
    expect(deleteCall).toBeDefined();
  });

  it("无 Grant 空状态", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(200, { status: "ok", result: [] }));
    renderRouterAt("/app/profile/connections");
    expect(await screen.findByTestId("connections-empty")).toHaveTextContent("尚无已授权的 MCP 客户端");
  });
});
