/**
 * Skill 私有分区页面集成测试（10 号文档，P3-E5 AC①②③④⑤⑥⑧）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { configurePlatformClient } from "@/lib/platform-client";
import { setAuthStateForTest, setCsrfToken } from "@/features/auth/auth-state";
import {
  ADMIN_ME,
  USER_ME,
  SKILL_ID,
  ZIP_ID,
  jsonResponse,
  renderRouterAt,
  skillRecord,
  zipSkillRecord,
  skillDetail,
} from "./test-utils";

describe("我的 Skill 列表（10 §53.2，AC①）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/me/skills")) {
        return jsonResponse(200, { status: "ok", result: { items: [skillRecord(), zipSkillRecord()], next_cursor: null } });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: USER_ME, sessionExpired: false });
  });

  afterEach(() => {
    setCsrfToken(null);
  });

  it("展示名称/描述/标签/归属/更新时间/辅助文件标识，无 URI 与控制文件（AC①）", async () => {
    renderRouterAt("/app/skills/private");
    expect(await screen.findByTestId("skill-row-11111111-1111-4111-8111-111111111111")).toBeInTheDocument();
    expect(screen.getByText("fix-me")).toBeInTheDocument();
    expect(screen.getByText("ZIP 技能包")).toBeInTheDocument();
    expect(screen.getAllByTestId("skill-tag-type=guide").length).toBe(2);
    expect(screen.getAllByText("我的私有").length).toBe(2);
    expect(screen.getByTestId("skill-files-22222222-2222-4222-8222-222222222222")).toHaveTextContent("含辅助文件");
    // 无 URI / 控制文件 / User ID
    expect(screen.queryByText(/viking:\/\//)).not.toBeInTheDocument();
    expect(screen.queryByText(/\.abstract\.md|\.overview\.md|\.source\.json|ov-u-1/)).not.toBeInTheDocument();
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("target_uri");
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("viking://");
  });

  it("列表行可跳详情（产品 ID 契约）", async () => {
    renderRouterAt("/app/skills/private");
    const link = await screen.findByTestId("skill-link-11111111-1111-4111-8111-111111111111");
    expect(link.getAttribute("href")).toBe("/app/skills/private/11111111-1111-4111-8111-111111111111");
  });

  it("裸路由 /app/skills 首次默认跳私有分区", async () => {
    renderRouterAt("/app/skills");
    expect(await screen.findByTestId("skills-private-page")).toBeInTheDocument();
  });

  it("裸路由 /app/skills 按最近打开分区跳转（platform.last-open-path）", async () => {
    setAuthStateForTest({ status: "authenticated", me: ADMIN_ME, sessionExpired: false });
    globalThis.localStorage.setItem("platform.last-open-path", JSON.stringify({ v: "/app/skills/shared" }));
    renderRouterAt("/app/skills");
    expect(await screen.findByTestId("shared-skills-manager")).toBeInTheDocument();
  });
});

describe("在线创建（10 §54.1，AC②③）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/me/skills") && url.endsWith("/me/skills")) {
        return jsonResponse(200, {
          status: "ok",
          result: { ...skillRecord({ name: "new-skill" }), id: "33333333-3333-4333-8333-333333333333" },
        });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: USER_ME, sessionExpired: false });
  });

  afterEach(() => {
    setCsrfToken(null);
  });

  it("表单提交不含手写 YAML、不含 target_uri；名称按 validate_skill_name 校验（AC②）", async () => {
    renderRouterAt("/app/skills/private/new");
    expect(await screen.findByTestId("skill-create-page")).toBeInTheDocument();
    // 表单不需要 frontmatter/YAML 输入（只提供结构化字段）
    expect(screen.queryByText(/frontmatter/i)).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/yaml|frontmatter/i)).not.toBeInTheDocument();

    fireEvent.change(screen.getByTestId("skill-name"), { target: { value: "bad name" } });
    fireEvent.change(screen.getByTestId("skill-description"), { target: { value: "描述" } });
    fireEvent.change(screen.getByTestId("skill-content"), { target: { value: "# 正文" } });
    fireEvent.click(screen.getByTestId("skill-submit"));
    expect(await screen.findByTestId("skill-form-error")).toHaveTextContent("名称仅允许 ASCII 字母、数字、下划线和连字符");
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId("skill-name"), { target: { value: "new-skill" } });
    fireEvent.change(screen.getByTestId("skill-tags"), { target: { value: "Type=Guide, project=openviking" } });
    fireEvent.change(screen.getByTestId("skill-tools"), { target: { value: "read_file, write_file" } });
    fireEvent.click(screen.getByTestId("skill-submit"));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const postCall = fetchMock.mock.calls.find(
      ([u, init]) => String(u).endsWith("/me/skills") && (init as RequestInit)?.method === "POST",
    );
    expect(postCall).toBeDefined();
    const body = JSON.parse(((postCall as unknown[])[1] as RequestInit).body as string);
    expect(body).toEqual({
      name: "new-skill",
      description: "描述",
      tags: ["type=guide", "project=openviking"],
      allowed_tools: ["read_file", "write_file"],
      content: "# 正文",
    });
    expect(body).not.toHaveProperty("target_uri");
    expect(body).not.toHaveProperty("visibility");
    expect(body).not.toHaveProperty("owner_user_id");
  });

  it("同名冲突展示统一文案，不泄露占用者（10 §55.1，AC③）", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(409, { status: "error", error: { code: "SKILL_NAME_CONFLICT" } }),
    );
    renderRouterAt("/app/skills/private/new");
    await screen.findByTestId("skill-create-page");
    fireEvent.change(screen.getByTestId("skill-name"), { target: { value: "taken" } });
    fireEvent.change(screen.getByTestId("skill-description"), { target: { value: "描述" } });
    fireEvent.change(screen.getByTestId("skill-content"), { target: { value: "# 正文" } });
    fireEvent.click(screen.getByTestId("skill-submit"));
    expect(await screen.findByText("该名称在当前 Account 不可用")).toBeInTheDocument();
    expect(screen.queryByText(/占用者|另一|用户 u/)).not.toBeInTheDocument();
  });
});

describe("上传创建（10 §54.2/§61.5）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/me/resource-uploads")) {
        return jsonResponse(200, {
          status: "ok",
          result: { upload_id: "99999999-9999-4999-8999-999999999999", filename: "skill.zip", mime_type: "application/zip", size_bytes: 1024, expires_at: "2026-09-01T00:00:00Z" },
        });
      }
      if (url.includes("/me/skills") && url.endsWith("/me/skills")) {
        return jsonResponse(200, { status: "ok", result: skillRecord({ id: "33333333-3333-4333-8333-333333333333" }) });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: USER_ME, sessionExpired: false });
  });

  afterEach(() => {
    setCsrfToken(null);
  });

  it("ZIP/SKILL.md 上传：先上传得 upload_id，再以 name+upload_id 创建；无 target_uri（AC②）", async () => {
    renderRouterAt("/app/skills/private/new");
    await screen.findByTestId("skill-create-page");
    fireEvent.click(screen.getByTestId("skill-create-tab-upload"));

    const file = new File(["dummy"], "skill.zip", { type: "application/zip" });
    fireEvent.change(screen.getByTestId("skill-upload-file"), { target: { files: [file] } });
    expect(await screen.findByTestId("skill-upload-file-hint")).toHaveTextContent("ZIP 包");

    fireEvent.change(screen.getByTestId("skill-upload-name"), { target: { value: "zip-pack" } });
    fireEvent.click(screen.getByTestId("skill-upload-submit"));

    await waitFor(() => {
      const uploadCall = fetchMock.mock.calls.find(([u]) => String(u).includes("/me/resource-uploads"));
      expect(uploadCall).toBeDefined();
      expect((uploadCall as unknown[])[1]).toBeInstanceOf(Object);
    });
    const createCall = fetchMock.mock.calls.find(
      ([u, init]) => String(u).endsWith("/me/skills") && (init as RequestInit)?.method === "POST",
    );
    expect(createCall).toBeDefined();
    const body = JSON.parse(((createCall as unknown[])[1] as RequestInit).body as string);
    expect(body).toEqual({ name: "zip-pack", upload_id: "99999999-9999-4999-8999-999999999999" });
    expect(body).not.toHaveProperty("target_uri");
  });
});

describe("详情页（10 §53.3/§56/§59，AC⑤）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes(`/me/skills/${SKILL_ID}`)) {
        return jsonResponse(200, {
          status: "ok",
          result: skillDetail({
            content: "# 使用说明\n\n按步骤执行。",
            files: [{ name: "fix.sh", size_bytes: 512 }],
          }),
        });
      }
      if (url.includes("/me/skill-configs/")) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            skill_id: SKILL_ID,
            configured: true,
            active_version: 1,
            latest_version: 2,
            versions: [1, 2],
            values: [
              { key: "api_token", configured: true, value_masked: "••••••••" },
              { key: "host", configured: true, value_masked: "••••••••" },
            ],
          },
        });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: USER_ME, sessionExpired: false });
  });

  afterEach(() => {
    setCsrfToken(null);
  });

  it("展示概览/使用说明/文件清单/工具范围/使用提示；无执行入口（10 §53.3，AC⑤）", async () => {
    renderRouterAt(`/app/skills/private/${SKILL_ID}`);
    expect(await screen.findByTestId("skill-detail")).toBeInTheDocument();
    expect(screen.getByTestId("skill-detail-name")).toHaveTextContent("fix-me");
    expect(screen.getByTestId("skill-content")).toHaveTextContent("# 使用说明");
    expect(screen.getByTestId("skill-file-fix.sh")).toBeInTheDocument();
    expect(screen.getByTestId("skill-tools-list")).toHaveTextContent("read_file");
    expect(screen.getByText(/不提供「在新 Session 中使用」或独立执行器/)).toBeInTheDocument();
    // 无执行入口按钮（AC⑤）
    expect(screen.queryByRole("button", { name: /在新 Session 中使用|运行 Skill|测试 Skill/ })).not.toBeInTheDocument();
    // 无 URI 与控制文件
    expect(screen.queryByText(/viking:\/\//)).not.toBeInTheDocument();
    expect(screen.queryByText(/\.abstract\.md|\.overview\.md|\.source\.json/)).not.toBeInTheDocument();
  });

  it("私密配置仅脱敏展示、可激活历史版本；不返回可恢复 Secret（05 §12.5，AC⑧）", async () => {
    renderRouterAt(`/app/skills/private/${SKILL_ID}`);
    expect(await screen.findByTestId("skill-config-panel")).toBeInTheDocument();
    expect(await screen.findByTestId("skill-config-status")).toHaveTextContent("已配置");
    expect(screen.getByTestId("skill-config-mask-api_token")).toHaveTextContent("••••••••");
    expect(screen.getByTestId("skill-config-mask-host")).toHaveTextContent("••••••••");
    // 无明文 Secret（DOM 与网络请求）
    expect(screen.queryByText(/sk-[A-Za-z0-9]+|Bearer |secret-value/i)).not.toBeInTheDocument();
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("sk-secret");

    // 激活历史版本 v1（当前激活 v1 已禁用；激活 v2）
    const activateV2 = screen.getByTestId("skill-config-activate-2");
    expect(activateV2).not.toBeDisabled();
    expect(screen.getByTestId("skill-config-activate-1")).toBeDisabled();
    fireEvent.click(activateV2);
    await waitFor(() => {
      expect(screen.getByTestId("skill-config-notice")).toHaveTextContent("已激活版本 v2");
    });
    const activateCall = fetchMock.mock.calls.find(([u]) => String(u).includes("/versions/2/activate"));
    expect(activateCall).toBeDefined();
  });

  it("保存新版本：PUT 提交完整值，响应只返回掩码（AC⑧）", async () => {
    let saveNext = false;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/me/skill-configs/") && method === "PUT") {
        saveNext = true;
        return jsonResponse(200, {
          status: "ok",
          result: {
            skill_id: SKILL_ID,
            configured: true,
            active_version: 3,
            latest_version: 3,
            versions: [1, 2, 3],
            values: [{ key: "api_token", configured: true, value_masked: "••••••••" }],
          },
        });
      }
      if (url.includes("/me/skill-configs/")) {
        return jsonResponse(200, {
          status: "ok",
          result: saveNext
            ? {
                skill_id: SKILL_ID,
                configured: true,
                active_version: 3,
                latest_version: 3,
                versions: [1, 2, 3],
                values: [{ key: "api_token", configured: true, value_masked: "••••••••" }],
              }
            : {
                skill_id: SKILL_ID,
                configured: true,
                active_version: 2,
                latest_version: 2,
                versions: [1, 2],
                values: [{ key: "api_token", configured: true, value_masked: "••••••••" }],
              },
        });
      }
      if (url.includes(`/me/skills/${SKILL_ID}`)) {
        return jsonResponse(200, { status: "ok", result: skillDetail() });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });

    renderRouterAt(`/app/skills/private/${SKILL_ID}`);
    await screen.findByTestId("skill-config-status");
    fireEvent.click(screen.getByTestId("skill-config-edit"));
    fireEvent.change(screen.getByTestId("skill-config-lines"), {
      target: { value: "api_token=sk-new-secret\nhost=example.com" },
    });
    fireEvent.click(screen.getByTestId("skill-config-save"));
    await waitFor(() => {
      expect(screen.getByTestId("skill-config-notice")).toHaveTextContent("已保存新版本 v3");
    });
    const putCall = fetchMock.mock.calls.find(
      ([u, init]) => String(u).includes("/me/skill-configs/") && (init as RequestInit)?.method === "PUT",
    );
    const putBody = JSON.parse(((putCall as unknown[])[1] as RequestInit).body as string);
    expect(putBody).toEqual({ values: { api_token: "sk-new-secret", host: "example.com" } });
    // 保存后仅掩码展示，明文不出现在任何请求响应/DOM
    expect(screen.queryByText("sk-new-secret")).not.toBeInTheDocument();
  });

  it("在线编辑：name 只读不可变，PUT 请求体不含 name（AC③）", async () => {
    let updated = false;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes(`/me/skills/${SKILL_ID}`) && method === "PUT") {
        updated = true;
        return jsonResponse(200, {
          status: "ok",
          result: skillDetail({ description: "新描述", tags: ["type=updated"] }),
        });
      }
      if (url.includes("/me/skill-configs/")) {
        return jsonResponse(200, {
          status: "ok",
          result: { skill_id: SKILL_ID, configured: false, active_version: null, latest_version: null, versions: [], values: [] },
        });
      }
      if (url.includes(`/me/skills/${SKILL_ID}`)) {
        return jsonResponse(200, {
          status: "ok",
          result: updated ? skillDetail({ description: "新描述", tags: ["type=updated"] }) : skillDetail(),
        });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });

    renderRouterAt(`/app/skills/private/${SKILL_ID}`);
    expect(await screen.findByTestId("skill-detail")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("skill-edit"));
    expect(await screen.findByTestId("skill-name-readonly")).toHaveValue("fix-me");
    expect(screen.queryByTestId("skill-name")).not.toBeInTheDocument();

    fireEvent.change(screen.getByTestId("skill-description"), { target: { value: "新描述" } });
    fireEvent.change(screen.getByTestId("skill-tags"), { target: { value: "type=updated" } });
    fireEvent.click(screen.getByTestId("skill-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("skill-detail-name")).toHaveTextContent("fix-me");
    });
    const putCall = fetchMock.mock.calls.find(
      ([u, init]) => String(u).includes(`/me/skills/${SKILL_ID}`) && (init as RequestInit)?.method === "PUT",
    );
    const putBody = JSON.parse(((putCall as unknown[])[1] as RequestInit).body as string);
    expect(putBody).not.toHaveProperty("name");
    expect(putBody).toMatchObject({ description: "新描述", tags: ["type=updated"] });
    expect(screen.getByTestId("skill-detail-name")).toHaveTextContent("fix-me");
  });

  it("软删：确认弹窗 → DELETE → 删除视图展示恢复截止与恢复入口；恢复成功回详情（10 §59，AC⑥）", async () => {
    let deleted = false;
    let restored = false;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes(`/me/skills/${SKILL_ID}`) && method === "DELETE") {
        deleted = true;
        return jsonResponse(200, {
          status: "ok",
          result: {
            resource_type: "skill",
            resource_id: SKILL_ID,
            deletion_job_id: "job-1",
            deleted_at: "2026-08-19T09:00:00Z",
            restore_until: "2026-09-18T09:00:00Z",
          },
        });
      }
      if (url.includes(`/me/skills/${SKILL_ID}/restore`) && method === "POST") {
        restored = true;
        return jsonResponse(200, {
          status: "ok",
          result: {
            resource_type: "skill",
            resource_id: SKILL_ID,
            deletion_job_id: "job-1",
            deleted_at: "2026-08-19T09:00:00Z",
            restore_until: "2026-09-18T09:00:00Z",
          },
        });
      }
      if (url.includes("/me/skill-configs/")) {
        return jsonResponse(200, {
          status: "ok",
          result: { skill_id: SKILL_ID, configured: false, active_version: null, latest_version: null, versions: [], values: [] },
        });
      }
      if (url.includes(`/me/skills/${SKILL_ID}`) && !restored) {
        return deleted
          ? jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } })
          : jsonResponse(200, { status: "ok", result: skillDetail() });
      }
      return jsonResponse(200, { status: "ok", result: skillDetail() });
    });

    renderRouterAt(`/app/skills/private/${SKILL_ID}`);
    await screen.findByTestId("skill-detail");
    fireEvent.click(screen.getByTestId("skill-delete"));
    expect(screen.getByTestId("skill-delete-dialog")).toHaveTextContent("名称将立即释放");
    fireEvent.click(screen.getByTestId("skill-delete-confirm"));

    expect(await screen.findByTestId("skill-deleted-view")).toBeInTheDocument();
    expect(screen.getByText(/恢复截止时间：2026\/9\/18/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("skill-restore"));
    expect(await screen.findByTestId("skill-detail")).toBeInTheDocument();
    expect(screen.getByTestId("skill-detail-name")).toHaveTextContent("fix-me");
  });

  it("恢复同名冲突：保持删除状态并展示冲突文案（10 §55.3，AC⑥）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes(`/me/skills/${SKILL_ID}`) && method === "DELETE") {
        return jsonResponse(200, {
          status: "ok",
          result: { resource_type: "skill", resource_id: SKILL_ID, deletion_job_id: "job-1", deleted_at: "2026-08-19T09:00:00Z", restore_until: "2026-09-18T09:00:00Z" },
        });
      }
      if (url.includes(`/me/skills/${SKILL_ID}/restore`)) {
        return jsonResponse(409, { status: "error", error: { code: "SKILL_NAME_CONFLICT" } });
      }
      if (url.includes("/me/skill-configs/")) {
        return jsonResponse(200, {
          status: "ok",
          result: { skill_id: SKILL_ID, configured: false, active_version: null, latest_version: null, versions: [], values: [] },
        });
      }
      if (url.includes(`/me/skills/${SKILL_ID}`)) {
        return jsonResponse(200, { status: "ok", result: skillDetail() });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });

    renderRouterAt(`/app/skills/private/${SKILL_ID}`);
    await screen.findByTestId("skill-detail");
    fireEvent.click(screen.getByTestId("skill-delete"));
    fireEvent.click(screen.getByTestId("skill-delete-confirm"));
    await screen.findByTestId("skill-deleted-view");
    fireEvent.click(screen.getByTestId("skill-restore"));

    expect(await screen.findByTestId("skill-restore-error")).toHaveTextContent(
      "该名称在当前 Account 不可用，原 Skill 保持在回收站，不会覆盖同名 Skill",
    );
    // 保持删除状态
    expect(screen.getByTestId("skill-deleted-view")).toBeInTheDocument();
  });
});

describe("详情页 404 语义（跳转契约 lib/links.ts）", () => {
  it("已删/无权 Skill → NOT_FOUND 展示", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } }),
    );
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: USER_ME, sessionExpired: false });
    renderRouterAt("/app/skills/private/11111111-1111-4111-8111-111111111111");
    expect(await screen.findByTestId("skill-not-found")).toBeInTheDocument();
    setCsrfToken(null);
  });
});

describe("ZIP Skill 整体替换（10 §56.2，AC④）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/me/resource-uploads")) {
        return jsonResponse(200, {
          status: "ok",
          result: { upload_id: "99999999-9999-4999-8999-999999999999", filename: "skill.zip", mime_type: "application/zip", size_bytes: 2048, expires_at: "2026-09-01T00:00:00Z" },
        });
      }
      if (url.includes("/me/skill-configs/")) {
        return jsonResponse(200, {
          status: "ok",
          result: { skill_id: ZIP_ID, configured: false, active_version: null, latest_version: null, versions: [], values: [] },
        });
      }
      if (url.includes(`/me/skills/${ZIP_ID}`)) {
        return jsonResponse(200, {
          status: "ok",
          result: {
            ...zipSkillRecord(),
            content: "# zip 包",
            allowed_tools: [],
            files: [
              { name: "SKILL.md", size_bytes: 200 },
              { name: "script.py", size_bytes: 1024 },
            ],
          },
        });
      }
      return jsonResponse(404, { status: "error", error: { code: "NOT_FOUND" } });
    });
    configurePlatformClient({ fetchImpl: fetchMock });
    setAuthStateForTest({ status: "authenticated", me: USER_ME, sessionExpired: false });
  });

  afterEach(() => {
    setCsrfToken(null);
  });

  it("ZIP Skill 不提供逐文件编辑，仅整体重传；替换请求只含 upload_id（AC④）", async () => {
    renderRouterAt(`/app/skills/private/${ZIP_ID}`);
    expect(await screen.findByTestId("skill-detail")).toBeInTheDocument();
    expect(screen.getByTestId("skill-file-SKILL.md")).toBeInTheDocument();
    expect(screen.getByTestId("skill-file-script.py")).toBeInTheDocument();
    expect(screen.queryByTestId("skill-edit")).not.toBeInTheDocument();
    expect(screen.getByTestId("skill-replace")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("skill-replace"));
    const file = new File(["zip"], "skill.zip", { type: "application/zip" });
    fireEvent.change(screen.getByTestId("skill-upload-file"), { target: { files: [file] } });
    fireEvent.click(screen.getByTestId("skill-upload-submit"));

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(
        ([u, init]) => String(u).includes(`/me/skills/${ZIP_ID}`) && (init as RequestInit)?.method === "PUT",
      );
      expect(putCall).toBeDefined();
      const putBody = JSON.parse(((putCall as unknown[])[1] as RequestInit).body as string);
      expect(putBody).toEqual({ upload_id: "99999999-9999-4999-8999-999999999999" });
      expect(putBody).not.toHaveProperty("name");
    });
  });

  it("新包名称不一致 → SKILL_NAME_IMMUTABLE 错误展示（10 §61.1 注，AC④）", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/me/resource-uploads")) {
        return jsonResponse(200, {
          status: "ok",
          result: { upload_id: "99999999-9999-4999-8999-999999999999", filename: "skill.zip", mime_type: "application/zip", size_bytes: 2048, expires_at: "2026-09-01T00:00:00Z" },
        });
      }
      if (url.includes(`/me/skills/${ZIP_ID}`) && method === "PUT") {
        return jsonResponse(409, { status: "error", error: { code: "SKILL_NAME_IMMUTABLE" } });
      }
      if (url.includes(`/me/skills/${ZIP_ID}`)) {
        return jsonResponse(200, {
          status: "ok",
          result: { ...zipSkillRecord(), content: "# zip 包", allowed_tools: [], files: [] },
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

    renderRouterAt(`/app/skills/private/${ZIP_ID}`);
    await screen.findByTestId("skill-detail");
    fireEvent.click(screen.getByTestId("skill-replace"));
    const file = new File(["zip"], "skill.zip", { type: "application/zip" });
    fireEvent.change(screen.getByTestId("skill-upload-file"), { target: { files: [file] } });
    fireEvent.click(screen.getByTestId("skill-upload-submit"));
    expect(await screen.findByText("Skill 名称创建后不可修改（名称不可变）")).toBeInTheDocument();
    expect(screen.getByTestId("skill-replace-form")).toBeInTheDocument();
  });
});
