/**
 * 回收站数据层（05 §12.5/§12.6 注，11 §72，P3-E3 AC⑦⑧）。
 *
 * - `/app/recycle-bin` 与 `/admin/recycle-bin`、`/platform/recycle-bin` 共用本层
 *   与共享组件（P4-E3 只复用不重写）；挂载点差异仅在后端 scope；
 * - 恢复权限按对象类型由服务端分别校验（`restore_permission` 逐项下发），
 *   前端仅以该码做体验性门禁（06 §13.4：前端不是安全边界）；
 * - `RESTORE_WINDOW_EXPIRED`（409）：已过恢复截止或已被物理清理，前端正确展示。
 */

import { request } from "@/lib/platform-client";

export type RecycleResourceType =
  | "session"
  | "resource"
  | "skill"
  | "user"
  | "account";

export interface RecycleBinItem {
  id: string;
  resource_type: RecycleResourceType;
  resource_id: string;
  target_name: string;
  deleted_at: string;
  purge_after: string;
  status: string;
  restore_allowed: boolean;
  restore_permission: string | null;
  restored_at: string | null;
}

export interface RecycleBinResult {
  items: RecycleBinItem[];
  next_cursor: string | null;
}

export interface RestoreResult {
  resource_type: string;
  resource_id: string;
  deletion_job_id: string;
  deleted_at: string;
  restore_until: string;
}

export async function fetchRecycleBin(): Promise<RecycleBinResult> {
  return request<RecycleBinResult>("/api/platform/v1/recycle-bin");
}

/** 恢复（05 §12.6 注：按对象类型携带权限码；属主 Session 自助恢复，AC⑦⑧）。 */
export async function restoreRecycleItem(jobId: string): Promise<RestoreResult> {
  return request<RestoreResult>(`/api/platform/v1/recycle-bin/${jobId}/restore`, {
    method: "POST",
  });
}

/** 对象类型 → 产品分组文案（06 §14.6：按对象类型分组展示与恢复）。 */
export const RESOURCE_TYPE_LABELS: Record<RecycleResourceType, string> = {
  session: "对话 Session",
  resource: "Resource",
  skill: "Skill",
  user: "用户",
  account: "Account",
};

export function resourceTypeLabel(type: string): string {
  return RESOURCE_TYPE_LABELS[type as RecycleResourceType] ?? type;
}

/** 仅展示可恢复对象（AC⑧：`restore_allowed` 由服务端实时计算）。 */
export function restorableItems(items: RecycleBinItem[]): RecycleBinItem[] {
  return items.filter((item) => item.restore_allowed === true);
}

export function formatRestoreDeadline(iso: string): string {
  return new Date(iso).toLocaleString();
}
