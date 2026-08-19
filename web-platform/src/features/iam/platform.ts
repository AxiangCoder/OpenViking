/**
 * /platform 平台管理数据层（05 §12.6 平台表、13 §89–§90，14 号计划 §98.9，P4-E4）。
 *
 * - 全部接口固定 `/api/platform/v1/platform/...`，仅 PSA（Platform Super Admin）
 *   可访问（06 §13.2）；选择目标 Account 是管理浏览（指定 Subject），不改变
 *   登录者 Actor 身份（89.1，AC②）；Account 切换不在产品范围（v0.1）；
 * - Account 列表 DTO 目前不含成员数（后端 account_dto），`member_count` 为
 *   防御性可选字段：后端补齐后自动展示，缺省显示「—」；
 * - 创建 Account：表单含首位 Account Admin 邮箱与显示名（+code，后端契约要求），
 *   成功一次性返回首位 Admin 初始密码（05 §12.6）；无创建/重置 PSA 入口（AC④）；
 * - Provisioning 重试：仅 `failed` 状态可重试且幂等（P2-E1，AC⑤）；
 * - 平台代管共享 Resource（09 §38.2/§40.1、05 §12.6 平台表）：列表/上传/导入/
 *   编辑/Refresh/删除；Skill 始终只读（10 §60，AC⑦⑨）；
 * - 成员数据只读（13 §89.3 同 84.2）：检索/Session/Resource/Skill 只读，
 *   无下载/导出/发布/删除端点（AC⑦）。
 */

import { PlatformError, request, requestForm } from "@/lib/platform-client";
import {
  toUrlResourceId,
  type PatchResourceInput,
  type ResourceListFilter,
  type ResourceListPage,
} from "@/features/resources";
import type {
  ImportBatchItem,
  OperationItem,
  ResourceDetail,
  ResourceNode,
  ResourceSummary,
} from "@/features/resources";
import type { SkillDetail, SkillRecord } from "@/features/skills";
import type {
  MemoryImpact,
  SessionDetail,
  SessionListItem,
  SessionMessage,
} from "@/features/sessions/sessions";
import { buildSearchBody, type SearchHit, type SearchParams } from "@/features/search/search";
import type { AuditEvent } from "./admin-governance";
import type {
  AdminPasswordResetResult,
  AdminUserDeletionPreview,
  AdminUserDeletionResult,
  AdminUserRecord,
} from "./admin-users";
import type { MemberApiKeyRecord } from "./member-data";

// ── Account（13 §89.2，05 §12.6 平台表）──

export type PlatformAccountStatus =
  | "provisioning"
  | "active"
  | "suspended"
  | "failed"
  | "pending_deletion";

export interface PlatformAccount {
  id: string;
  code: string;
  name: string;
  status: PlatformAccountStatus;
  ov_account_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  /** 后端 account_dto 暂未下发成员数；补齐后自动展示（缺省「—」，见 P4-E4 遗留记录）。 */
  member_count?: number | null;
}

export interface PlatformAccountListPage {
  items: PlatformAccount[];
  next_cursor: string | null;
}

/** 创建成功一次性返回首位 Account Admin 的初始密码（05 §12.6，仅该次响应）。 */
export interface PlatformAccountCreateResult {
  account: PlatformAccount;
  first_admin: {
    id: string;
    username: string;
    email: string;
    role: string;
    ov_user_id: string | null;
    initial_password: string;
  };
}

export interface PlatformProvisioningRetryResult {
  account_id: string;
  status: string;
  retried_events: number;
}

export const PLATFORM_ACCOUNT_STATUSES: PlatformAccountStatus[] = [
  "provisioning",
  "active",
  "suspended",
  "failed",
  "pending_deletion",
];

export const PLATFORM_ACCOUNT_STATUS_LABELS: Record<PlatformAccountStatus, string> = {
  provisioning: "开通中",
  active: "正常",
  suspended: "已暂停",
  failed: "开通失败",
  pending_deletion: "删除中",
};

export function platformAccountStatusLabel(status: string): string {
  return PLATFORM_ACCOUNT_STATUS_LABELS[status as PlatformAccountStatus] ?? status;
}

