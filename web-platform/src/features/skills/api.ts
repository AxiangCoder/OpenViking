/**
 * Skill 产品 API 数据层（10 §61.1–§61.5，05 §12.5，P3-E5）。
 *
 * - `/me/*` 固定当前 User 私有目标；`/account/*` 固定当前 Account 共享目标；
 *   前端不提交 `target_uri`/`visibility`/`owner_user_id`（10 §54.1/§54.2，AC②）；
 * - 上传复用 `me|account resource-uploads` 的一次性 `upload_id`（10 §61.5）；
 * - 私密配置三接口仅当前 User 管理自己的配置，响应只含掩码值（AC⑤⑧）；
 * - 错误映射（10 §63）：同名冲突统一返回「该名称在当前 Account 不可用」，
 *   不泄露占用者（AC③）。
 */

import { request, requestForm, PlatformError } from "@/lib/platform-client";
import type {
  SkillConfigSnapshot,
  SkillDeletionResult,
  SkillDetail,
  SkillListResult,
  SkillOnlineInput,
  SkillUploadResult,
} from "./types";

/** 10 §55.1/§63：同名冲突对普通 User 只说明名称不可用，不泄露占用者（AC③）。 */
export const SKILL_NAME_CONFLICT_MESSAGE = "该名称在当前 Account 不可用";
export const SKILL_NAME_IMMUTABLE_MESSAGE = "Skill 名称创建后不可修改（名称不可变）";

export type SkillScope = "me" | "account";

function listPath(scope: SkillScope): string {
  return scope === "me" ? "/api/platform/v1/me/skills" : "/api/platform/v1/account/skills";
}

function detailPath(scope: SkillScope, skillId: string): string {
  return `${listPath(scope)}/${skillId}`;
}

export async function listSkills(scope: SkillScope): Promise<SkillListResult> {
  return request<SkillListResult>(listPath(scope));
}

export async function getSkill(scope: SkillScope, skillId: string): Promise<SkillDetail> {
  return request<SkillDetail>(detailPath(scope, skillId));
}

/** 在线创建（10 §54.1：name/description/content 必填，不提交 URI/visibility）。 */
export async function createSkillOnline(
  scope: SkillScope,
  input: SkillOnlineInput,
): Promise<SkillDetail> {
  return request<SkillDetail>(listPath(scope), {
    method: "POST",
    body: { ...input, tags: input.tags, allowed_tools: input.allowed_tools },
  });
}

/** 上传消费创建：先 `upload*` 得 `upload_id`，再以 name+upload_id 创建（10 §61.5）。 */
export async function createSkillFromUpload(
  scope: SkillScope,
  name: string,
  uploadId: string,
): Promise<SkillDetail> {
  return request<SkillDetail>(listPath(scope), {
    method: "POST",
    body: { name, upload_id: uploadId },
  });
}

/** 在线整体更新（10 §55.2/§56.1：DTO 不接受 name，AC③）。 */
export async function updateSkillOnline(
  scope: SkillScope,
  skillId: string,
  input: SkillOnlineInput,
): Promise<SkillDetail> {
  const { allowed_tools, ...rest } = input;
  return request<SkillDetail>(detailPath(scope, skillId), {
    method: "PUT",
    body: { ...rest, allowed_tools },
  });
}

/** ZIP/SKILL.md 整体替换（10 §56.2/§61.1 注：新包 name 必须一致，否则 SKILL_NAME_IMMUTABLE，AC④）。 */
export async function replaceSkillFromUpload(
  scope: SkillScope,
  skillId: string,
  uploadId: string,
): Promise<SkillDetail> {
  return request<SkillDetail>(detailPath(scope, skillId), {
    method: "PUT",
    body: { upload_id: uploadId },
  });
}

/** 软删除（10 §59；返回 restore_until=30 天恢复截止，AC⑥）。 */
export async function softDeleteSkill(scope: SkillScope, skillId: string): Promise<SkillDeletionResult> {
  return request<SkillDeletionResult>(detailPath(scope, skillId), { method: "DELETE" });
}

