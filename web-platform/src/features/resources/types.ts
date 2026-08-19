/**
 * Resource 产品 DTO 类型与展示标签（09 §46.4，P3-E4）。
 *
 * - `id` 形态为 `res_{uuid}`（09 §46.4）；URL 路径参数必须是裸 UUID，
 *   跳转前用 `toUrlResourceId` 转换（lib/links.ts isProductId 契约，AC⑧）；
 * - 产品页面/网络请求不出现 Viking URI、原始 Task ID、宿主机路径
 *   或来源 Query（09 §48 #3，AC⑧）。
 */

export type ResourceVisibility = "user_private" | "account_shared";

export type ResourceSourceType = "upload" | "web" | "git";

/** 09 §41.1 产品 Lifecycle 状态（服务端 ref.status 已映射为产品枚举）。 */
export type ResourceLifecycleStatus =
  | "provisioning"
  | "active"
  | "failed"
  | "pending_deletion";

/** 09 §41.1 产品处理阶段（前端不依赖 OpenViking 内部字符串）。 */
export type ProcessingStage =
  | "queued"
  | "fetching"
  | "parsing"
  | "indexing"
  | "finalizing"
  | "succeeded"
  | "failed"
  | "cancelled";

export type OperationStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelling"
  | "cancelled";

export type WatchState = "active" | "paused" | "not_configured" | "error";

export interface ResourceProcessing {
  state: ProcessingStage;
  stage: ProcessingStage;
  latest_operation_id: string | null;
  last_succeeded_at: string | null;
}

export interface ResourceWatchSummary {
  state: WatchState;
  interval_minutes?: number | null;
  last_run_at?: string | null;
  next_run_at?: string | null;
  last_result?: OperationStatus | null;
  last_error?: string | null;
}

/** 09 §46.4 ResourceSummary。 */
export interface ResourceSummary {
  id: string;
  visibility: ResourceVisibility;
  name: string;
  description: string | null;
  source_type: ResourceSourceType;
  source_display: string | null;
  tags: string[];
  lifecycle_status: ResourceLifecycleStatus;
  processing: ResourceProcessing;
  watch: ResourceWatchSummary;
  version: number;
  created_at: string | null;
  updated_at: string | null;
}

/** 09 §42.2/§46.4 ResourceDetail = Summary + overview/内容统计/Watch/当前 Operation。 */
export interface ResourceDetail extends ResourceSummary {
  overview: string | null;
  content: { node_count: number; size_bytes: number };
  current_operation: OperationItem | null;
}

/** 09 §46.1 Capabilities（上传限制必须取自它，AC②：不硬编码）。 */
export interface ResourceCapabilities {
  source_types: ResourceSourceType[];
  upload: {
    max_files_per_batch: number;
    max_file_size_bytes: number;
    ttl_minutes: number;
    accepts_archives: boolean;
  };
  watch: {
    enabled: boolean;
    interval_presets_minutes: number[];
    manual_refresh_min_interval_seconds: number;
  };
  git: {
    ignore_dirs_max: number;
    include_exclude_max: number;
    processing_mode_fixed: string;
  };
}

/** 上传（09 §46.1：Upload ID 绑定 Actor/Scope/过期时间，只能消费一次）。 */
export interface ResourceUploadResult {
  upload_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  expires_at: string;
}

export interface ImportItemError {
  code: string;
  message: string;
  retryable: boolean;
}

/** 批量导入单项目结果（09 §40.5：按文件独立成功/失败，不做全批回滚）。 */
export interface ImportBatchItem {
  resource_id: string | null;
  operation_id: string | null;
  error: ImportItemError | null;
}

export interface ImportBatchResult {
  batch_id: string;
  items: ImportBatchItem[];
}

export interface IngestStartResult {
  resource_id: string;
  operation_id: string;
  status: string;
}

/** 09 §43.3 Activity 项（不返回堆栈/Worker 路径/原始 Task ID）。 */
export interface OperationItem {
  id: string;
  operation_type: string;
  status: OperationStatus;
  stage: ProcessingStage | null;
  initiated_by: "user" | "system";
  created_at: string | null;
  completed_at: string | null;
  cancellable: boolean;
  error: { code: string; summary: string | null; retryable: boolean } | null;
  generation: number;
}

/** 09 §42.3 节点（node_id 为服务端生成的不透明标识）。 */
export interface ResourceNode {
  node_id: string;
  name: string;
  path: string;
  type: "file" | "dir";
  size_bytes: number;
  mime_type: string | null;
}

export interface ResourceNodeDetail extends ResourceNode {
  preview: string | null;
}

export interface NodeSearchHit {
  node_id: string;
  name: string;
  snippet: string;
}

/** 09 §45.1 删除预览（弹窗完整数据）。 */
export interface ResourceDeletionPreview {
  resource_id: string;
  name: string;
  visibility: ResourceVisibility;
  content: { node_count: number; size_bytes: number };
  watch: { configured: boolean; will_be_paused: boolean };
  inflight_cancelled: string[];
  restore_until: string;
  shared_impact_note: string | null;
}

export interface ResourceDeletionResult {
  resource_id: string;
  deletion_job_id: string;
  deleted_at: string;
  restore_until: string;
}

/** 09 §44.2 发布结果：生成新共享 ID，原对象保留。 */
export interface ResourcePublishResult {
  resource_id: string;
  visibility: ResourceVisibility;
  source_resource_id: string;
}

/** 05 §12.5 /recycle-bin 行（恢复按对象类型携带权限码）。 */
export interface RecycleBinRow {
  id: string;
  resource_type: string;
  resource_id: string;
  target_name: string;
  deleted_at: string;
  purge_after: string;
  status: string;
  restore_allowed: boolean;
  restore_permission: string | null;
  restored_at: string | null;
}

export interface RecycleBinPage {
  items: RecycleBinRow[];
  next_cursor: string | null;
}

export interface RestoreResult {
  resource_type: string;
  resource_id: string;
  deletion_job_id: string;
  deleted_at: string;
  restore_until: string;
}
