/**
 * 共享 Skill 页面集成测试（10 号文档，P3-E5 AC④⑦）。
 *
 * 覆盖：普通 User 共享页只读（无管理按钮 + 管理员维护提示）；Account Admin
 * 在 /app 共享页看到与 /admin 相同的管理组件；共享新增固定进共享区；共享详情
 * 普通 User 无管理操作、Admin 可编辑/替换/删除；共享 SKILL_NAME_IMMUTABLE。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, setCsrfToken } from "@/features/auth/auth-state";
import {
  ADMIN_ME,
  USER_ME,
  ZIP_ID,
  jsonResponse,
  renderRouterAt,
  skillRecord,
  zipSkillRecord,
} from "./test-utils";

const SHARED = "33333333-3333-4333-8333-333333333333";

function sharedSkillRecord() {
  return skillRecord({ id: SHARED, name: "shared-guide", visibility: "account_shared" });
}

describe("共享 Skill 分区（06 §13.3，AC⑦）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/account/skills")) {
        return jsonResponse(200, { status: "ok", result: { items: [sharedSkillRecord()], next_cursor: null } });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
  });

  afterEach(() => {
    setCsrfToken(null);
  });

  it("普通 User：只读列表、无管理按钮、展示管理员维护提示（AC⑦）", async () => {
    setAuthStateForTest({ status: "authenticated", me: USER_ME, sessionExpired: false });
    renderRouterAt("/app/skills/shared");
    expect(await screen.findByTestId("skills-shared-page")).toBeInTheDocument();
    expect(screen.getByTestId("skill-row-33333333-3333-4333-8333-333333333333")).toBeInTheDocument();
    expect(screen.getByTestId("skills-list-hint")).toHaveTextContent("共享内容由 Account 管理员维护");
    expect(screen.queryByTestId("shared-skill-new")).not.toBeInTheDocument();
    expect(screen.queryByText(/新建共享 Skill|编辑|删除/)).not.toBeInTheDocument();
  });

  it("Account Admin：/app 共享页渲染与 /admin 相同的管理组件，新建固定进共享区（AC⑦）", async () => {
    setAuthStateForTest({ status: "authenticated", me: ADMIN_ME, sessionExpired: false });
    renderRouterAt("/app/skills/shared");
    expect(await screen.findByTestId("shared-skills-manager")).toBeInTheDocument();
    const newLink = screen.getByTestId("shared-skill-new");
    expect(newLink.getAttribute("href")).toBe("/admin/shared-skills/new");

    // /admin 挂载点复用同一组件（后续 Epic 只复用不重写）
    cleanup();
    renderRouterAt("/admin/shared-skills");
    expect(await screen.findByTestId("shared-skills-manager")).toBeInTheDocument();
    expect(screen.getByTestId("shared-skill-new").getAttribute("href")).toBe("/admin/shared-skills/new");
  });
});

describe("共享 Skill 创建（10 §54.1，AC⑦）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/account/skills") && url.endsWith("/account/skills")) {
        return jsonResponse(200, { status: "ok", result: sharedSkillRecord() });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: ADMIN_ME, sessionExpired: false });
  });

  afterEach(() => {
    setCsrfToken(null);
  });

  it("在线创建提交到 /account/skills（固定共享区），请求体无 visibility/owner_user_id（AC⑦）", async () => {
    renderRouterAt("/admin/shared-skills/new");
    expect(await screen.findByTestId("skill-create-page")).toBeInTheDocument();
    expect(screen.getByTestId("skill-create-title")).toHaveTextContent("新建共享 Skill");
    expect(screen.getByTestId("skill-create-scope-hint")).toHaveTextContent("固定进入当前 Account 共享区");

    fireEvent.change(screen.getByTestId("skill-name"), { target: { value: "shared-guide" } });
    fireEvent.change(screen.getByTestId("skill-description"), { target: { value: "团队指南" } });
    fireEvent.change(screen.getByTestId("skill-content"), { target: { value: "# 指南" } });
    fireEvent.click(screen.getByTestId("skill-submit"));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([u, init]) => String(u).endsWith("/account/skills") && (init as RequestInit)?.method === "POST",
      );
      expect(postCall).toBeDefined();
      const body = JSON.parse(((postCall as unknown[])[1] as RequestInit).body as string);
      expect(body.name).toBe("shared-guide");
      expect(body).not.toHaveProperty("visibility");
      expect(body).not.toHaveProperty("owner_user_id");
      expect(body).not.toHaveProperty("target_uri");
    });
  });
});

describe("共享 Skill 详情（10 §53.3，AC⑤⑦）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  function detailMock(input: RequestInfo | URL, init?: RequestInit) {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url.includes(`/account/skills/${SHARED}`) && method === "DELETE") {
      return jsonResponse(200, {
        status: "ok",
        result: { resource_type: "skill", resource_id: SHARED, deletion_job_id: "job-s1", deleted_at: "2026-08-19T09:00:00Z", restore_until: "2026-09-18T09:00:00Z" },
      });
    }
    if (url.includes("/me/skill-configs/")) {
      return jsonResponse(200, {
        status: "ok",
        result: { skill_id: SHARED, configured: false, active_version: null, latest_version: null, versions: [], values: [] },
      });
    }
    if (url.includes(`/account/skills/${SHARED}`)) {
      return jsonResponse(200, {
        status: "ok",
        result: { ...sharedSkillRecord(), content: "# 共享指南", allowed_tools: ["read_file"], files: [] },
      });
    }
    return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
  }

  beforeEach(() => {
    fetchMock = vi.fn(detailMock);
    configurePlatformClient({ fetchImpl: fetchMock });
  });

  afterEach(() => {
    setCsrfToken(null);
  });

  it("普通 User：共享详情只读，无编辑/替换/删除管理区，但可管理自己的私密配置（AC⑤⑦）", async () => {
    setAuthStateForTest({ status: "authenticated", me: USER_ME, sessionExpired: false });
    renderRouterAt(`/app/skills/shared/${SHARED}`);
    expect(await screen.findByTestId("skill-detail")).toBeInTheDocument();
    expect(screen.getByTestId("skill-detail-name")).toHaveTextContent("shared-guide");
    expect(screen.queryByTestId("skill-manage-section")).not.toBeInTheDocument();
    expect(screen.queryByTestId("skill-edit")).not.toBeInTheDocument();
    expect(screen.queryByTestId("skill-delete")).not.toBeInTheDocument();
    // 自己的私密配置入口仍存在（05 §12.5：配置目标含当前 Account 共享 Skill）
    expect(screen.getByTestId("skill-config-section")).toBeInTheDocument();
  });

  it("Account Admin：共享详情可编辑/整体替换/删除（AC⑦）", async () => {
    setAuthStateForTest({ status: "authenticated", me: ADMIN_ME, sessionExpired: false });
    renderRouterAt(`/app/skills/shared/${SHARED}`);
    expect(await screen.findByTestId("skill-detail")).toBeInTheDocument();
    expect(screen.getByTestId("skill-manage-section")).toBeInTheDocument();
    expect(screen.getByTestId("skill-edit")).toBeInTheDocument();
    expect(screen.getByTestId("skill-delete")).toBeInTheDocument();
  });

  it("Account Admin 软删共享 Skill 后可恢复（10 §59）", async () => {
    let deleted = false;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes(`/account/skills/${SHARED}`) && method === "DELETE") {
        deleted = true;
        return jsonResponse(200, {
          status: "ok",
          result: { resource_type: "skill", resource_id: SHARED, deletion_job_id: "job-s1", deleted_at: "2026-08-19T09:00:00Z", restore_until: "2026-09-18T09:00:00Z" },
        });
      }
      if (url.includes(`/account/skills/${SHARED}/restore`) && method === "POST") {
        deleted = false;
        return jsonResponse(200, {
          status: "ok",
          result: { resource_type: "skill", resource_id: SHARED, deletion_job_id: "job-s1", deleted_at: "2026-08-19T09:00:00Z", restore_until: "2026-09-18T09:00:00Z" },
        });
      }
      if (url.includes("/me/skill-configs/")) {
        return jsonResponse(200, {
          status: "ok",
          result: { skill_id: SHARED, configured: false, active_version: null, latest_version: null, versions: [], values: [] },
        });
      }
      if (url.includes(`/account/skills/${SHARED}`)) {
        return deleted
          ? jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } })
          : jsonResponse(200, { status: "ok", result: { ...sharedSkillRecord(), content: "# 共享指南", allowed_tools: [], files: [] } });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });
    setAuthStateForTest({ status: "authenticated", me: ADMIN_ME, sessionExpired: false });
    renderRouterAt(`/app/skills/shared/${SHARED}`);
    await screen.findByTestId("skill-detail");
    fireEvent.click(screen.getByTestId("skill-delete"));
    fireEvent.click(screen.getByTestId("skill-delete-confirm"));
    expect(await screen.findByTestId("skill-deleted-view")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("skill-restore"));
    expect(await screen.findByTestId("skill-detail")).toBeInTheDocument();
    expect(screen.getByTestId("skill-detail-name")).toHaveTextContent("shared-guide");
  });
});

describe("共享 ZIP Skill 整体替换（10 §56.2，AC④⑦）", () => {
  it("Admin 对共享 ZIP Skill 仅整体重传，新包 name 不一致 → SKILL_NAME_IMMUTABLE", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/account/resource-uploads")) {
        return jsonResponse(200, {
          status: "ok",
          result: { upload_id: "99999999-9999-4999-8999-999999999999", filename: "skill.zip", mime_type: "application/zip", size_bytes: 2048, expires_at: "2026-09-01T00:00:00Z" },
        });
      }
      if (url.includes(`/account/skills/${ZIP_ID}`) && method === "PUT") {
        return jsonResponse(409, { status: "error", error: { code: "SKILL_NAME_IMMUTABLE" } });
      }
      if (url.includes(`/account/skills/${ZIP_ID}`)) {
        return jsonResponse(200, {
          status: "ok",
          result: { ...zipSkillRecord(), visibility: "account_shared", content: "# zip", allowed_tools: [], files: [] },
        });
      }
      if (url.includes("/me/skill-configs/")) {
        return jsonResponse(200, {
          status: "ok",
          result: { skill_id: ZIP_ID, configured: false, active_version: null, latest_version: null, versions: [], values: [] },
        });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: ADMIN_ME, sessionExpired: false });

    renderRouterAt(`/admin/shared-skills/${ZIP_ID}`);
    expect(await screen.findByTestId("skill-detail")).toBeInTheDocument();
    expect(screen.queryByTestId("skill-edit")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("skill-replace"));
    const file = new File(["zip"], "skill.zip", { type: "application/zip" });
    fireEvent.change(screen.getByTestId("skill-upload-file"), { target: { files: [file] } });
    fireEvent.click(screen.getByTestId("skill-upload-submit"));
    expect(await screen.findByText("Skill 名称创建后不可修改（名称不可变）")).toBeInTheDocument();

    const putCall = fetchMock.mock.calls.find(
      ([u, init]) => String(u).includes(`/account/skills/${ZIP_ID}`) && (init as RequestInit)?.method === "PUT",
    );
    const putBody = JSON.parse(((putCall as unknown[])[1] as RequestInit).body as string);
    expect(putBody).toEqual({ upload_id: "99999999-9999-4999-8999-999999999999" });
    expect(putBody).not.toHaveProperty("name");
    setCsrfToken(null);
  });
});