/** `GET /platform/accounts`（`account.read.platform`）。 */
export async function listPlatformAccounts(cursor?: string): Promise<PlatformAccountListPage> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return request<PlatformAccountListPage>(`/api/platform/v1/platform/accounts${query}`);
}

/** `POST /platform/accounts`（`account.manage.platform`；后端契约要求首位 Admin 的 code）。 */
export function createPlatformAccount(input: {
  account_name: string;
  account_code: string;
  admin_email: string;
  admin_username: string;
  admin_display_name: string | null;
}): Promise<PlatformAccountCreateResult> {
  return request<PlatformAccountCreateResult>("/api/platform/v1/platform/accounts", {
    method: "POST",
    body: {
      account_code: input.account_code,
      account_name: input.account_name,
      admin_email: input.admin_email,
      admin_username: input.admin_username,
      ...(input.admin_display_name ? { admin_display_name: input.admin_display_name } : {}),
    },
  });
}

/** `GET /platform/accounts/{id}/deletion-preview`（`account.delete`）。 */
export function fetchPlatformAccountDeletionPreview(
  accountId: string,
): Promise<AdminUserDeletionPreview> {
  return request<AdminUserDeletionPreview>(
    `/api/platform/v1/platform/accounts/${accountId}/deletion-preview`,
  );
}

/** `DELETE /platform/accounts/{id}`：进入 30 天回收期（`account.delete`）。 */
export function deletePlatformAccount(accountId: string): Promise<AdminUserDeletionResult> {
  return request<AdminUserDeletionResult>(`/api/platform/v1/platform/accounts/${accountId}`, {
    method: "DELETE",
  });
}

/**
 * `POST /platform/accounts/{id}/provisioning/retry`（`account.manage.platform`；
 * 仅 `failed` 状态可重试，幂等，P2-E1 AC⑤）。
 */
export function retryPlatformProvisioning(accountId: string): Promise<PlatformProvisioningRetryResult> {
  return request<PlatformProvisioningRetryResult>(
    `/api/platform/v1/platform/accounts/${accountId}/provisioning/retry`,
    { method: "POST" },
  );
}

// ── Account 用户（13 §89.3，05 §12.6 平台表）──

/** `GET /platform/accounts/{id}/users`（`user.read.platform`；DTO 与 /admin/users 同形）。 */
export async function listPlatformAccountUsers(
  accountId: string,
  cursor?: string,
): Promise<{ items: AdminUserRecord[]; next_cursor: string | null }> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return request<{ items: AdminUserRecord[]; next_cursor: string | null }>(
    `/api/platform/v1/platform/accounts/${accountId}/users${query}`,
  );
}

/** `PUT /platform/accounts/{id}/users/{uid}/role`：仅 `user → account_admin`。 */
export function promotePlatformAccountUser(
  accountId: string,
  userId: string,
): Promise<AdminUserRecord> {
  return request<AdminUserRecord>(
    `/api/platform/v1/platform/accounts/${accountId}/users/${userId}/role`,
    { method: "PUT" },
  );
}

/** 平台级分级重置（禁目标 PSA；成功撤销目标全部登录 Session，05 §12.6）。 */
export function resetPlatformAccountUserPassword(
  accountId: string,
  userId: string,
): Promise<AdminPasswordResetResult> {
  return request<AdminPasswordResetResult>(
    `/api/platform/v1/platform/accounts/${accountId}/users/${userId}/password/reset`,
    { method: "POST" },
  );
}

// ── 成员 API Key 元数据 / 撤销（05 §12.6 平台表）──

export function listPlatformMemberApiKeys(
  accountId: string,
  userId: string,
): Promise<{ items: MemberApiKeyRecord[]; next_cursor: string | null }> {
  return request<{ items: MemberApiKeyRecord[]; next_cursor: string | null }>(
    `/api/platform/v1/platform/accounts/${accountId}/users/${userId}/api-keys`,
  );
}

export interface PlatformMemberApiKeyRevokeResult {
  id: string;
  name: string;
  key_last_four: string;
  revoked: boolean;
}

