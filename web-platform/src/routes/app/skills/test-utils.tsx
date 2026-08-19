/**
 * Skill 页面测试共用设施（非测试文件，vitest include 不收集）。
 */

import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { createAppRouter } from "@/router";
import type { AuthMeResult } from "@/features/auth/auth-state";

export const USER_ME: AuthMeResult = {
  account: { id: "acc-1" },
  user: { id: "u-1", ov_user_id: "ov-u-1" },
  roles: ["user"],
  permissions: [
    "skill.user_private.read.self",
    "skill.user_private.manage.self",
    "skill.account_shared.read.account",
    "privacy_config.read.self",
    "privacy_config.write.self",
  ],
  can_switch_account: false,
  csrf_token: null,
};

export const ADMIN_ME: AuthMeResult = {
  ...USER_ME,
  roles: ["account_admin"],
  permissions: [...USER_ME.permissions, "skill.account_shared.manage.account"],
};

export const SKILL_ID = "11111111-1111-4111-8111-111111111111";
export const ZIP_ID = "22222222-2222-4222-8222-222222222222";

export function skillRecord(overrides: Record<string, unknown> = {}) {
  return {
    id: SKILL_ID,
    name: "fix-me",
    description: "修复指南",
    tags: ["type=guide", "project=openviking"],
    visibility: "user_private",
    source_type: "online",
    has_auxiliary_files: false,
    status: "active",
    version: 1,
    created_at: "2026-08-18T08:00:00Z",
    updated_at: "2026-08-19T08:00:00Z",
    ...overrides,
  };
}

export function zipSkillRecord() {
  return {
    ...skillRecord({
      id: ZIP_ID,
      name: "zip-pack",
      description: "ZIP 技能包",
      source_type: "zip",
      has_auxiliary_files: true,
    }),
  };
}

export function skillDetail(overrides: Record<string, unknown> = {}) {
  return {
    ...skillRecord(),
    content: "# 使用说明\n\n按步骤执行。",
    allowed_tools: ["read_file"],
    files: [],
    ...overrides,
  };
}

export function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export function renderRouterAt(path: string) {
  const router = createAppRouter({ history: createMemoryHistory({ initialEntries: [path] }) });
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}
