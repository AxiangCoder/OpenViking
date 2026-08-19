/**
 * /platform/accounts/{accountId}/skills 平台共享 Skill 只读页测试
 * （10 §60、13 §90，P4-E4 AC⑦⑨：Skill 始终只读）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, type AuthMeResult } from "@/features/auth/auth-state";

const psaMe: AuthMeResult = {
  account: null,
  user: { id: "u-psa", ov_user_id: "ov-u-psa", display_name: "平台管理员", email: "psa@example.com" },
  roles: ["platform_super_admin"],
  permissions: ["account.read.platform", "skill.account_shared.read.platform"],
  can_switch_account: false,
  csrf_token: null,
};

const ACCT = {
  id: "acc-1",
  code: "acme",
  name: "Acme Corp",
  status: "active",
  ov_account_id: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const SKILL_ID = "a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1";

const SKILL_ROW = {
  id: SKILL_ID,
  name: "Shared Skill",
  description: "共享技能",
  tags: ["type=ops"],
  visibility: "account_shared",
  source_type: "online",
  has_auxiliary_files: false,
  status: "active",
  version: 2,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-03T00:00:00Z",
};

const SKILL_DETAIL = {
  ...SKILL_ROW,
  content: "技能正文",
  allowed_tools: ["web_search"],
  files: [],
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

describe("/platform/accounts/{id}/skills（10 §60，AC⑦⑨）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: psaMe });
      }
      if (method === "GET" && url === "/api/platform/v1/platform/accounts") {
        return jsonResponse(200, { status: "ok", result: { items: [ACCT], next_cursor: null } });
      }
      if (method === "GET" && url === "/api/platform/v1/platform/accounts/acc-1/skills") {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [SKILL_ROW], next_cursor: null },
        });
      }
      if (
        method === "GET" &&
        url === `/api/platform/v1/platform/accounts/acc-1/skills/${SKILL_ID}`
      ) {
        return jsonResponse(200, { status: "ok", result: SKILL_DETAIL });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: psaMe, sessionExpired: false });
  });

  afterEach(() => {
    cleanup();
    setAuthStateForTest({ status: "idle", me: null, sessionExpired: false });
    configurePlatformClient({ fetchImpl: undefined as never });
  });

  it("共享 Skill 列表只读：无创建/上传/编辑入口，Account 上下文明确（AC②⑨）", async () => {
    renderRouterAt("/platform/accounts/acc-1/skills");
    expect(await screen.findByTestId("platform-skills-list")).toHaveTextContent("Shared Skill");
    expect(screen.getByTestId("platform-skills-account-context")).toHaveTextContent("Acme Corp");
    expect(screen.getByTestId("platform-skills-list")).toHaveTextContent("平台对 Skill 始终只读");
    expect(screen.queryByText("新建 Skill")).not.toBeInTheDocument();
    expect(screen.queryByText("上传")).not.toBeInTheDocument();
  });

  it("共享 Skill 详情只读：无编辑/删除/恢复入口（AC⑨）", async () => {
    renderRouterAt(`/platform/accounts/acc-1/skills/${SKILL_ID}`);
    expect(await screen.findByTestId("platform-skill-name")).toHaveTextContent("Shared Skill");
    expect(screen.getByTestId("platform-skill-ownership")).toHaveTextContent("Account 共享");
    expect(screen.getByTestId("platform-skill-readonly-note")).toHaveTextContent("始终只读");
    expect(screen.queryByText("编辑")).not.toBeInTheDocument();
    expect(screen.queryByText("删除")).not.toBeInTheDocument();
    expect(screen.queryByText("恢复")).not.toBeInTheDocument();
  });

  it("非法产品 ID 直接 404 语义、不发起后端请求", async () => {
    renderRouterAt("/platform/accounts/acc-1/skills/not-a-product-id");
    expect(await screen.findByTestId("platform-skill-invalid-id")).toBeInTheDocument();
    const detailCall = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/skills/not-a-product-id"),
    );
    expect(detailCall).toBeUndefined();
  });
});
