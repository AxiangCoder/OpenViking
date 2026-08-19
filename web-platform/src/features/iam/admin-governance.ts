/**
 * /admin 治理页数据层（05 §12.6，13 §87，14 号计划 §98.8，P4-E3）。
 *
 * - roles：`GET /admin/roles`（`role.read`）只读三内置角色与权限矩阵（AC①）；
 * - audit：`GET /admin/audit-events`（`audit.read`）仅当前 Account；列表字段白名单，
 *   不展示会话/凭据内部 ID、业务元数据与拒绝原因等敏感字段（13 §87.2，AC②）；
 * - monitoring：`GET /admin/monitoring`（`monitoring.read`）仅业务健康摘要，
 *   不包含 Queue/锁/模型/VectorDB/文件系统与原始请求日志（08 §28.4，AC④）。
 */

import { request } from "@/lib/platform-client";

// ── roles（03 §9.2/9.3，AC①）──

export interface RoleView {
  id: string;
  code: string;
  name: string;
  description: string | null;
  ov_base_role: string | null;
  rank: number;
  is_system: boolean;
  status: string;
  permissions: string[];
}

/** 只读三内置角色与权限集合（服务端返回数组，无 items 包裹）。 */
export async function fetchAdminRoles(): Promise<RoleView[]> {
  return request<RoleView[]>("/api/platform/v1/admin/roles");
}

/** 权限矩阵行分组：取 `<domain>`（第一个 `.` 前），用于矩阵分区展示。 */
export function permissionDomain(code: string): string {
  const dot = code.indexOf(".");
  return dot === -1 ? code : code.slice(0, dot);
}

// ── audit（13 §87.2，AC②）──

export type AuditActorType = "user" | "system" | "api_key" | "internal";
export type AuditResult = "success" | "denied" | "failed" | string;

export interface AuditEvent {
  id: string;
  occurred_at: string;
  request_id: string | null;
  account_id: string | null;
  actor_type: AuditActorType;
  actor_user_id: string | null;
  actor_account_id: string | null;
  actor_system_component: string | null;
  actor_session_id: string | null;
  authentication_method: string | null;
  actor_credential_id: string | null;
  subject_account_id: string | null;
  subject_user_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  target_visibility: string | null;
  scope: string | null;
  result: AuditResult;
  reason: string | null;
  metadata: Record<string, unknown> | null;
}

export interface AuditEventPage {
  items: AuditEvent[];
  next_cursor: string | null;
}

/** 审计事件列表（05 §12.6：仅当前 Account；筛选在前端对已加载列表进行）。 */
export async function fetchAdminAuditEvents(cursor?: string): Promise<AuditEventPage> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return request<AuditEventPage>(`/api/platform/v1/admin/audit-events${query}`);
}

/** 列表字段白名单校验（13 §87.2 可展示字段；其余字段页面一律不渲染）。 */
export const AUDIT_DISPLAY_FIELDS = [
  "occurred_at",
  "actor_type",
  "actor_user_id",
  "actor_system_component",
  "subject_user_id",
  "subject_account_id",
  "action",
  "scope",
  "result",
  "request_id",
] as const;

/** 结果筛选选项（13 §87.2：成功/拒绝/失败）。 */
export const AUDIT_RESULT_FILTERS: { value: "all" | "success" | "denied" | "failed"; label: string }[] = [
  { value: "all", label: "全部结果" },
  { value: "success", label: "成功" },
  { value: "denied", label: "拒绝" },
  { value: "failed", label: "失败" },
];

export const AUDIT_RESULT_LABELS: Record<string, string> = {
  success: "成功",
  denied: "拒绝",
  failed: "失败",
};

/** Actor 展示：系统组件名或 User（内部 ID 取短前缀，不展示会话/凭据 ID）。 */
export function auditActorLabel(event: AuditEvent): string {
  if (event.actor_type !== "user") {
    return event.actor_system_component ?? event.actor_type ?? "系统";
  }
  return event.actor_user_id ? `User（${shortId(event.actor_user_id)}）` : "User";
}

/** Subject 展示：User/Account 内部 ID 短前缀；无则占位（管理员跨用户事件同时含两者，06 §17.2）。 */
export function auditSubjectLabel(event: AuditEvent): string {
  const parts: string[] = [];
  if (event.subject_user_id) parts.push(`User（${shortId(event.subject_user_id)}）`);
  if (event.subject_account_id) parts.push(`Account（${shortId(event.subject_account_id)}）`);
  return parts.length > 0 ? parts.join(" / ") : "—";
}

function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

/** 前端筛选（列表接口无筛选参数，对已加载列表过滤，05 §12.6 同款模式）。 */
export interface AuditFilter {
  result: "all" | "success" | "denied" | "failed";
  action: string;
  actor: string;
  subject: string;
  since: string;
  until: string;
}

export function filterAuditEvents(items: AuditEvent[], filter: AuditFilter): AuditEvent[] {
  const action = filter.action.trim().toLowerCase();
  const actor = filter.actor.trim().toLowerCase();
  const subject = filter.subject.trim().toLowerCase();
  return items.filter((event) => {
    if (filter.result !== "all" && event.result !== filter.result) return false;
    if (action && !event.action.toLowerCase().includes(action)) return false;
    if (actor) {
      const actorText =
        event.actor_type !== "user"
          ? (event.actor_system_component ?? "").toLowerCase()
          : (event.actor_user_id ?? "").toLowerCase();
      if (!actorText.includes(actor)) return false;
    }
    if (subject) {
      const subjectText = `${event.subject_user_id ?? ""} ${event.subject_account_id ?? ""}`.toLowerCase();
      if (!subjectText.includes(subject)) return false;
    }
    if (filter.since && event.occurred_at < filter.since) return false;
    if (filter.until && event.occurred_at > `${filter.until}T23:59:59.999Z`) return false;
    return true;
  });
}

// ── monitoring（13 §87.4，AC④）──

export interface MonitoringSummary {
  accounts: number;
  active_accounts: number;
  users: number;
  active_users: number;
  content_refs_active_by_type: Record<string, number>;
  deletion_jobs_in_recycle: number;
  pending_uploads: number;
  open_operations: number;
}

export interface MonitoringResult {
  generated_at: string;
  scope: "account" | "platform";
  summary: MonitoringSummary;
}

/** 业务健康摘要（05 §12.6：仅当前 Account；不含底层组件状态）。 */
export async function fetchAdminMonitoring(): Promise<MonitoringResult> {
  return request<MonitoringResult>("/api/platform/v1/admin/monitoring");
}

// ── /platform 挂载点（13 §90，P4-E4；复用本层 DTO 与展示规则）──

/** `/platform/audit-events`：平台范围审计（13 §90，AC⑧；列表字段白名单同 87.2）。 */
export async function fetchPlatformAuditEvents(cursor?: string): Promise<AuditEventPage> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return request<AuditEventPage>(`/api/platform/v1/platform/audit-events${query}`);
}

/** `/platform/monitoring`：平台聚合业务摘要（05 §12.6；不含底层组件状态）。 */
export async function fetchPlatformMonitoring(): Promise<MonitoringResult> {
  return request<MonitoringResult>("/api/platform/v1/platform/monitoring");
}
