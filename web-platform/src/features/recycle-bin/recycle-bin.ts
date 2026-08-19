/**
 * 回收站数据层（05 §12.5/§12.6 注，11 §72，P3-E3 AC⑦⑧）。
 *
 * - `/app/recycle-bin` 与 `/admin/recycle-bin`、`/platform/recycle-bin` 共用本层
 *   与共享组件（P4-E3 只复用不重写）；挂载点差异仅在后端 scope；
 * - 恢复权限按对象类型由服务端分别校验（`restore_permission` 逐项下发），
 *   前端仅以该码做体验性门禁（06 §13.4：前端不是安全边界）；
 * - `RESTORE_WINDOW_EXPIRED`（409）：已过恢复截止或已被物理清理，前端正确展示。
 */

import { PlatformError, isPlatformError, request } from "@/lib/platform-client";

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

// ── /admin 挂载点（05 §12.6 注：按对象类型分别校验；与 /app 共用组件）──

/** `/admin/recycle-bin`：当前 Account 内有权恢复的对象（AC⑤）。 */
export async function fetchAdminRecycleBin(): Promise<RecycleBinResult> {
  return request<RecycleBinResult>("/api/platform/v1/admin/recycle-bin");
}

/** 恢复动作写审计由后端完成；`SKILL_NAME_CONFLICT` 转为产品文案（10 §55.3，AC⑥）。 */
export async function restoreAdminRecycleItem(jobId: string): Promise<RestoreResult> {
  try {
    return await request<RestoreResult>(`/api/platform/v1/admin/recycle-bin/${jobId}/restore`, {
      method: "POST",
    });
  } catch (error) {
    // 共享组件以 err.message 展示；将稳定错误码转为可读文案（组件只挂载不改写）
    if (isPlatformError(error) && error.code === "SKILL_NAME_CONFLICT") {
      throw new PlatformError({
        code: "SKILL_NAME_CONFLICT",
        status: error.status,
        message: "该名称已被其他 Skill 占用：恢复被拒绝，原 Skill 保持删除状态（名称不可改名或覆盖）。",
        requestId: error.requestId,
      });
    }
    throw error;
  }
}

// ── /platform 挂载点（05 §12.6 注：平台范围，Skill 始终只读，P4-E4 AC⑧⑨）──

/** `/platform/recycle-bin`：平台范围回收站（`restore_allowed` 由服务端按类型实时计算）。 */
export async function fetchPlatformRecycleBin(): Promise<RecycleBinResult> {
  return request<RecycleBinResult>("/api/platform/v1/platform/recycle-bin");
}

/** 平台范围恢复（按对象类型校验；Skill 服务端不下发恢复权限 → 组件只读，10 §60）。 */
export async function restorePlatformRecycleItem(jobId: string): Promise<RestoreResult> {
  try {
    return await request<RestoreResult>(`/api/platform/v1/platform/recycle-bin/${jobId}/restore`, {
      method: "POST",
    });
  } catch (error) {
    // 共享组件以 err.message 展示；将稳定错误码转为可读文案（组件只挂载不改写）
    if (isPlatformError(error) && error.code === "SKILL_NAME_CONFLICT") {
      throw new PlatformError({
        code: "SKILL_NAME_CONFLICT",
        status: error.status,
        message: "该名称已被其他 Skill 占用：恢复被拒绝，原 Skill 保持删除状态（名称不可改名或覆盖）。",
        requestId: error.requestId,
      });
    }
    throw error;
  }
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
