/**
 * Account Admin 成员数据只读数据层（05 §12.6、13 §84.2/§86，14 号计划 §98.7，P4-E2）。
 *
 * - 全部接口固定 `/admin/users/{userId}/...`：Account 固定来自登录 Session，
 *   路径中的用户只作为 Subject 且必须属于该 Account（05 §12.6，AC①）；
 * - 只读检索（POST search/find）、Session 历史、Resource/Skill 只读预览：
 *   请求体复用产品白名单（SearchParams），不提交 target_uri/score_threshold
 *   等底层字段；无下载/导出/修改/删除端点（84.2，AC②）；
 * - 成员 Skill 发布（10 §58.4）：两段式（PG 归属转换 + 后台迁移 Operation），
 *   成功响应只返回 skill_id/operation_id/status/visibility（不含明文内容）；
 *   同步失败（SKILL_NAME_CONFLICT/SKILL_PUBLISH_FORBIDDEN 等）可重试；
 * - API Key 只读元数据 + 撤销：列表只返回名称/掩码/状态/使用时间（AC③），
 *   无明文、无代创建入口；撤销幂等（KEY_NOT_FOUND 视为已撤销成功）。
 */

import { PlatformError, request } from "@/lib/platform-client";
import { buildSearchBody, type SearchHit, type SearchParams } from "@/features/search/search";
import type {
  MemoryImpact,
  SessionDetail,
  SessionListItem,
  SessionMessage,
} from "@/features/sessions/sessions";
import type { ResourceDetail, ResourceSummary } from "@/features/resources";
import type { SkillDetail, SkillRecord } from "@/features/skills";
import type { ApiKeyRecord } from "@/features/profile/api-keys";

export interface MemberPageResult<T> {
  items: T[];
  next_cursor: string | null;
}

/** 成员 API Key 元数据（admin api_key_dto：额外含 revoked_at，无明文）。 */
export interface MemberApiKeyRecord extends ApiKeyRecord {
  revoked_at: string | null;
}

/** 成员 Skill 发布结果（10 §58.4：归属转换已提交，迁移后台执行）。 */
export interface MemberSkillPublishResult {
  skill_id: string;
  operation_id: string;
  status: string;
  visibility: string;
}

export interface MemberApiKeyRevokeResult {
  id: string;
  name: string;
  revoked: boolean;
}

function memberPath(userId: string, tail: string): string {
  return `/api/platform/v1/admin/users/${userId}${tail}`;
}

/** 成员只读检索（11 §74.1：`memory.read.account` 等目标对象读取权限）。 */
export async function memberSearchFind(userId: string, params: SearchParams): Promise<SearchHit[]> {
  const result = await request<{ items: SearchHit[] }>(memberPath(userId, "/search/find"), {
    method: "POST",
    body: buildSearchBody(params),
  });
  return result.items;
}

/** 成员 Session 列表（11 §74.2：`session.read.account`，只读）。 */
export async function listMemberSessions(userId: string): Promise<MemberPageResult<SessionListItem>> {
  return request<MemberPageResult<SessionListItem>>(memberPath(userId, "/sessions"));
}

export async function fetchMemberSessionDetail(
  userId: string,
  sessionId: string,
): Promise<SessionDetail> {
  return request<SessionDetail>(memberPath(userId, `/sessions/${sessionId}`));
}

export async function fetchMemberSessionMessages(
  userId: string,
  sessionId: string,
): Promise<SessionMessage[]> {
  const result = await request<{ items: SessionMessage[]; next_cursor: string | null }>(
    memberPath(userId, `/sessions/${sessionId}/messages`),
  );
  return result.items;
}

export async function fetchMemberMemoryImpact(
  userId: string,
  sessionId: string,
): Promise<MemoryImpact> {
  return request<MemoryImpact>(memberPath(userId, `/sessions/${sessionId}/memory-impact`));
}

/** 成员私有 Resource 只读列表（`resource.user_private.read.account`）。 */
export async function listMemberResources(userId: string): Promise<MemberPageResult<ResourceSummary>> {
  return request<MemberPageResult<ResourceSummary>>(memberPath(userId, "/resources"));
}

