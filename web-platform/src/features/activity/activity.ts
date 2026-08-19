/**
 * Activity 数据层（05 §12.5 `/activity`，09 §43.3，P3-E3 AC⑩）。
 *
 * - 聚合当前 User 私有对象任务 + 有权查看的 Account 共享对象任务；
 * - DTO 由服务端脱敏：无原始 Task ID/内部堆栈/Worker 路径（06 §13.7，AC⑩）；
 * - 取消（`task.cancel.self` / `task.cancel.account_shared`）：仅可取消状态，
 *   成功后状态进入 `cancelling`（09 §43.3：系统已接受并最终停止，非立即终止）。
 */

import { request } from "@/lib/platform-client";

export type OperationStatus =
  | "pending"
  | "running"
  | "cancelling"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface OperationError {
  code: string | null;
  summary: string | null;
  retryable: boolean;
}

export interface ActivityItem {
  id: string;
  operation_type: string;
  status: OperationStatus;
  stage: string | null;
  initiated_by: "user" | "system";
  created_at: string | null;
  completed_at: string | null;
  cancellable: boolean;
  error: OperationError | null;
  generation: number;
}

export interface ActivityListResult {
  items: ActivityItem[];
  next_cursor: string | null;
}

export interface CancelResult {
  operation_id: string;
  status: "cancelling";
}

export async function fetchActivity(): Promise<ActivityListResult> {
  return request<ActivityListResult>("/api/platform/v1/activity");
}

export async function cancelActivity(operationId: string): Promise<CancelResult> {
  return request<CancelResult>(`/api/platform/v1/activity/${operationId}/cancel`, {
    method: "POST",
  });
}

// ── /admin 挂载点（05 §12.6：`task.read.account_shared`，仅当前 Account 共享对象任务）──

interface AdminActivityRow {
  id: string;
  operation_type: string;
  status: OperationStatus;
  stage: string | null;
  target_type: string | null;
  target_id: string | null;
  target_visibility: string | null;
  generation: number;
  cancellable: boolean;
  initiated_by: "user" | "system";
  error_code: string | null;
  error_summary: string | null;
  retryable: boolean;
  created_at: string | null;
  completed_at: string | null;
}

/** 服务端 admin 聚合 DTO 为扁平 error_code/error_summary，规整为共享组件期望的嵌套 error。 */
function toActivityItem(row: AdminActivityRow): ActivityItem {
  return {
    id: row.id,
    operation_type: row.operation_type,
    status: row.status,
    stage: row.stage,
    initiated_by: row.initiated_by,
    created_at: row.created_at,
    completed_at: row.completed_at,
    cancellable: row.cancellable,
    error:
      row.error_code != null
        ? { code: row.error_code, summary: row.error_summary, retryable: row.retryable }
        : null,
    generation: row.generation,
  };
}

/** 仅当前 Account 共享对象任务（P4-E3 AC③；共享组件只按摘要渲染，无 Task ID/堆栈/Worker 路径）。 */
export async function fetchAdminActivity(): Promise<ActivityListResult> {
  const page = await request<{ items: AdminActivityRow[]; next_cursor: string | null }>(
    "/api/platform/v1/admin/activity",
  );
  return { items: page.items.map(toActivityItem), next_cursor: page.next_cursor };
}

/** 取消 Account 共享对象任务（`task.cancel.account_shared` + 目标对象写权限由后端校验）。 */
export async function cancelAdminActivity(operationId: string): Promise<CancelResult> {
  return request<CancelResult>(`/api/platform/v1/admin/activity/${operationId}/cancel`, {
    method: "POST",
  });
}

/** 任务类型 → 产品文案（09 §43.3：类型稳定，新增类型走兜底）。 */
const OPERATION_TYPE_LABELS: Record<string, string> = {
  resource_import: "导入 Resource",
  resource_refresh: "刷新 Resource",
  resource_replace: "替换 Resource",
  resource_watch: "自动同步（Watch）",
  session_commit: "Session 归档整理",
};

export function operationTypeLabel(operationType: string): string {
  return OPERATION_TYPE_LABELS[operationType] ?? "处理任务";
}

const OPERATION_STATUS_LABELS: Record<string, string> = {
  pending: "等待中",
  running: "执行中",
  cancelling: "取消中",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消",
};

export function operationStatusLabel(status: string): string {
  return OPERATION_STATUS_LABELS[status] ?? status;
}

/** 取消弹窗影响文案（06 §13.7：展示目标/任务类型/当前状态/影响；按任务类型描述目标）。 */
export function cancelImpactText(operationType: string): string {
  switch (operationType) {
    case "resource_import":
      return "取消后该导入将停止，目标 Resource 保持当前状态（首次导入不会产生可用的内容）。";
    case "resource_refresh":
      return "取消后该次刷新将停止，Resource 继续保留最近一次成功的内容版本。";
    case "resource_replace":
      return "取消后该次替换将停止，Resource 继续保留旧的成功版本，不会应用本次上传内容。";
    case "resource_watch":
      return "取消后本次自动同步将停止，已配置的同步计划不会被删除。";
    case "session_commit":
      return "取消后本次归档整理将停止，消息历史保持不变；可以稍后重试。";
    default:
      return "取消后该处理任务将停止，目标对象保持当前状态。";
  }
}