/** 恢复（10 §55.3/§59；同名已占用 → SKILL_NAME_CONFLICT，原对象保持在回收站，AC⑥）。 */
export async function restoreSkill(scope: SkillScope, skillId: string): Promise<SkillDeletionResult> {
  return request<SkillDeletionResult>(`${detailPath(scope, skillId)}/restore`, { method: "POST" });
}

/** 一次性上传（05 §12.5 `resource-uploads`；scope 决定绑定目标，上传 ID 不能跨入口消费）。 */
export async function uploadSkillFile(
  scope: SkillScope,
  file: File,
): Promise<SkillUploadResult> {
  const form = new FormData();
  form.append("file", file);
  const path =
    scope === "me"
      ? "/api/platform/v1/me/resource-uploads"
      : "/api/platform/v1/account/resource-uploads";
  return requestForm<SkillUploadResult>(path, form);
}

// ── 05 §12.5 me/skill-configs 三接口（仅当前 User，AC⑧）──

export async function getSkillConfig(skillId: string): Promise<SkillConfigSnapshot> {
  return request<SkillConfigSnapshot>(`/api/platform/v1/me/skill-configs/${skillId}`);
}

/** 保存新版本（values 为完整 key→secret 提交；响应只返回掩码）。 */
export async function putSkillConfig(skillId: string, values: Record<string, string>): Promise<SkillConfigSnapshot> {
  return request<SkillConfigSnapshot>(`/api/platform/v1/me/skill-configs/${skillId}`, {
    method: "PUT",
    body: { values },
  });
}

/** 激活历史版本（版本不存在 → CONFIG_VERSION_NOT_FOUND）。 */
export async function activateSkillConfig(skillId: string, version: number): Promise<SkillConfigSnapshot> {
  return request<SkillConfigSnapshot>(
    `/api/platform/v1/me/skill-configs/${skillId}/versions/${version}/activate`,
    { method: "POST" },
  );
}

/** 稳定错误 → 用户可见文案（10 §63；同名冲突不泄露占用者，AC③）。 */
export function skillErrorMessage(error: unknown): string {
  if (error instanceof PlatformError) {
    switch (error.code) {
      case "SKILL_NAME_CONFLICT":
        return SKILL_NAME_CONFLICT_MESSAGE;
      case "SKILL_NAME_IMMUTABLE":
        return SKILL_NAME_IMMUTABLE_MESSAGE;
      case "SKILL_INVALID_FORMAT":
        return "Skill 包格式不合法：请检查 SKILL.md 结构或 ZIP 内容";
      case "SKILL_SHARED_WRITE_FORBIDDEN":
        return "普通 User 不能写入共享 Skill";
      case "SKILL_PRIVATE_MANAGE_FORBIDDEN":
        return "无权编辑、删除或恢复他人的私有 Skill";
      case "SKILL_PUBLISH_FORBIDDEN":
        return "无权发布该 Skill";
      case "SKILL_UNPUBLISH_UNSUPPORTED":
        return "v0.1 不支持取消发布或共享转私有";
      case "CONFIG_INVALID":
        return "私密配置格式不合法：需提供非空 key/value";
      case "CONFIG_VERSION_NOT_FOUND":
        return "该配置版本不存在";
      case "UPLOAD_EXPIRED":
        return "上传已过期，请重新上传文件";
      case "UPLOAD_CONSUMED":
        return "该上传已被使用，请重新上传文件";
      case "UPLOAD_NOT_FOUND":
        return "上传不存在，请重新上传文件";
      case "UPLOAD_SCOPE_MISMATCH":
        return "上传与目标范围不匹配，请重新上传文件";
      case "NOT_RESTORABLE":
        return "该 Skill 当前不可恢复";
      case "RESTORE_WINDOW_EXPIRED":
        return "恢复窗口（30 天）已过期，无法恢复";
      default:
        return error.message || error.code;
    }
  }
  return "操作失败，请稍后重试";
}