/** 撤销（`credential.revoke.platform`；幂等：KEY_NOT_FOUND 视为已撤销成功）。 */
export async function revokePlatformMemberApiKey(
  accountId: string,
  userId: string,
  credentialId: string,
): Promise<PlatformMemberApiKeyRevokeResult> {
  try {
    const revoked = await request<{ id: string; name: string; key_last_four: string }>(
      `/api/platform/v1/platform/accounts/${accountId}/users/${userId}/api-keys/${credentialId}`,
      { method: "DELETE" },
    );
    return { ...revoked, revoked: true };
  } catch (error) {
    if (error instanceof PlatformError && error.code === "KEY_NOT_FOUND") {
      return { id: credentialId, name: "", key_last_four: "", revoked: true };
    }
    throw error;
  }
}

// ── 平台级成员数据只读（13 §89.3 同 84.2，AC⑦）──

function platformMemberPath(accountId: string, userId: string, tail: string): string {
  return `/api/platform/v1/platform/accounts/${accountId}/users/${userId}${tail}`;
}

/** 平台级只读成员检索（11 §74.1：`memory.read.platform` 等目标对象读取权限）。 */
export async function platformSearchFind(
  accountId: string,
  userId: string,
  params: SearchParams,
): Promise<SearchHit[]> {
  const result = await request<{ items: SearchHit[] }>(
    platformMemberPath(accountId, userId, "/search/find"),
    { method: "POST", body: buildSearchBody(params) },
  );
  return result.items;
}

/** 成员 Session 只读列表（`session.read.platform`）。 */
export async function listPlatformMemberSessions(
  accountId: string,
  userId: string,
): Promise<{ items: SessionListItem[]; next_cursor: string | null }> {
  return request<{ items: SessionListItem[]; next_cursor: string | null }>(
    platformMemberPath(accountId, userId, "/sessions"),
  );
}

export async function fetchPlatformMemberSessionDetail(
  accountId: string,
  userId: string,
  sessionId: string,
): Promise<SessionDetail> {
  return request<SessionDetail>(platformMemberPath(accountId, userId, `/sessions/${sessionId}`));
}

export async function fetchPlatformMemberSessionMessages(
  accountId: string,
  userId: string,
  sessionId: string,
): Promise<SessionMessage[]> {
  const result = await request<{ items: SessionMessage[]; next_cursor: string | null }>(
    platformMemberPath(accountId, userId, `/sessions/${sessionId}/messages`),
  );
  return result.items;
}

export async function fetchPlatformMemberMemoryImpact(
  accountId: string,
  userId: string,
  sessionId: string,
): Promise<MemoryImpact> {
  return request<MemoryImpact>(platformMemberPath(accountId, userId, `/sessions/${sessionId}/memory-impact`));
}

/** 成员私有 Resource 只读列表（`resource.user_private.read.platform`）。 */
export async function listPlatformMemberResources(
  accountId: string,
  userId: string,
): Promise<{ items: ResourceSummary[]; next_cursor: string | null }> {
  return request<{ items: ResourceSummary[]; next_cursor: string | null }>(
    platformMemberPath(accountId, userId, "/resources"),
  );
}

/** 成员私有 Resource 只读预览（05 §12.6：不提供下载/导出，无节点端点）。 */
export async function fetchPlatformMemberResource(
  accountId: string,
  userId: string,
  resourceId: string,
): Promise<ResourceDetail> {
  return request<ResourceDetail>(
    platformMemberPath(accountId, userId, `/resources/${toUrlResourceId(resourceId)}`),
  );
}

/** 成员私有 Skill 只读列表（`skill.user_private.read.platform`；只读）。 */
export async function listPlatformMemberSkills(
  accountId: string,
  userId: string,
): Promise<{ items: SkillRecord[]; next_cursor: string | null }> {
  return request<{ items: SkillRecord[]; next_cursor: string | null }>(
    platformMemberPath(accountId, userId, "/skills"),
  );
}

/** 成员私有 Skill 只读详情（10 §61.3：无编辑/删除/发布端点，平台代发布不在 v0.1）。 */
export async function fetchPlatformMemberSkill(
  accountId: string,
  userId: string,
  skillId: string,
): Promise<SkillDetail> {
  return request<SkillDetail>(
    platformMemberPath(accountId, userId, `/skills/${skillId}`),
  );
}

// ── 平台代管共享 Resource（09 §38.2/§40.1、05 §12.6 平台表，AC⑨）──

