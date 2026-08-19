/**
 * Resource 产品数据层（09 §46，05 §12.5，14 号计划 §98.4）。
 *
 * - Scope 由路由入口固定：`/me/*`=当前 User 私有、`/account/*`=当前
 *   Account 共享（09 §40.1：入口决定归属，请求不提交 visibility/URI）；
 * - 上传限制（数量/大小）取自 `resources/capabilities`（AC②），前端
 *   只做浏览器预检，服务端仍独立强制（09 §40.5）；
 * - 写请求携带 Idempotency-Key（09 §45.4）；元数据/删除用请求体 version
 *   乐观锁（09 §42.4，RESOURCE_VERSION_CONFLICT 前端重新加载）；
 * - 批量导入按文件独立成败（09 §40.5）；Refresh/Watch 期间旧版本可读
 *   （09 §41.2）；上传文件不能 Watch（09 §43.2）；
 * - DTO `id` 为 `res_{uuid}`，URL 参数必须是裸 UUID（AC⑧）。
 */

import { API_PREFIX, request } from "@/lib/platform-client";
import type {
  ImportBatchResult,
  IngestStartResult,
  NodeSearchHit,
  OperationItem,
  RecycleBinPage,
  ResourceCapabilities,
  ResourceDeletionPreview,
  ResourceDeletionResult,
  ResourceDetail,
  ResourceNode,
  ResourceNodeDetail,
  ResourcePublishResult,
  ResourceSummary,
  ResourceUploadResult,
  ResourceWatchSummary,
  RestoreResult,
} from "./types";

export type ResourceScopeKind = "private" | "shared";

function scopePrefix(scope: ResourceScopeKind): string {
  return scope === "private" ? "/me" : "/account";
}

/** `res_{uuid}` DTO ID → URL 路径参数（裸 UUID，lib/links.ts 契约）。 */
export function toUrlResourceId(id: string): string {
  const raw = id.startsWith("res_") ? id.slice(4) : id;
  return raw;
}

export interface ResourceListPage {
  items: ResourceSummary[];
  next_cursor: string | null;
}

export interface ResourceListFilter {
  sourceType?: ResourceSourceTypeFilter;
  status?: string;
  limit?: number;
  cursor?: string;
}

export type ResourceSourceTypeFilter = "upload" | "web" | "git";

function listQuery(filter: ResourceListFilter): string {
  const params: string[] = [];
  if (filter.sourceType) params.push(`source_type=${encodeURIComponent(filter.sourceType)}`);
  if (filter.status) params.push(`status=${encodeURIComponent(filter.status)}`);
  params.push(`limit=${filter.limit ?? 50}`);
  if (filter.cursor) params.push(`cursor=${encodeURIComponent(filter.cursor)}`);
  return params.length ? `?${params.join("&")}` : "";
}

export function listResources(
  scope: ResourceScopeKind,
  filter: ResourceListFilter = {},
): Promise<ResourceListPage> {
  return request<ResourceListPage>(
    `${API_PREFIX}${scopePrefix(scope)}/resources${listQuery(filter)}`,
  );
}

export function fetchCapabilities(): Promise<ResourceCapabilities> {
  return request<ResourceCapabilities>(`${API_PREFIX}/resources/capabilities`);
}

/** 上传（multipart）：上传文件 → 绑定当前 Actor/Scope 的 Upload ID。 */
export function uploadResourceFile(
  scope: ResourceScopeKind,
  file: File,
): Promise<ResourceUploadResult> {
  const form = new FormData();
  form.append("file", file, file.name);
  return request<ResourceUploadResult>(
    `${API_PREFIX}${scopePrefix(scope)}/resource-uploads`,
    { method: "POST", body: form },
  );
}

export interface ImportItemInput {
  uploadId?: string;
  sourceUrl?: string;
  isGit?: boolean;
  name?: string;
  description?: string;
  tags?: string[];
  instruction?: string;
}

export function importResources(
  scope: ResourceScopeKind,
  items: ImportItemInput[],
  idempotencyKey?: string,
): Promise<ImportBatchResult> {
  return request<ImportBatchResult>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/imports`,
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

export function fetchResourceDetail(
  scope: ResourceScopeKind,
  resourceId: string,
): Promise<ResourceDetail> {
  return request<ResourceDetail>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}`,
  );
}

export interface PatchResourceInput {
  displayName?: string;
  description?: string;
  tags?: string[];
  /** 乐观锁版本（09 §42.4；冲突返回 RESOURCE_VERSION_CONFLICT）。 */
  version: number;
}

export function patchResource(
  scope: ResourceScopeKind,
  resourceId: string,
  input: PatchResourceInput,
): Promise<ResourceSummary> {
  return request<ResourceSummary>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}`,
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

/** 上传来源替换（09 §43.1：旧成功版本处理期间保持可读）。 */
export function replaceResource(
  scope: ResourceScopeKind,
  resourceId: string,
  uploadId: string,
): Promise<IngestStartResult> {
  return request<IngestStartResult>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/replace`,
    { method: "POST", body: { upload_id: uploadId } },
  );
}

/** 稳定远程来源手动 Refresh（09 §43.1：5 分钟限频由 Capabilities 返回）。 */
export function refreshResource(
  scope: ResourceScopeKind,
  resourceId: string,
): Promise<IngestStartResult> {
  return request<IngestStartResult>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/refresh`,
    { method: "POST" },
  );
}

/** 重试首次失败导入（09 §46.2：上传/一次性 URL 必须重新提交来源）。 */
export function retryResource(
  scope: ResourceScopeKind,
  resourceId: string,
  input: { uploadId?: string; sourceUrl?: string },
): Promise<IngestStartResult> {
  return request<IngestStartResult>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/retry`,
    {
      method: "POST",
      body: {
        ...(input.uploadId ? { upload_id: input.uploadId } : {}),
        ...(input.sourceUrl ? { source_url: input.sourceUrl } : {}),
      },
    },
  );
}

