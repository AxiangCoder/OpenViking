/**
 * /admin/recycle-bin 回收站页测试（13 §88，05 §12.6 注，14 号计划 §98.8，P4-E3 AC⑤⑥）。
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
  user: { id: "u-admin", ov_user_id: "ov-u-admin" },
  roles: ["account_admin"],
  permissions: [
    "resource.account_shared.delete.account",
    "skill.account_shared.manage.account",
    "user.delete",
  ],
  can_switch_account: false,
  csrf_token: null,
};

const ITEM_RESOURCE = {
  id: "job-res-1",
  resource_type: "resource",
  resource_id: "res-shared-1",
  target_name: "共享规范文档",
  deleted_at: "2026-08-10T09:00:00Z",
  purge_after: "2026-09-09T09:00:00Z",
  status: "pending",
  restore_allowed: true,
  restore_permission: "resource.account_shared.delete.account",
  restored_at: null,
};

const ITEM_SKILL = {
  id: "job-skill-1",
  resource_type: "skill",
  resource_id: "skill-shared-1",
  target_name: "code-review-skill",
  deleted_at: "2026-08-11T09:00:00Z",
  purge_after: "2026-09-10T09:00:00Z",
  status: "pending",
  restore_allowed: true,
  restore_permission: "skill.account_shared.manage.account",
  restored_at: null,
};

const ITEM_USER = {
  id: "job-user-1",
  resource_type: "user",
  resource_id: "u-9",
  target_name: "Bob",
  deleted_at: "2026-08-12T09:00:00Z",
  purge_after: "2026-09-11T09:00:00Z",
  status: "pending",
  restore_allowed: true,
  restore_permission: "user.delete",
  restored_at: null,
};

// 他人私有 Skill：不可恢复类型 → 不显示（AC⑤）
const ITEM_OTHER_PRIVATE_SKILL = {
  id: "job-skill-other",
  resource_type: "skill",
  resource_id: "skill-other",
  target_name: "other-private-skill",
  deleted_at: "2026-08-12T09:00:00Z",
  purge_after: "2026-09-11T09:00:00Z",
  status: "pending",
  restore_allowed: false,
  restore_permission: null,
  restored_at: null,
};

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

describe("/admin/recycle-bin（13 §88，AC⑤⑥）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: adminMe });
      }
      if (method === "POST" && url.includes("/admin/recycle-bin/")) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            resource_type: "resource",
            resource_id: "res-shared-1",
            deletion_job_id: "job-res-1",
            deleted_at: "2026-08-10T09:00:00Z",
            restore_until: "2026-09-09T09:00:00Z",
          },
        });
      }
      if (method === "GET" && url.includes("/admin/recycle-bin")) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [ITEM_RESOURCE, ITEM_SKILL, ITEM_USER, ITEM_OTHER_PRIVATE_SKILL],
            next_cursor: null,
          },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: adminMe, sessionExpired: false });
  });

  afterEach(() => {
    cleanup();
  });

  it("AC⑤：按对象类型分组展示；不可恢复类型（他人私有 Skill）不显示", async () => {
    renderRouterAt("/admin/recycle-bin");
    const panel = await screen.findByTestId("recycle-bin-panel");

    expect(within(panel).getByText("Resource")).toBeInTheDocument();
    expect(within(panel).getByText("Skill")).toBeInTheDocument();
    expect(within(panel).getByText("用户")).toBeInTheDocument();
    expect(screen.queryByText("other-private-skill")).not.toBeInTheDocument();
    expect(screen.queryByText("不可恢复")).not.toBeInTheDocument();
  });

  it("AC⑤：每项展示名称/删除时间/恢复截止；恢复按钮携带类型化权限码", async () => {
    renderRouterAt("/admin/recycle-bin");
    await screen.findByTestId("recycle-bin-panel");

    expect(screen.getByText("共享规范文档")).toBeInTheDocument();
    expect(screen.getByTestId("restore-button-resource-res-shared-1")).toBeInTheDocument();
    expect(screen.getByTestId("restore-button-skill-skill-shared-1")).toBeInTheDocument();
    expect(screen.getByTestId("restore-button-user-u-9")).toBeInTheDocument();
  });

  it("AC⑤：恢复权限按类型分别校验——无对应权限码的类型显示「无恢复权限」而非恢复按钮", async () => {
    const adminWithoutSkillPerm: AuthMeResult = {
      ...adminMe,
      permissions: ["resource.account_shared.delete.account"],
    };
    setAuthStateForTest({ status: "authenticated", me: adminWithoutSkillPerm, sessionExpired: false });
    renderRouterAt("/admin/recycle-bin");
    await screen.findByTestId("recycle-bin-panel");

    // 持有 resource 恢复码 → 显示恢复按钮
    expect(screen.getByTestId("restore-button-resource-res-shared-1")).toBeInTheDocument();
    // 未持有 skill/user 恢复码 → 显示「无恢复权限」（两行：Skill 与 用户）
    expect(screen.getAllByText("无恢复权限")).toHaveLength(2);
    expect(screen.queryByTestId("restore-button-skill-skill-shared-1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("restore-button-user-u-9")).not.toBeInTheDocument();
  });

  it("AC⑥：恢复弹窗展示名称/类型/恢复截止；确认恢复 → POST /admin/recycle-bin/{id}/restore", async () => {
    renderRouterAt("/admin/recycle-bin");
    await screen.findByTestId("recycle-bin-panel");

    fireEvent.click(screen.getByTestId("restore-button-resource-res-shared-1"));
    const dialog = await screen.findByTestId("restore-dialog");
    expect(dialog).toHaveTextContent("共享规范文档");
    expect(dialog).toHaveTextContent("Resource");

    fireEvent.click(screen.getByTestId("restore-confirm"));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([u, init]) =>
          String(u).includes("/admin/recycle-bin/job-res-1/restore") &&
          (init as RequestInit)?.method === "POST",
      );
      expect(call).toBeDefined();
    });
  });

  it("AC⑥：Skill 恢复同名冲突 → SKILL_NAME_CONFLICT 正确展示，保持删除状态", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: adminMe });
      }
      if (method === "POST" && url.includes("/admin/recycle-bin/job-skill-1/restore")) {
        return jsonResponse(409, {
          status: "error",
          error: { code: "SKILL_NAME_CONFLICT" },
        });
      }
      if (method === "GET" && url.includes("/admin/recycle-bin")) {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [ITEM_SKILL], next_cursor: null },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/admin/recycle-bin");
    await screen.findByTestId("recycle-bin-panel");

    fireEvent.click(screen.getByTestId("restore-button-skill-skill-shared-1"));
    await screen.findByTestId("restore-dialog");
    fireEvent.click(screen.getByTestId("restore-confirm"));

    expect(await screen.findByTestId("recycle-bin-action-error")).toHaveTextContent(
      "该名称已被其他 Skill 占用",
    );
    expect(screen.getByText("code-review-skill")).toBeInTheDocument();
  });

  it("AC⑥：恢复窗口已过 → RESTORE_WINDOW_EXPIRED 正确展示并刷新列表", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: adminMe });
      }
      if (method === "POST" && url.includes("/admin/recycle-bin/")) {
        return jsonResponse(409, {
          status: "error",
          error: { code: "RESTORE_WINDOW_EXPIRED" },
        });
      }
      if (method === "GET" && url.includes("/admin/recycle-bin")) {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [ITEM_RESOURCE], next_cursor: null },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    renderRouterAt("/admin/recycle-bin");
    await screen.findByTestId("recycle-bin-panel");

    fireEvent.click(screen.getByTestId("restore-button-resource-res-shared-1"));
    await screen.findByTestId("restore-dialog");
    fireEvent.click(screen.getByTestId("restore-confirm"));

    expect(await screen.findByTestId("recycle-bin-action-error")).toHaveTextContent(
      "恢复窗口已过期",
    );
  });
});