function platformResourcePath(accountId: string, tail: string): string {
  return `/api/platform/v1/platform/accounts/${accountId}${tail}`;
}

function listQuery(filter: ResourceListFilter): string {
  const params: string[] = [];
  if (filter.sourceType) params.push(`source_type=${encodeURIComponent(filter.sourceType)}`);
  if (filter.status) params.push(`status=${encodeURIComponent(filter.status)}`);
  params.push(`limit=${filter.limit ?? 50}`);
  if (filter.cursor) params.push(`cursor=${encodeURIComponent(filter.cursor)}`);
  return params.length ? `?${params.join("&")}` : "";
}

/** 平台代管共享 Resource 列表（`resource.account_shared.read.platform`）。 */
export function listPlatformAccountResources(
  accountId: string,
  filter: ResourceListFilter = {},
): Promise<ResourceListPage> {
  return request<ResourceListPage>(platformResourcePath(accountId, `/resources${listQuery(filter)}`));
}

/** 上传（multipart）：目标 Account 共享区（`resource.account_shared.write.platform`）。 */
export function platformUploadResourceFile(accountId: string, file: File): Promise<{ upload_id: string }> {
  const form = new FormData();
  form.append("file", file, file.name);
  return requestForm<{ upload_id: string }>(
    platformResourcePath(accountId, "/resource-uploads"),
    form,
    { method: "POST" },
  );
}

export interface PlatformImportItemInput {
  uploadId?: string;
  sourceUrl?: string;
  isGit?: boolean;
  name?: string;
  description?: string;
  tags?: string[];
  instruction?: string;
}

/** 导入（09 §40.2 同款来源类型；批量按文件独立成败）。 */
export async function platformImportResources(
  accountId: string,
  items: PlatformImportItemInput[],
  idempotencyKey?: string,
): Promise<{ batch_id: string; items: ImportBatchItem[] }> {
  return request<{ batch_id: string; items: ImportBatchItem[] }>(
    platformResourcePath(accountId, "/resources/imports"),
    {
      method: "POST",
      body: {
        items: items.map((item) => ({
          ...(item.uploadId ? { upload_id: item.uploadId } : {}),
          ...(item.sourceUrl ? { source_url: item.sourceUrl } : {}),
          ...(item.isGit ? { is_git: true } : {}),
          ...(item.name ? { name: item.name } : {}),
          ...(item.description ? { description: item.description } : {}),
          ...(item.tags && item.tags.length > 0 ? { tags: item.tags } : {}),
          ...(item.instruction ? { instruction: item.instruction } : {}),
        })),
      },
      idempotencyKey,
    },
  );
}

export function fetchPlatformAccountResource(
  accountId: string,
  resourceId: string,
): Promise<ResourceDetail> {
  return request<ResourceDetail>(
    platformResourcePath(accountId, `/resources/${toUrlResourceId(resourceId)}`),
  );
}

/** 编辑元数据（`resource.account_shared.write.platform`；乐观锁 version）。 */
export function patchPlatformAccountResource(
  accountId: string,
  resourceId: string,
  input: PatchResourceInput,
): Promise<ResourceSummary> {
  return request<ResourceSummary>(
    platformResourcePath(accountId, `/resources/${toUrlResourceId(resourceId)}`),
    {
      method: "PATCH",
      body: {
        ...(input.displayName !== undefined ? { display_name: input.displayName } : {}),
        ...(input.description !== undefined ? { description: input.description } : {}),
        ...(input.tags !== undefined ? { tags: input.tags } : {}),
        version: input.version,
      },
    },
  );
}

/** 手动 Refresh（`resource.account_shared.write.platform`）。 */
export function refreshPlatformAccountResource(
  accountId: string,
  resourceId: string,
): Promise<{ resource_id: string; operation_id: string }> {
  return request<{ resource_id: string; operation_id: string }>(
    platformResourcePath(accountId, `/resources/${toUrlResourceId(resourceId)}/refresh`),
    { method: "POST" },
  );
}

/** 删除（`resource.account_shared.delete.platform`；If-Match 乐观锁）。 */
export function deletePlatformAccountResource(
  accountId: string,
  resourceId: string,
  version: number,
): Promise<AdminUserDeletionResult> {
  return request<AdminUserDeletionResult>(
    platformResourcePath(accountId, `/resources/${toUrlResourceId(resourceId)}`),
    { method: "DELETE", ifMatch: String(version) },
  );
}

