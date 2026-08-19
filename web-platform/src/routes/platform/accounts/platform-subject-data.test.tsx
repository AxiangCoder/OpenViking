/**
 * /platform/accounts/{accountId}/users/{userId}/data 平台级 Subject 数据视图集成测试
 * （13 §89.3 同 84.2，P4-E4 AC②⑦⑨；Skill 路由全部只读）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, type AuthMeResult } from "@/features/auth/auth-state";

const psaMe: AuthMeResult = {
  account: null,
  user: { id: "u-psa", ov_user_id: "ov-u-psa", display_name: "平台管理员", email: "psa@example.com" },
  roles: ["platform_super_admin"],
  permissions: [
    "account.read.platform",
    "user.read.platform",
    "memory.read.platform",
    "session.read.platform",
    "resource.user_private.read.platform",
    "skill.user_private.read.platform",
    "credential.read.platform",
  ],
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

const RES_ID = "3f3f3f3f-3f3f-3f3f-3f3f-3f3f3f3f3f3f";
const SKILL_ID = "a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1";

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

describe("/platform/accounts/{id}/users/{uid}/data（13 §89.3，AC②⑦⑨）", () => {
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
      if (method === "GET" && url === "/api/platform/v1/platform/accounts/acc-1/users") {
        return jsonResponse(200, { status: "ok", result: { items: [ALICE], next_cursor: null } });
      }
      if (
        method === "POST" &&
        url === "/api/platform/v1/platform/accounts/acc-1/users/u-1/search/find"
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                context_type: "resource",
                display_name: "Res Alpha",
                ref_id: RES_ID,
                visibility: "user_private",
                abstract: "资源摘要",
                memory_type: null,
                match_reason: null,
              },
              {
                context_type: "skill",
                display_name: "Skill Beta",
                ref_id: SKILL_ID,
                visibility: "user_private",
                abstract: "技能摘要",
                memory_type: null,
                match_reason: null,
              },
              {
                context_type: "memory",
                display_name: "Mem Gamma",
                ref_id: null,
                visibility: "user_private",
                abstract: "记忆摘要",
                memory_type: "entity",
                match_reason: "近期工作",
              },
            ],
          },
        });
      }
      if (
        method === "GET" &&
        url === "/api/platform/v1/platform/accounts/acc-1/users/u-1/resources"
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: `res_${RES_ID}`,
                name: "Res Alpha",
                description: null,
                tags: [],
                visibility: "user_private",
                source_type: "web",
                lifecycle_status: "active",
                version: 1,
                created_at: "2026-08-01T00:00:00Z",
                updated_at: "2026-08-01T00:00:00Z",
                source_display: null,
                watch: { state: "not_configured", interval_minutes: null },
                processing: { status: "active", stage: null, last_succeeded_at: null, last_error: null },
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (
        method === "GET" &&
        url === `/api/platform/v1/platform/accounts/acc-1/users/u-1/resources/${RES_ID}`
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            id: `res_${RES_ID}`,
            name: "Res Alpha",
            description: "描述",
            tags: ["type=doc"],
            visibility: "user_private",
            source_type: "web",
            lifecycle_status: "active",
            version: 1,
            created_at: "2026-08-01T00:00:00Z",
            updated_at: "2026-08-01T00:00:00Z",
            source_display: "example.com",
            overview: "概览正文",
            content: { node_count: 3, size_bytes: 1024 },
            processing: { status: "active", stage: null, last_succeeded_at: null, last_error: null },
            watch: { state: "not_configured", interval_minutes: null },
          },
        });
      }
      if (method === "GET" && url === "/api/platform/v1/platform/accounts/acc-1/users/u-1/skills") {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: SKILL_ID,
                name: "Skill Beta",
                description: "技能描述",
                tags: [],
                visibility: "user_private",
                source_type: "online",
                has_auxiliary_files: false,
                status: "active",
                version: 1,
                created_at: "2026-08-01T00:00:00Z",
                updated_at: "2026-08-01T00:00:00Z",
                owner_user_id: "u-1",
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (
        method === "GET" &&
        url === `/api/platform/v1/platform/accounts/acc-1/users/u-1/skills/${SKILL_ID}`
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            id: SKILL_ID,
            name: "Skill Beta",
            description: "技能描述",
            tags: [],
            visibility: "user_private",
            source_type: "online",
            has_auxiliary_files: false,
            status: "active",
            version: 1,
            created_at: "2026-08-01T00:00:00Z",
            updated_at: "2026-08-01T00:00:00Z",
            owner_user_id: "u-1",
            content: "技能正文",
            allowed_tools: [],
            files: [],
          },
        });
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

  it("固定 Actor/Subject 横幅：以平台管理员身份查看 Alice 的数据，Account 上下文明确（AC②）", async () => {
    renderRouterAt("/platform/accounts/acc-1/users/u-1/data");
    await screen.findByTestId("platform-subject-data-banner");
    expect(screen.getByTestId("platform-subject-data-actor")).toHaveTextContent("平台管理员");
    expect(screen.getByTestId("platform-subject-data-subject")).toHaveTextContent("Alice（alice@example.com）");
    expect(screen.getByTestId("platform-subject-data-account")).toHaveTextContent("Acme Corp");
    expect(screen.getByTestId("platform-subject-data-account")).toHaveTextContent("不改变登录者身份");
    expect(screen.getByTestId("platform-subject-data-readonly-note")).toHaveTextContent("只读");
  });

  it("检索结果只跳平台成员只读详情，不跳 /app；Memory 抽屉只读（AC⑦）", async () => {
    renderRouterAt("/platform/accounts/acc-1/users/u-1/data");
    await screen.findByTestId("platform-subject-data-banner");
    const query = screen.getByTestId("platform-member-search-query");
    fireEvent.change(query, { target: { value: "alpha" } });
    fireEvent.submit(screen.getByTestId("platform-member-search-form"));

    const resourceHit = await screen.findByTestId("platform-member-search-hit-resource");
    expect(resourceHit).toHaveAttribute(
      "href",
      `/platform/accounts/acc-1/users/u-1/resources/${RES_ID}`,
    );
    const skillHit = screen.getByTestId("platform-member-search-hit-skill");
    expect(skillHit).toHaveAttribute(
      "href",
      `/platform/accounts/acc-1/users/u-1/skills/${SKILL_ID}`,
    );
    // AC⑦：不跳 /app 分区
    expect(screen.queryByText(/app\/resources/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("platform-member-search-hit-memory"));
    expect(screen.getByTestId("platform-member-memory-drawer")).toHaveTextContent("Memory 详情（只读）");
    expect(screen.getByTestId("platform-member-memory-drawer-abstract")).toHaveTextContent("记忆摘要");
  });

  it("成员 Resource 只读预览：无编辑/替换/Refresh/Watch/发布/删除/下载（AC⑦）", async () => {
    renderRouterAt(`/platform/accounts/acc-1/users/u-1/resources/${RES_ID}`);
    const detail = await screen.findByTestId("platform-member-resource-detail");
    expect(detail).toHaveTextContent("Res Alpha");
    expect(screen.getByTestId("platform-member-resource-content")).toHaveTextContent("3 个节点");
    expect(screen.getByTestId("platform-member-resource-readonly-note")).toHaveTextContent("只读预览");
    expect(screen.queryByText("编辑信息")).not.toBeInTheDocument();
    expect(screen.queryByText("Refresh")).not.toBeInTheDocument();
    expect(screen.queryByText("删除")).not.toBeInTheDocument();
    expect(screen.queryByText("下载")).not.toBeInTheDocument();
  });

  it("成员 Skill 只读详情：无发布/编辑/删除入口（Skill 始终只读，AC⑦⑨）", async () => {
    renderRouterAt(`/platform/accounts/acc-1/users/u-1/skills/${SKILL_ID}`);
    await screen.findByTestId("platform-member-skill-name");
    expect(screen.getByTestId("platform-member-skill-ownership")).toHaveTextContent("成员私有");
    expect(screen.getByTestId("platform-member-skill-meta")).toHaveTextContent("始终只读");
    // 平台代用户发布/写入 Skill 不在 v0.1（范围 Out）：无发布按钮/无发布区块
    expect(screen.queryByText("发布为共享")).not.toBeInTheDocument();
    expect(screen.queryByText("重试发布")).not.toBeInTheDocument();
    expect(screen.queryByText("编辑")).not.toBeInTheDocument();
    expect(screen.queryByText("删除")).not.toBeInTheDocument();
  });

  it("非法产品 ID 直接 404 语义、不发起后端详情请求（AC⑦）", async () => {
    renderRouterAt("/platform/accounts/acc-1/users/u-1/resources/not-a-product-id");
    expect(await screen.findByTestId("platform-member-resource-invalid-id")).toBeInTheDocument();
    const detailCall = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/resources/not-a-product-id"),
    );
    expect(detailCall).toBeUndefined();
  });
});
