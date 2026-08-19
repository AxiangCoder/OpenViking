/**
 * /platform 治理页（audit/activity/monitoring/recycle-bin）+ api-keys + skills
 * 集成测试（13 §90，P4-E4 AC⑧⑨⑩）。
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
    "audit.read",
    "task.read.platform",
    "monitoring.read",
    "resource.account_shared.read.platform",
    "resource.account_shared.delete.platform",
    "credential.read.platform",
    "credential.revoke.platform",
    "user.read.platform",
  ],
  can_switch_account: false,
  csrf_token: null,
};

const ACCT_1 = {
  id: "acc-1",
  code: "acme",
  name: "Acme Corp",
  status: "active",
  ov_account_id: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};
const ACCT_2 = {
  id: "acc-2",
  code: "beta",
  name: "Beta Inc",
  status: "active",
  ov_account_id: null,
  created_at: "2026-08-02T00:00:00Z",
  updated_at: "2026-08-02T00:00:00Z",
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

describe("/platform 治理页（13 §90，AC⑧⑩）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { status: "ok", result: psaMe });
      }
      if (method === "GET" && url === "/api/platform/v1/platform/accounts") {
        return jsonResponse(200, { status: "ok", result: { items: [ACCT_1, ACCT_2], next_cursor: null } });
      }
      if (method === "GET" && url === "/api/platform/v1/platform/audit-events") {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: "e1",
                occurred_at: "2026-08-18T08:00:00Z",
                request_id: "req-1",
                account_id: null,
                actor_type: "user",
                actor_user_id: "u-psa",
                actor_account_id: "acc-1",
                actor_system_component: null,
                actor_session_id: null,
                authentication_method: "session",
                actor_credential_id: null,
                subject_account_id: "acc-1",
                subject_user_id: "u-1",
                action: "user.create",
                target_type: "user",
                target_id: "u-1",
                target_visibility: null,
                scope: "platform",
                result: "success",
                reason: null,
                metadata: null,
              },
              {
                id: "e2",
                occurred_at: "2026-08-18T09:00:00Z",
                request_id: "req-2",
                account_id: null,
                actor_type: "user",
                actor_user_id: "u-psa",
                actor_account_id: "acc-2",
                actor_system_component: null,
                actor_session_id: null,
                authentication_method: "session",
                actor_credential_id: null,
                subject_account_id: "acc-2",
                subject_user_id: "u-2",
                action: "password.reset",
                target_type: "user",
                target_id: "u-2",
                target_visibility: null,
                scope: "platform",
                result: "success",
                reason: null,
                metadata: null,
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (
        method === "GET" &&
        (url === "/api/platform/v1/platform/activity" ||
          url.startsWith("/api/platform/v1/platform/activity?"))
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: "op-1",
                operation_type: "resource_import",
                status: "running",
                stage: null,
                target_type: "resource",
                target_id: "res-1",
                target_visibility: "account_shared",
                generation: 1,
                cancellable: true,
                initiated_by: "user",
                error_code: null,
                error_summary: null,
                retryable: false,
                created_at: "2026-08-18T08:00:00Z",
                completed_at: null,
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (method === "GET" && url === "/api/platform/v1/platform/monitoring") {
        return jsonResponse(200, {
          status: "ok",
          result: {
            generated_at: "2026-08-19T00:00:00Z",
            scope: "platform",
            summary: {
              accounts: 3,
              active_accounts: 2,
              users: 10,
              active_users: 8,
              content_refs_active_by_type: { resource: 6, skill: 4 },
              deletion_jobs_in_recycle: 2,
              pending_uploads: 1,
              open_operations: 3,
            },
          },
        });
      }
      if (method === "GET" && url === "/api/platform/v1/platform/recycle-bin") {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: "job-res",
                resource_type: "resource",
                resource_id: "res-1",
                target_name: "Del Doc",
                deleted_at: "2026-08-18T00:00:00Z",
                purge_after: "2026-09-17T00:00:00Z",
                status: "pending",
                restore_allowed: true,
                restore_permission: "resource.account_shared.delete.platform",
                restored_at: null,
              },
              {
                id: "job-skill",
                resource_type: "skill",
                resource_id: "sk-1",
                target_name: "Del Skill",
                deleted_at: "2026-08-18T00:00:00Z",
                purge_after: "2026-09-17T00:00:00Z",
                status: "pending",
                restore_allowed: false,
                restore_permission: null,
                restored_at: null,
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (
        method === "POST" &&
        url === "/api/platform/v1/platform/recycle-bin/job-res/restore"
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            resource_type: "resource",
            resource_id: "res-1",
            deletion_job_id: "job-res",
            deleted_at: "2026-08-18T00:00:00Z",
            restore_until: "2026-09-17T00:00:00Z",
          },
        });
      }
      if (
        method === "GET" &&
        url === "/api/platform/v1/platform/accounts/acc-1/users"
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: "u-1",
                username: "alice",
                email: "alice@example.com",
                display_name: "Alice",
                status: "active",
                role: "user",
                ov_user_id: "ov-u-1",
                created_at: "2026-08-01T00:00:00Z",
                last_login_at: null,
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (
        method === "GET" &&
        url === "/api/platform/v1/platform/accounts/acc-1/users/u-1/api-keys"
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            items: [
              {
                id: "cred-1",
                name: "ci-token",
                key_last_four: "4321",
                status: "active",
                expires_at: null,
                last_used_at: "2026-08-18T08:00:00Z",
                created_at: "2026-08-01T00:00:00Z",
                revoked_at: null,
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (
        method === "DELETE" &&
        url === "/api/platform/v1/platform/accounts/acc-1/users/u-1/api-keys/cred-1"
      ) {
        return jsonResponse(200, {
          status: "ok",
          result: { id: "cred-1", name: "ci-token", key_last_four: "4321", revoked: true },
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

  it("audit：可按目标 Account 筛选，同时展示 Actor 与 Subject（AC⑧），复用 87.2 展示规则（AC⑩）", async () => {
    renderRouterAt("/platform/audit");
    await screen.findByTestId("platform-audit-table");
    expect(screen.getAllByTestId(/platform-audit-row-/)).toHaveLength(2);
    // 同时展示 Actor 与 Subject（06 §17.2，AC⑧）
    expect(screen.getByTestId("platform-audit-actor-e1")).toHaveTextContent("User（u-psa）");
    expect(screen.getByTestId("platform-audit-subject-e1")).toHaveTextContent("Account（acc-1）");
    expect(screen.getByTestId("platform-audit-subject-e1")).toHaveTextContent("User（u-1）");

    // AC⑧：按目标 Account 筛选（Subject/Actor Account 匹配）
    fireEvent.change(screen.getByTestId("audit-filter-account"), { target: { value: "acc-1" } });
    expect(screen.queryByTestId("platform-audit-row-e2")).not.toBeInTheDocument();
    expect(screen.getByTestId("platform-audit-row-e1")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("audit-filter-account"), { target: { value: "acc-2" } });
    expect(screen.queryByTestId("platform-audit-row-e1")).not.toBeInTheDocument();
    expect(screen.getByTestId("platform-audit-row-e2")).toBeInTheDocument();
  });

  it("activity：复用 ActivityList 组件，可按目标 Account 过滤（服务端参数，AC⑩）", async () => {
    renderRouterAt("/platform/activity");
    expect(await screen.findByTestId("activity-list")).toBeInTheDocument();
    expect(screen.getByText("导入 Resource")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("platform-activity-account-filter"), {
      target: { value: "acc-1" },
    });
    await screen.findByTestId("activity-list");
    const activityCall = fetchMock.mock.calls.find(([url]) =>
      String(url).startsWith("/api/platform/v1/platform/activity?"),
    );
    expect(activityCall).toBeTruthy();
    expect(String(activityCall?.[0])).toContain("account_id=acc-1");
  });

  it("monitoring：平台聚合业务摘要，不含底层组件状态（05 §12.6，AC⑩）", async () => {
    renderRouterAt("/platform/monitoring");
    const summary = await screen.findByTestId("platform-monitoring-summary");
    expect(summary).toHaveTextContent("2/3");
    expect(summary).toHaveTextContent("8/10");
    expect(summary).toHaveTextContent("Resource 6 ｜ Skill 4");
    expect(screen.getByTestId("platform-monitoring-note")).toHaveTextContent("不展示底层组件状态");
    expect(screen.queryByText("VectorDB")).not.toBeInTheDocument();
  });

  it("recycle-bin：复用 RecycleBinPanel；Skill 只读不展示恢复入口（AC⑧⑩）", async () => {
    renderRouterAt("/platform/recycle-bin");
    expect(await screen.findByTestId("recycle-bin-panel")).toBeInTheDocument();
    // Resource：可恢复（restore_allowed）
    expect(screen.getByTestId("recycle-item-resource")).toBeInTheDocument();
    expect(screen.getByText("Del Doc")).toBeInTheDocument();
    // Skill：restore_allowed=false → 不展示（对 Skill 只读，10 §60，AC⑧）
    expect(screen.queryByText("Del Skill")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("restore-button-resource-res-1"));
    fireEvent.click(screen.getByTestId("restore-confirm"));
    // 恢复调用走平台端点（05 §12.6 注：类型化恢复权限由服务端校验）
    await new Promise((resolve) => setTimeout(resolve, 50));
    const restoreCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/platform/v1/platform/recycle-bin/job-res/restore" &&
        String(init?.method) === "POST",
    );
    expect(restoreCall).toBeTruthy();
  });

  it("api-keys：元数据掩码展示 + 撤销（AC③⑥ 平台版）", async () => {
    renderRouterAt("/platform/accounts/acc-1/users/u-1/api-keys");
    const row = await screen.findByTestId("platform-api-key-row-cred-1");
    expect(row).toHaveTextContent("ci-token");
    expect(screen.getByTestId("platform-api-key-mask-cred-1")).toHaveTextContent("4321");
    expect(screen.getByTestId("platform-api-keys-readonly-note")).toHaveTextContent("不能获取明文");

    fireEvent.click(screen.getByTestId("platform-api-key-revoke-cred-1"));
    fireEvent.click(screen.getByTestId("platform-api-key-revoke-confirm"));
    await screen.findByTestId("platform-api-keys-notice");
    expect(screen.getByTestId("platform-api-keys-notice")).toHaveTextContent("已撤销 API Key「ci-token」");
  });
});
