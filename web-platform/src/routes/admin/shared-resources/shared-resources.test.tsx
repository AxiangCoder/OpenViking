/**
 * /admin/shared-resources 共享 Resource 管理页测试（06 §13.3，09 §39.2/§40.1，
 * 14 号计划 §98.8，P4-E3 AC⑦）。
 *
 * - 显示 provisioning/failed 占位行（首次处理中/首次处理失败，仅管理者可见，09 §39.2）；
 * - 新增弹窗显示「保存到：{Account} 共享 Resource」，无归属切换（AC⑦，09 §40.1）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, type AuthMeResult } from "@/features/auth/auth-state";

const ADMIN_ME: AuthMeResult = {
  account: { id: "acc-1", code: "acme", name: "Acme Corp" },
  user: { id: "u-admin", ov_user_id: "ov-u-admin" },
  roles: ["account_admin"],
  permissions: [
    "resource.account_shared.read.account",
    "resource.account_shared.write.account",
    "resource.account_shared.delete.account",
  ],
  can_switch_account: false,
  csrf_token: null,
};

const ROW_ACTIVE = {
  id: "res_00000000-0000-0000-0000-000000000001",
  visibility: "account_shared",
  name: "共享规范文档",
  description: null,
  source_type: "web",
  source_display: "example.com",
  tags: [],
  lifecycle_status: "active",
  processing: { state: "succeeded", stage: "succeeded", latest_operation_id: null, last_succeeded_at: "2026-08-18T08:00:00Z" },
  watch: { state: "not_configured" },
  version: 1,
  created_at: "2026-08-18T07:50:00Z",
  updated_at: "2026-08-18T08:00:00Z",
};

const ROW_PROVISIONING = {
  ...ROW_ACTIVE,
  id: "res_00000000-0000-0000-0000-000000000002",
  name: "待处理导入",
  lifecycle_status: "provisioning",
  processing: { state: "provisioning", stage: "provisioning", latest_operation_id: null, last_succeeded_at: null },
  updated_at: "2026-08-19T08:00:00Z",
};

const ROW_FAILED = {
  ...ROW_ACTIVE,
  id: "res_00000000-0000-0000-0000-000000000003",
  name: "导入失败项",
  lifecycle_status: "failed",
  processing: { state: "failed", stage: "failed", latest_operation_id: null, last_succeeded_at: null },
  updated_at: "2026-08-19T08:30:00Z",
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

describe("/admin/shared-resources（09 §39.2/§40.1，AC⑦）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: ADMIN_ME });
      }
      if (url.endsWith("/resources/capabilities")) {
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
      if (url.includes("/account/resources?limit=50")) {
        return jsonResponse(200, {
          status: "ok",
          result: { items: [ROW_ACTIVE, ROW_PROVISIONING, ROW_FAILED], next_cursor: null },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    configurePlatformClient({ fetchImpl: fetchMock, csrfTokenProvider: () => "csrf" });
    setAuthStateForTest({ status: "authenticated", me: ADMIN_ME, sessionExpired: false });
  });

  afterEach(() => {
    cleanup();
  });

  it("AC⑦：显示 provisioning/failed 占位行（首次处理中/首次处理失败）", async () => {
    renderRouterAt("/admin/shared-resources");
    const list = await screen.findByTestId("resource-list-shared");

    expect(within(list).getByText("共享规范文档")).toBeInTheDocument();
    expect(within(list).getByText("待处理导入")).toBeInTheDocument();
    expect(within(list).getByText("导入失败项")).toBeInTheDocument();
    // 占位行状态徽标（筛选项 option 中也有同名文案，限定状态徽标）
    expect(list.querySelectorAll(".status-badge.provisioning").length).toBe(1);
    expect(list.querySelectorAll(".status-badge.failed").length).toBe(1);
  });

  it("AC⑦：新增弹窗显示「保存到：{Account} 共享 Resource」且无归属切换", async () => {
    renderRouterAt("/admin/shared-resources");
    await screen.findByTestId("resource-list-shared");

    fireEvent.click(screen.getByRole("button", { name: "新增 Resource" }));
    const dialog = await screen.findByRole("dialog", { name: "新增 Resource" });

    expect(dialog).toHaveTextContent("保存到：");
    expect(dialog).toHaveTextContent("Acme Corp 共享 Resource");
    expect(dialog).toHaveTextContent("无归属切换");
    // 无归属/范围下拉
    const selects = dialog.querySelectorAll("select");
    expect(selects.length).toBe(0);
  });
});
