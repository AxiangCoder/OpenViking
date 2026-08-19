/**
 * Resource 路由页集成测试（09 §38.1/§39/§45.3，P3-E4 AC①⑥）。
 *
 * - `/app/resources` 裸路由：首次默认 /private；最近使用的合法分区跳转；
 *   无共享读权限时回退私有区（09 §38.1）；
 * - 私有列表：新增入口固定归属；回收站区恢复自己的私有 Resource，
 *   RESTORE_WINDOW_EXPIRED 正确展示（AC⑥）；
 * - 共享页：普通 User 只读提示管理员维护；Admin 见管理能力（AC①）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, type AuthMeResult } from "@/features/auth/auth-state";
import { clearPreferences } from "@/lib/storage";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const USER_ME: AuthMeResult = {
  account: { id: "acc-1", name: "测试Account" },
  user: { id: "u-1", ov_user_id: "ov-u-1" },
  roles: ["user"],
  permissions: [
    "resource.user_private.read.self",
    "resource.user_private.write.self",
    "resource.user_private.delete.self",
    "resource.account_shared.read.account",
  ],
  can_switch_account: false,
  csrf_token: null,
};

const ADMIN_ME: AuthMeResult = {
  ...USER_ME,
  roles: ["account_admin"],
  permissions: [...USER_ME.permissions, "resource.account_shared.write.account", "resource.account_shared.delete.account"],
};

const SUMMARY = {
  id: "res_00000000-0000-0000-0000-000000000001",
  visibility: "user_private",
  name: "文档A",
  description: null,
  source_type: "web",
  source_display: "example.com",
  tags: ["type=doc"],
  lifecycle_status: "active",
  processing: { state: "succeeded", stage: "succeeded", latest_operation_id: null, last_succeeded_at: "2026-08-18T08:00:00Z" },
  watch: { state: "not_configured" },
  version: 1,
  created_at: "2026-08-18T07:50:00Z",
  updated_at: "2026-08-18T08:00:00Z",
};

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

describe("Resource 路由页（09 §38.1/§39/§45.3，AC①⑥）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    clearPreferences();
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (url: string) => {
      const path = String(url);
      if (path.endsWith("/resources/capabilities")) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            source_types: ["upload", "web", "git"] as const,
            upload: { max_files_per_batch: 3, max_file_size_bytes: 10 * 1024 * 1024, ttl_minutes: 15, accepts_archives: false },
            watch: { enabled: true, interval_presets_minutes: [60, 360], manual_refresh_min_interval_seconds: 300 },
            git: { ignore_dirs_max: 50, include_exclude_max: 20, processing_mode_fixed: "semantic_and_vectors" },
          },
        });
      }
      if (path.endsWith("/me/resources?limit=50") || path.endsWith("/account/resources?limit=50")) {
        return jsonResponse(200, { status: "ok", result: { items: [SUMMARY], next_cursor: null } });
      }
      if (path.endsWith("/recycle-bin")) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: "job-1",
                resource_type: "resource",
                resource_id: "00000000-0000-0000-0000-000000000002",
                target_name: "已删文档",
                deleted_at: "2026-08-10T00:00:00Z",
                purge_after: "2026-09-10T00:00:00Z",
                status: "pending_deletion",
                restore_allowed: true,
                restore_permission: "resource.user_private.delete.self",
                restored_at: null,
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (path.endsWith("/recycle-bin/job-1/restore")) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            resource_type: "resource",
            resource_id: "00000000-0000-0000-0000-000000000002",
            deletion_job_id: "job-1",
            deleted_at: "2026-08-10T00:00:00Z",
            restore_until: "2026-09-10T00:00:00Z",
          },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    configurePlatformClient({ fetchImpl: fetchMock, csrfTokenProvider: () => "csrf" });
  });

  afterEach(() => {
    configurePlatformClient({ fetchImpl: undefined, csrfTokenProvider: undefined });
    clearPreferences();
  });

  it("09 §38.1：裸路由首次默认 /app/resources/private", async () => {
    setAuthStateForTest({ status: "authenticated", me: USER_ME, sessionExpired: false });
    const router = renderRouterAt("/app/resources");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/app/resources/private");
    });
    expect((await screen.findAllByText("我的 Resource")).length).toBeGreaterThanOrEqual(1);
  });

  it("09 §38.1：最近使用的合法分区跳转（shared）；无共享读权限回退私有", async () => {
    setAuthStateForTest({ status: "authenticated", me: ADMIN_ME, sessionExpired: false });
    let router = renderRouterAt("/app/resources/shared");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/app/resources/shared");
    });
    // 已访问共享页 → 记录最近分区；裸路由跳 shared
    await waitFor(() => {
      expect(localStorage.getItem("platform.resources-last-partition")).toBeTruthy();
    });
    router = renderRouterAt("/app/resources");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/app/resources/shared");
    });

    // 无共享读权限：回退私有
    setAuthStateForTest({
      status: "authenticated",
      me: { ...USER_ME, permissions: ["resource.user_private.read.self"] },
      sessionExpired: false,
    });
    router = renderRouterAt("/app/resources");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/app/resources/private");
    });
  });

  it("AC①：共享页普通 User 只读提示；Admin 可见新增管理入口", async () => {
    setAuthStateForTest({ status: "authenticated", me: USER_ME, sessionExpired: false });
    renderRouterAt("/app/resources/shared");
    expect(await screen.findByTestId("shared-managed-by-admin")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "新增 Resource" })).not.toBeInTheDocument();

    setAuthStateForTest({ status: "authenticated", me: ADMIN_ME, sessionExpired: false });
    renderRouterAt("/app/resources/shared");
    expect(await screen.findByRole("button", { name: "新增 Resource" })).toBeInTheDocument();
  });

  it("AC⑥：私有页回收站区可恢复自己的 Resource，恢复提示 Watch 保持 paused", async () => {
    setAuthStateForTest({ status: "authenticated", me: USER_ME, sessionExpired: false });
    renderRouterAt("/app/resources/private");
    expect(await screen.findByTestId("restore-section")).toBeInTheDocument();
    expect(screen.getByText("已删文档")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "恢复" }));
    expect(await screen.findByTestId("restore-notice")).toBeInTheDocument();
    expect(screen.getByText(/恢复后保持暂停，需手动重新启用/)).toBeInTheDocument();
  });

  it("AC⑥：RESTORE_WINDOW_EXPIRED 正确展示", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const path = String(url);
      if (path.endsWith("/recycle-bin")) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: "job-1",
                resource_type: "resource",
                resource_id: "00000000-0000-0000-0000-000000000002",
                target_name: "已删文档",
                deleted_at: "2026-07-01T00:00:00Z",
                purge_after: "2026-08-01T00:00:00Z",
                status: "pending_deletion",
                restore_allowed: true,
                restore_permission: "resource.user_private.delete.self",
                restored_at: null,
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (path.endsWith("/recycle-bin/job-1/restore")) {
        return jsonResponse(409, {
          status: "error",
          error: { code: "RESTORE_WINDOW_EXPIRED", message: "expired" },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    setAuthStateForTest({ status: "authenticated", me: USER_ME, sessionExpired: false });
    renderRouterAt("/app/resources/private");
    fireEvent.click(await screen.findByRole("button", { name: "恢复" }));
    expect(await screen.findByText(/恢复窗口（30 天）已过期/)).toBeInTheDocument();
  });

  it("AC⑧：列表页 URL/请求无 URI 类参数（列表请求仅 source_type/status/cursor）", async () => {
    setAuthStateForTest({ status: "authenticated", me: USER_ME, sessionExpired: false });
    renderRouterAt("/app/resources/private");
    await screen.findByText("文档A");
    const listCall = fetchMock.mock.calls.find((call) =>
      String(call[0]).includes("/me/resources?"),
    );
    expect(listCall).toBeDefined();
    expect(String(listCall![0])).not.toMatch(/uri|viking/i);
  });
});