/** 平台代管共享 Resource 节点列表（只读；无单节点读取/下载端点）。 */
export function listPlatformAccountResourceNodes(
  accountId: string,
  resourceId: string,
): Promise<{ items: ResourceNode[]; next_cursor: string | null }> {
  return request<{ items: ResourceNode[]; next_cursor: string | null }>(
    platformResourcePath(accountId, `/resources/${toUrlResourceId(resourceId)}/nodes`),
  );
}

/** 平台代管共享 Resource 活动（只读；无取消端点）。 */
export function listPlatformAccountResourceOperations(
  accountId: string,
  resourceId: string,
): Promise<{ items: OperationItem[]; next_cursor: string | null }> {
  return request<{ items: OperationItem[]; next_cursor: string | null }>(
    platformResourcePath(accountId, `/resources/${toUrlResourceId(resourceId)}/operations`),
  );
}

// ── 平台共享 Skill 只读（10 §60，AC⑦⑨）──

export function listPlatformAccountSkills(
  accountId: string,
): Promise<{ items: SkillRecord[]; next_cursor: string | null }> {
  return request<{ items: SkillRecord[]; next_cursor: string | null }>(
    platformResourcePath(accountId, "/skills"),
  );
}

export function fetchPlatformAccountSkill(
  accountId: string,
  skillId: string,
): Promise<SkillDetail> {
  return request<SkillDetail>(platformResourcePath(accountId, `/skills/${skillId}`));
}

// ── 审计按目标 Account 筛选（13 §90，AC⑧）──

/** 平台审计可按目标 Account 筛选：匹配 Subject Account 或 Actor Account。 */
export function filterAuditEventsByAccount(
  items: AuditEvent[],
  accountId: string,
): AuditEvent[] {
  if (!accountId) return items;
  return items.filter(
    (event) => event.subject_account_id === accountId || event.actor_account_id === accountId,
  );
}

// ── 稳定错误码 → 文案（05 §12.2；未知码回落 fallback）──

export function platformErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof PlatformError) {
    switch (error.code) {
      case "ACCOUNT_CODE_ALREADY_EXISTS":
        return "该 Account code 已被使用（平台全局唯一）。";
      case "EMAIL_ALREADY_EXISTS":
        return "该邮箱已被其他用户使用（邮箱全局唯一）。";
      case "USERNAME_ALREADY_EXISTS":
        return "该 code 在目标 Account 内已被使用。";
      case "PROVISIONING_NOT_RETRYABLE":
        return "该 Account 当前状态不可重试开通（仅开通失败状态可重试）。";
      case "PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN":
        return "仅可重置严格低等级用户的密码（禁止重置 Platform Super Admin）。";
      case "ROLE_PROMOTION_FORBIDDEN":
        return "仅支持将 user 提升为 account_admin，目标角色不符被拒绝。";
      case "LAST_ACCOUNT_ADMIN_REQUIRED":
        return "该用户是最后一个 Account Admin，操作被拒绝。";
      case "PROVISIONING_PENDING":
        return "该 Account/用户仍在开通中，操作被拒绝。";
      case "PROVISIONING_FAILED":
        return "该 Account/用户开通失败，操作被拒绝。";
      case "DELETION_PENDING":
        return "该对象已进入删除流程，操作被拒绝。";
      case "NOT_FOUND":
        return "对象不存在（可能已删除或不属于目标 Account）。";
      case "INVALID_CURSOR":
        return "分页游标失效，请重新加载。";
      case "SEARCH_UNAVAILABLE":
        return "检索服务暂不可用，请稍后重试。";
      case "INVALID_QUERY":
        return "检索词不合法，请检查后重试。";
      case "KEY_NOT_FOUND":
        return "该 API Key 已不存在（可能已被撤销）。";
      case "RESOURCE_VERSION_CONFLICT":
        return "版本冲突，请重新加载后重试。";
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

/** 仅对严格低级别目标展示重置按钮；PSA 永不显示（03 §8.3、89.3，AC⑥）。 */
export function isPlatformResetTarget(role: string | null | undefined): boolean {
  return role != null && role !== "platform_super_admin";
}
