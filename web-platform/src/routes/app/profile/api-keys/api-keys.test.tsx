/**
 * 个人 API Key 页集成测试（13 §82，P3-E2 AC③④⑦）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
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
  permissions: ["credential.read.self", "credential.create.self", "credential.revoke.self"],
  can_switch_account: false,
  csrf_token: null,
};

const EXISTING_KEY = {
  id: "k-1",
  name: "Codex on MacBook",
  key_last_four: "4f2a",
  status: "active",
  expires_at: "2027-01-01T00:00:00Z",
  last_used_at: "2026-08-19T08:00:00Z",
  created_at: "2026-08-18T08:00:00Z",
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

describe("个人 API Key 页（13 §82，AC③④⑦）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/me/api-keys") && !/api-keys\/[^/]+$/.test(url)) {
        return jsonResponse(200, { status: "ok", result: [EXISTING_KEY] });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me, sessionExpired: false });
  });

  afterEach(() => {
    setCsrfToken(null);
  });

  it("列表只显示元数据与末四位掩码，无完整 Key（82.3，AC③）", async () => {
    renderRouterAt("/app/profile/api-keys");
    const row = await screen.findByTestId("api-key-row-k-1");
    expect(row).toHaveTextContent("Codex on MacBook");
    expect(screen.getByTestId("api-key-mask")).toHaveTextContent("••••••••4f2a");
    expect(screen.queryByText(/ovk_u\./)).not.toBeInTheDocument();
    expect(screen.queryByText("4f2a".concat(""))).not.toBeInTheDocument();
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("ovk_u");
  });

  it("无 Key 空状态说明与创建入口（82.5）", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(200, { status: "ok", result: [] }),
    );
    renderRouterAt("/app/profile/api-keys");
    expect(await screen.findByTestId("api-keys-empty")).toHaveTextContent("可为 Codex、插件或 MCP 客户端创建个人访问凭证");
    expect(screen.getByTestId("api-key-create-form")).toBeInTheDocument();
  });

  it("创建成功：一次性明文只在结果页出现一次，返回列表后不可再取（82.4，AC③）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/me/api-keys") && method === "GET") {
        return jsonResponse(200, { status: "ok", result: [EXISTING_KEY] });
      }
      if (url.includes("/me/api-keys")) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            id: "k-2",
            name: "New Key",
            key_last_four: "9b3c",
            status: "active",
            expires_at: null,
            last_used_at: null,
            created_at: "2026-08-19T09:00:00Z",
            api_key: "ovk_u.pub-123.sec-456-secret",
          },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });

    renderRouterAt("/app/profile/api-keys");
    await screen.findByTestId("api-key-row-k-1");
    fireEvent.change(screen.getByTestId("api-key-name"), { target: { value: "New Key" } });
    fireEvent.click(screen.getByTestId("api-key-create"));

    expect(await screen.findByTestId("api-key-plaintext")).toHaveTextContent("ovk_u.pub-123.sec-456-secret");
    expect(screen.getByTestId("api-key-once-notice")).toHaveTextContent("明文只显示这一次");

    // 返回列表：明文视图销毁，不可再取（AC③）
    fireEvent.click(screen.getByTestId("api-key-done"));
    await waitFor(() => {
      expect(screen.queryByTestId("api-key-plaintext")).not.toBeInTheDocument();
    });
    expect(screen.queryByText(/ovk_u\./)).not.toBeInTheDocument();
  });

  it("Key 明文不入任何存储与 URL（AC④）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/me/api-keys") && method === "GET") {
        return jsonResponse(200, { status: "ok", result: [EXISTING_KEY] });
      }
      return jsonResponse(200, {
        status: "ok",
        result: {
          id: "k-2",
          name: "New Key",
          key_last_four: "9b3c",
          status: "active",
          expires_at: null,
          last_used_at: null,
          created_at: "2026-08-19T09:00:00Z",
          api_key: "ovk_u.pub-123.sec-456-secret",
        },
      });
    });

    renderRouterAt("/app/profile/api-keys");
    await screen.findByTestId("api-key-row-k-1");
    fireEvent.change(screen.getByTestId("api-key-name"), { target: { value: "New Key" } });
    fireEvent.click(screen.getByTestId("api-key-create"));
    await screen.findByTestId("api-key-plaintext");

    const plaintext = "ovk_u.pub-123.sec-456-secret";
    expect(globalThis.localStorage.getItem("platform.last-open-path") ?? "").not.toContain("ovk_u");
    expect(globalThis.sessionStorage.length).toBe(0);
    expect(globalThis.location.href).not.toContain("ovk_u");
    // 刷新等价（重挂载）：明文不可再取（AC③ 刷新后）
    cleanup();
    renderRouterAt("/app/profile/api-keys");
    expect(await screen.findByTestId("api-key-row-k-1")).toBeInTheDocument();
    expect(screen.queryByText(plaintext)).not.toBeInTheDocument();
  });

  it("撤销单个 Key：确认弹窗 → DELETE → 列表刷新；不影响其他 Key（AC⑦）", async () => {
    let revoked = false;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/api-keys/k-1") && method === "DELETE") {
        revoked = true;
        return jsonResponse(200, { status: "ok" });
      }
      if (url.includes("/me/api-keys")) {
        return jsonResponse(200, {
          status: "ok",
          result: revoked
            ? [
                { ...EXISTING_KEY, status: "revoked" },
                { ...EXISTING_KEY, id: "k-9", name: "Other", key_last_four: "aa11" },
              ]
            : [EXISTING_KEY, { ...EXISTING_KEY, id: "k-9", name: "Other", key_last_four: "aa11" }],
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });

    renderRouterAt("/app/profile/api-keys");
    await screen.findByTestId("api-key-row-k-1");
    expect(screen.getByTestId("api-key-row-k-9")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("api-key-revoke-k-1"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("api-key-revoke-confirm"));

    await waitFor(() => {
      expect(screen.getByTestId("api-key-row-k-1")).toHaveTextContent("已撤销");
    });
    // 其他 Key 不受影响（AC⑦）
    expect(screen.getByTestId("api-key-row-k-9")).toBeInTheDocument();
    const deleteCall = fetchMock.mock.calls.find(([u]) => String(u).includes("/api-keys/k-1"));
    expect(deleteCall).toBeDefined();
  });

  it("撤销已撤销 Key 幂等成功（82.5：404 KEY_NOT_FOUND 视为成功）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/api-keys/k-1") && method === "DELETE") {
        return jsonResponse(404, { status: "error", error: { code: "KEY_NOT_FOUND" } });
      }
      if (url.includes("/me/api-keys")) {
        return jsonResponse(200, { status: "ok", result: [EXISTING_KEY] });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });

    renderRouterAt("/app/profile/api-keys");
    await screen.findByTestId("api-key-row-k-1");
    fireEvent.click(screen.getByTestId("api-key-revoke-k-1"));
    fireEvent.click(screen.getByTestId("api-key-revoke-confirm"));

    await waitFor(() => {
      expect(screen.getByText("该 API Key 已撤销。")).toBeInTheDocument();
    });
  });
});
