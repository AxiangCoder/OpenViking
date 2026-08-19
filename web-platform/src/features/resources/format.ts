/**
 * Resource 展示格式化与标签（09 §39.2/§41.1/§42，P3-E4）。
 * 页面不展示底层 URI/内部字符串，全部经产品枚举映射（09 §41.1）。
 */

import type {
  OperationItem,
  OperationStatus,
  ProcessingStage,
  ResourceLifecycleStatus,
  ResourceSourceType,
  WatchState,
} from "./types";

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export const SOURCE_TYPE_LABELS: Record<ResourceSourceType, string> = {
  upload: "文件",
  web: "网页",
  git: "Git 仓库",
};

export const LIFECYCLE_LABELS: Record<ResourceLifecycleStatus, string> = {
  provisioning: "首次处理中",
  active: "可用",
  failed: "首次处理失败",
  pending_deletion: "待删除",
};

export const WATCH_STATE_LABELS: Record<WatchState, string> = {
  active: "运行中",
  paused: "已暂停",
  not_configured: "未启用",
  error: "最近同步失败",
};

export const STAGE_LABELS: Record<ProcessingStage, string> = {
  queued: "等待处理",
  fetching: "正在获取来源",
  parsing: "正在解析内容",
  indexing: "正在生成摘要与索引",
  finalizing: "正在完成处理",
  succeeded: "处理完成",
  failed: "处理失败",
  cancelled: "已取消",
};

export const OPERATION_STATUS_LABELS: Record<OperationStatus, string> = {
  pending: "等待中",
  running: "处理中",
  succeeded: "成功",
  failed: "失败",
  cancelling: "取消中",
  cancelled: "已取消",
};

export const OPERATION_TYPE_LABELS: Record<string, string> = {
  resource_import: "导入",
  resource_replace: "替换来源",
  resource_refresh: "刷新",
  resource_watch: "自动同步",
  resource_retry: "重试导入",
};

export function operationTypeLabel(type: string): string {
  return OPERATION_TYPE_LABELS[type] ?? "处理任务";
}

/** 09 §39.2 watch 列：上传文件不显示 Watch（source_type=upload 返回 null）。 */
export function watchColumnText(
  sourceType: ResourceSourceType,
  watch: { state: WatchState } | null | undefined,
): string | null {
  if (sourceType === "upload") return null;
  if (!watch) return WATCH_STATE_LABELS.not_configured;
  return WATCH_STATE_LABELS[watch.state] ?? watch.state;
}

/** 09 §39.2 latest_operation 列文案（Refresh/Watch 不改变已有可用版本）。 */
export function latestOperationText(summary: {
  lifecycle_status: ResourceLifecycleStatus;
  processing: { state: ProcessingStage; last_succeeded_at: string | null };
}): string {
  if (summary.lifecycle_status === "provisioning") {
    return `处理中（${STAGE_LABELS[summary.processing.state] ?? "等待处理"}）`;
  }
  if (summary.lifecycle_status === "failed") {
    return "首次处理失败";
  }
  if (summary.processing.state === "failed") {
    return "最近同步失败";
  }
  if (
    summary.processing.state === "fetching" ||
    summary.processing.state === "parsing" ||
    summary.processing.state === "indexing" ||
    summary.processing.state === "finalizing"
  ) {
    return "正在同步";
  }
  return summary.processing.last_succeeded_at
    ? `最近处理 ${formatDateTime(summary.processing.last_succeeded_at)}`
    : "—";
}

export function initiatedByLabel(initiatedBy: OperationItem["initiated_by"]): string {
  return initiatedBy === "system" ? "系统" : "页面";
}
