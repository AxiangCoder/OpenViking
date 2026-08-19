/**
 * /admin/users/{id}/skills/{skillId} 成员 Skill 只读详情 + 发布为共享集成测试
 * （13 §86.2、10 §58，14 号计划 §98.7，P4-E2 AC④⑤⑥）。
 *
 * - AC⑥：只读详情无编辑/删除/恢复入口（10 §61.3）；
 * - AC④：发布确认弹窗完整展示影响与不可取消提示（10 §58.3），不要求密码；
 * - AC⑤：发布失败状态展示与重试入口（10 §58.4 Operation failed 可重试）；
 * - 无发布权限时无发布入口。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, type AuthMeResult } from "@/features/auth/auth-state";

const adminMe: AuthMeResult = {
  account: { id: "acc-1", code: "acme", name: "Acme Corp" },
  user: { id: "u-admin", ov_user_id: "ov-u-admin", display_name: "Admin Lee" },
  roles: ["account_admin"],
  permissions: [
    "user.read",
    "skill.user_private.read.account",
    "skill.user_private.publish.account",
  ],
  can_switch_account: false,
  csrf_token: null,
};

const adminNoPublish: AuthMeResult = {
  account: { id: "acc-1", code: "acme", name: "Acme Corp" },
  user: { id: "u-admin", ov_user_id: "ov-u-admin", display_name: "Admin Lee" },
  roles: ["account_admin"],
  permissions: ["user.read", "skill.user_private.read.account"],
  can_switch_account: false,
  csrf_token: null,
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
  last_login_at: null,
};

const USERS_RESULT = { items: [ALICE], next_cursor: null };

const SKILL = {
  id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  name: "code-review",
  description: "代码评审助手",
  tags: ["review=code"],
  visibility: "user_private",
  source_type: "online",
  has_auxiliary_files: false,
  status: "active",
  version: 2,
  created_at: "2026-08-10T00:00:00Z",
  updated_at: "2026-08-12T00:00:00Z",
  owner_user_id: "u-1",
  content: "# code-review\n评审 Pull Request 的规则。",
  allowed_tools: ["github"],
  files: [],
};

const SKILL_PATH = "/api/platform/v1/admin/users/u-1/skills/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function renderRouterAt(path: string) {
  const router = createAppRouter({ history: createMemoryHistory({ initialEntries: [path] }) });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("/admin/users/{id}/skills/{skillId} 成员 Skill 详情与发布（AC④⑤⑥）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: adminMe });
      }
      if (init?.method === "GET" && url === "/api/platform/v1/admin/users") {
        return jsonResponse(200, { status: "ok", result: USERS_RESULT });
      }
      if (init?.method === "GET" && url === SKILL_PATH) {
        return jsonResponse(200, { status: "ok", result: SKILL });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: adminMe, sessionExpired: false });
  });

  afterEach(() => {
    cleanup();
    setAuthStateForTest({ status: "idle", me: null, sessionExpired: false });
  });

  it("AC⑥：只读详情展示完整区块，无编辑/删除/恢复入口（10 §61.3）", async () => {
    renderRouterAt("/admin/users/u-1/skills/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");
    expect(await screen.findByTestId("member-skill-name")).toHaveTextContent("code-review");
    expect(screen.getByTestId("skill-detail")).toBeInTheDocument();
    expect(screen.getByTestId("skill-usage")).toHaveTextContent("评审 Pull Request");
    expect(screen.getByTestId("skill-tools-list")).toHaveTextContent("github");
    expect(screen.getByTestId("member-skill-ownership")).toHaveTextContent("成员私有");

    // AC⑥：成员私有 Skill 无编辑/删除/恢复/上传入口（设计禁止，10 §59/§61.3）
    expect(screen.queryByRole("button", { name: /编辑|删除|恢复|整体替换|上传/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId("skill-edit")).not.toBeInTheDocument();
    expect(screen.queryByTestId("skill-delete")).not.toBeInTheDocument();
    expect(screen.queryByTestId("skill-restore")).not.toBeInTheDocument();
  });

  it("AC④：发布确认弹窗完整展示影响与不可取消提示，无密码输入", async () => {
    renderRouterAt("/admin/users/u-1/skills/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");
    await screen.findByTestId("member-skill-name");
    fireEvent.click(screen.getByTestId("member-skill-publish-open"));

    const dialog = screen.getByTestId("member-skill-publish-dialog");
    expect(dialog).toBeInTheDocument();
    const impact = within(dialog).getByTestId("member-skill-publish-impact");
    expect(impact).toHaveTextContent("code-review");
    expect(impact).toHaveTextContent("当前所属 User：Alice");
    expect(impact).toHaveTextContent("目标 Account：Acme Corp");
    expect(impact).toHaveTextContent("发布后所有 Account 成员可读取和使用");
    expect(impact).toHaveTextContent("原 User 私有区不再保留");
    expect(impact).toHaveTextContent("不可取消发布");

    // 不要求所属 User 审批、不重输密码（10 §58.3、06 §14.5）
    expect(dialog).toHaveTextContent("不要求所属 User 审批");
    expect(screen.queryByLabelText(/密码/)).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/密码/)).not.toBeInTheDocument();
  });

  it("发布成功：展示 Operation 状态与不可取消提示（10 §58.4，AC④）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return jsonResponse(200, { status: "ok", result: adminMe });
      if (init?.method === "GET" && url === "/api/platform/v1/admin/users") {
        return jsonResponse(200, { status: "ok", result: USERS_RESULT });
      }
      if (init?.method === "GET" && url === SKILL_PATH) {
        return jsonResponse(200, { status: "ok", result: SKILL });
      }
      if (init?.method === "POST" && url === `${SKILL_PATH}/publish`) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            skill_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            operation_id: "op-publish-1",
            status: "pending",
            visibility: "account_shared",
          },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/admin/users/u-1/skills/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");
    await screen.findByTestId("member-skill-name");
    fireEvent.click(screen.getByTestId("member-skill-publish-open"));
    fireEvent.click(screen.getByTestId("member-skill-publish-confirm"));

    const success = await screen.findByTestId("member-skill-publish-success");
    expect(success).toHaveTextContent("目标 Account：Acme Corp");
    expect(success).toHaveTextContent("op-publish-1");
    expect(success).toHaveTextContent("pending");
    expect(success).toHaveTextContent("不可取消");
    expect(success).toHaveTextContent("私有区不再保留");

    // 提交的请求路径与发布端点一致（POST .../publish）
    const publishCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === `${SKILL_PATH}/publish` && init?.method === "POST",
    );
    expect(publishCall).toBeTruthy();
  });

  it("AC⑤：发布失败展示失败状态与重试入口，重试成功后恢复（Operation failed 可重试，10 §58.4）", async () => {
    let failOnce = true;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return jsonResponse(200, { status: "ok", result: adminMe });
      if (init?.method === "GET" && url === "/api/platform/v1/admin/users") {
        return jsonResponse(200, { status: "ok", result: USERS_RESULT });
      }
      if (init?.method === "GET" && url === SKILL_PATH) {
        return jsonResponse(200, { status: "ok", result: SKILL });
      }
      if (init?.method === "POST" && url === `${SKILL_PATH}/publish`) {
        if (failOnce) {
          failOnce = false;
          return jsonResponse(500, {
            status: "error",
            error: { code: "SKILL_PUBLISH_MIGRATION_FAILED", message: "migration failed" },
          });
        }
        return jsonResponse(200, {
          status: "ok",
          result: {
            skill_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            operation_id: "op-publish-2",
            status: "pending",
            visibility: "account_shared",
          },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/admin/users/u-1/skills/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");
    await screen.findByTestId("member-skill-name");
    fireEvent.click(screen.getByTestId("member-skill-publish-open"));
    fireEvent.click(screen.getByTestId("member-skill-publish-confirm"));

    // 失败状态展示 + 重试入口（AC⑤）
    const errorBox = await screen.findByTestId("member-skill-publish-error");
    expect(errorBox).toHaveTextContent("发布迁移失败");
    expect(errorBox).toHaveTextContent("可重试");
    expect(screen.getByTestId("member-skill-publish-retry")).toBeInTheDocument();

    // 重试 → 成功
    fireEvent.click(screen.getByTestId("member-skill-publish-retry"));
    expect(await screen.findByTestId("member-skill-publish-success")).toHaveTextContent("op-publish-2");
    const publishCalls = fetchMock.mock.calls.filter(
      ([url, init]) => String(url) === `${SKILL_PATH}/publish` && init?.method === "POST",
    );
    expect(publishCalls).toHaveLength(2);
  });

  it("无发布权限（无 skill.user_private.publish.account）时不显示发布入口（AC⑥）", async () => {
    setAuthStateForTest({ status: "authenticated", me: adminNoPublish, sessionExpired: false });
    renderRouterAt("/admin/users/u-1/skills/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");
    await screen.findByTestId("member-skill-name");
    expect(screen.queryByTestId("member-skill-publish-open")).not.toBeInTheDocument();
    expect(screen.queryByTestId("member-skill-publish-section")).not.toBeInTheDocument();
    await waitFor(() => {
      const calls = fetchMock.mock.calls.filter(([url, init]) =>
        String(url).endsWith("/publish"),
      );
      expect(calls).toHaveLength(0);
    });
  });
});