/** 成员私有 Resource 只读预览（05 §12.6：不提供下载/导出，无节点端点）。 */
export async function fetchMemberResource(
  userId: string,
  resourceId: string,
): Promise<ResourceDetail> {
  return request<ResourceDetail>(memberPath(userId, `/resources/${resourceId}`));
}

/** 成员私有 Skill 只读列表（`skill.user_private.read.account`）。 */
export async function listMemberSkills(userId: string): Promise<MemberPageResult<SkillRecord>> {
  return request<MemberPageResult<SkillRecord>>(memberPath(userId, "/skills"));
}

/** 成员私有 Skill 只读详情（10 §61.3：含正文与文件清单，无编辑/删除接口）。 */
export async function fetchMemberSkill(userId: string, skillId: string): Promise<SkillDetail> {
  return request<SkillDetail>(memberPath(userId, `/skills/${skillId}`));
}

/** 将成员私有 Skill 原地发布为共享（10 §58.4：`skill.user_private.publish.account`）。 */
export async function publishMemberSkill(
  userId: string,
  skillId: string,
): Promise<MemberSkillPublishResult> {
  return request<MemberSkillPublishResult>(memberPath(userId, `/skills/${skillId}/publish`), {
    method: "POST",
  });
}

/** 成员 API Key 元数据列表（`credential.read.account`；只含掩码与状态）。 */
export async function listMemberApiKeys(userId: string): Promise<MemberPageResult<MemberApiKeyRecord>> {
  return request<MemberPageResult<MemberApiKeyRecord>>(memberPath(userId, "/api-keys"));
}

/** 撤销成员 API Key（`credential.revoke.account`；幂等：KEY_NOT_FOUND 视为已撤销）。 */
export async function revokeMemberApiKey(
  userId: string,
  credentialId: string,
): Promise<MemberApiKeyRevokeResult> {
  try {
    return await request<MemberApiKeyRevokeResult>(
      memberPath(userId, `/api-keys/${credentialId}`),
      { method: "DELETE" },
    );
  } catch (error) {
    if (error instanceof PlatformError && error.code === "KEY_NOT_FOUND") {
      return { id: credentialId, name: "", revoked: true };
    }
    throw error;
  }
}

/** 发布同步失败是否可重试（10 §58.4：迁移瞬时错误与网络错误可重试；冲突类不可）。 */
export function isPublishRetryable(error: unknown): boolean {
  if (error instanceof PlatformError) {
    switch (error.code) {
      case "SKILL_PUBLISH_MIGRATION_FAILED":
      case "UNAVAILABLE":
      case "DEADLINE_EXCEEDED":
      case "INTERNAL":
        return true;
      default:
        return false;
    }
  }
  return true;
}

/** 成员数据页稳定错误码 → 文案（05 §12.2；未知码回落 fallback）。 */
export function memberDataErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof PlatformError) {
    switch (error.code) {
      case "NOT_FOUND":
        return "用户或对象不存在（可能不属于当前 Account）。";
      case "SEARCH_UNAVAILABLE":
        return "检索服务暂不可用，请稍后重试。";
      case "INVALID_QUERY":
        return "检索词不合法，请检查后重试。";
      case "INVALID_CURSOR":
        return "分页游标失效，请重新加载。";
      case "KEY_NOT_FOUND":
        return "该 API Key 已不存在（可能已被撤销）。";
      case "SKILL_NAME_CONFLICT":
        return "该名称在当前 Account 不可用，发布被拒绝。";
      case "SKILL_PUBLISH_FORBIDDEN":
        return "无权发布该 Skill（可能已发布或不属于该用户）。";
      case "SKILL_PUBLISH_TARGET_CONFLICT":
        return "发布失败：目标共享区出现同名占用，请稍后重试。";
      case "SKILL_PUBLISH_MIGRATION_FAILED":
        return "发布迁移失败，可重试（失败不会产生重复对象）。";
      case "PERMISSION_NOT_GRANTED":
      case "PERMISSION_DENIED":
        return "权限不足，操作被拒绝。";
      case "UNAVAILABLE":
        return "网络异常，请检查连接后重试。";
      default:
        break;
    }
  }
  return fallback;
}