/** 发布自己的私有 Resource 为共享副本（09 §44.1：仅 Account Admin）。 */
export function publishResource(
  resourceId: string,
  idempotencyKey?: string,
): Promise<ResourcePublishResult> {
  return request<ResourcePublishResult>(
    `${API_PREFIX}/me/resources/${toUrlResourceId(resourceId)}/publish`,
    { method: "POST", idempotencyKey },
  );
}

// ── Watch（09 §43.2：只存 Resource ID 与调度参数；上传文件不可 Watch）──

export function fetchWatchConfig(
  scope: ResourceScopeKind,
  resourceId: string,
): Promise<ResourceWatchSummary> {
  return request<ResourceWatchSummary>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/watch`,
  );
}

export function configureWatch(
  scope: ResourceScopeKind,
  resourceId: string,
  intervalMinutes: number,
  processingInstruction?: string,
): Promise<ResourceWatchSummary> {
  return request<ResourceWatchSummary>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/watch`,
    {
      method: "PUT",
      body: {
        interval_minutes: intervalMinutes,
        ...(processingInstruction
          ? { processing_instruction: processingInstruction }
          : {}),
      },
    },
  );
}

export function pauseWatch(
  scope: ResourceScopeKind,
  resourceId: string,
): Promise<ResourceWatchSummary> {
  return request<ResourceWatchSummary>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/watch/pause`,
    { method: "POST" },
  );
}

export function resumeWatch(
  scope: ResourceScopeKind,
  resourceId: string,
): Promise<ResourceWatchSummary> {
  return request<ResourceWatchSummary>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/watch/resume`,
    { method: "POST" },
  );
}

export function triggerWatch(
  scope: ResourceScopeKind,
  resourceId: string,
): Promise<{ resource_id: string; operation_id: string }> {
  return request<{ resource_id: string; operation_id: string }>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/watch/trigger`,
    { method: "POST" },
  );
}

export function deleteWatch(
  scope: ResourceScopeKind,
  resourceId: string,
): Promise<void> {
  return request<void>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/watch`,
    { method: "DELETE" },
  );
}

// ── Activity（09 §43.3）──

export function listResourceOperations(
  scope: ResourceScopeKind,
  resourceId: string,
): Promise<{ items: OperationItem[]; next_cursor: string | null }> {
  return request<{ items: OperationItem[]; next_cursor: string | null }>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/operations`,
  );
}

export function cancelResourceOperation(
  scope: ResourceScopeKind,
  resourceId: string,
  operationId: string,
): Promise<{ operation_id: string; status: string }> {
  return request<{ operation_id: string; status: string }>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/operations/${operationId}/cancel`,
    { method: "POST" },
  );
}

// ── Nodes / 预览 / 下载 / 资源内搜索（09 §42.3，AC③）──

export function listResourceNodes(
  scope: ResourceScopeKind,
  resourceId: string,
  nodeId?: string,
): Promise<{ items: ResourceNode[]; next_cursor: string | null }> {
  const query = nodeId ? `?node_id=${encodeURIComponent(nodeId)}` : "";
  return request<{ items: ResourceNode[]; next_cursor: string | null }>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/nodes${query}`,
  );
}

export function readResourceNode(
  scope: ResourceScopeKind,
  resourceId: string,
  nodeId: string,
): Promise<ResourceNodeDetail> {
  return request<ResourceNodeDetail>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/nodes/${nodeId}`,
  );
}

/** 单节点下载 URL：服务端以安全文件名/Content-Type/Content-Disposition 返回（AC③）。 */
export function resourceNodeDownloadUrl(
  scope: ResourceScopeKind,
  resourceId: string,
  nodeId: string,
): string {
  return `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/nodes/${nodeId}/download`;
}

export function searchResourceNodes(
  scope: ResourceScopeKind,
  resourceId: string,
  query: string,
): Promise<{ resource_id: string; query: string; items: NodeSearchHit[] }> {
  return request<{ resource_id: string; query: string; items: NodeSearchHit[] }>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/search`,
    { method: "POST", body: { query, limit: 10 } },
  );
}

// ── 删除 / 恢复（09 §45，AC⑥）──

export function fetchDeletionPreview(
  scope: ResourceScopeKind,
  resourceId: string,
): Promise<ResourceDeletionPreview> {
  return request<ResourceDeletionPreview>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}/deletion-preview`,
  );
}

export function deleteResource(
  scope: ResourceScopeKind,
  resourceId: string,
  version: number,
): Promise<ResourceDeletionResult> {
  return request<ResourceDeletionResult>(
    `${API_PREFIX}${scopePrefix(scope)}/resources/${toUrlResourceId(resourceId)}`,
    { method: "DELETE", ifMatch: String(version) },
  );
}

/** 当前 User 回收站（05 §12.5）：恢复自己的私有 Resource（09 §45.3）。 */
export function listRecycleBin(): Promise<RecycleBinPage> {
  return request<RecycleBinPage>(`${API_PREFIX}/recycle-bin`);
}

export function restoreResource(jobId: string): Promise<RestoreResult> {
  return request<RestoreResult>(`${API_PREFIX}/recycle-bin/${jobId}/restore`, {
    method: "POST",
  });
}
