/**
 * Session 查看管理数据层（11 §70/§71/§72/§74.2，05 §12.5，P3-E3 AC⑤⑥⑦）。
 *
 * - 列表/详情/消息/Memory Impact 只读（`session.read.self`）；
 * - 软删除（`session.delete.self`）→ 30 天回收期，恢复走回收站
 *   （features/recycle-bin 统一 restore，AC⑦ 属主自助恢复）；
 * - 产品页不调用任何写接口（Create/Append/Commit 属集成客户端，11 §70.6）；
 * - 标题由服务端生成 = 客户端名 + Session ID 短标识（11 §70.2，AC⑤）。
 */

import { request } from "@/lib/platform-client";

export type SyncStatus =
  | "active"
  | "commit_pending"
  | "committing"
  | "commit_failed"
  | "retrying"
  | "deletion_pending";

export type Phase2Status = "pending" | "running" | "completed" | "failed";

export interface SessionListItem {
  id: string;
  client_name: string;
  title: string;
  sync_status: SyncStatus;
  commit_count: number;
  message_count: number;
  last_sync_at: string | null;
  updated_at: string | null;
  created_at: string | null;
}

export interface SessionDetail extends SessionListItem {
  pending_tokens: number;
}

export interface SessionMessage {
  role: "user" | "assistant" | "system";
  content: string;
  sequence: number;
  turn_id: string | null;
  created_at: string | null;
}

/** 脱敏 Memory Diff 条目（11 §71.3：不含 Archive/Memory URI，AC⑥）。 */
export interface MemoryDiffEntry {
  memory_type: string;
  action: "add" | "update" | "delete";
  before?: string | null;
  after?: string | null;
}

export interface CommitImpact {
  commit_id: string;
  commit_number: number;
  phase2_status: Phase2Status;
  phase2_error: string | null;
  message_count_at_commit: number;
  created_at: string | null;
  completed_at: string | null;
  has_operations: boolean;
  diffs: MemoryDiffEntry[];
}

export interface MemoryImpact {
  session_id: string;
  commit_count: number;
  totals: { added: number; updated: number; deleted: number };
  items: CommitImpact[];
}

export interface SoftDeleteResult {
  resource_type: string;
  resource_id: string;
  deletion_job_id: string;
  deleted_at: string;
  restore_until: string;
}

export interface SessionListResult {
  items: SessionListItem[];
  next_cursor: string | null;
}

export async function listSessions(): Promise<SessionListResult> {
  return request<SessionListResult>("/api/platform/v1/sessions");
}

export async function fetchSessionDetail(sessionId: string): Promise<SessionDetail> {
  return request<SessionDetail>(`/api/platform/v1/sessions/${sessionId}`);
}

export async function fetchSessionMessages(sessionId: string): Promise<SessionMessage[]> {
  const result = await request<{ items: SessionMessage[]; next_cursor: string | null }>(
    `/api/platform/v1/sessions/${sessionId}/messages`,
  );
  return result.items;
}

export async function fetchMemoryImpact(sessionId: string): Promise<MemoryImpact> {
  return request<MemoryImpact>(`/api/platform/v1/sessions/${sessionId}/memory-impact`);
}

/** 软删除（11 §72：立即隐藏，30 天后物理清理；重复删除幂等返回当前状态）。 */
export async function softDeleteSession(sessionId: string): Promise<SoftDeleteResult> {
  return request<SoftDeleteResult>(`/api/platform/v1/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

export function shortSessionId(sessionId: string): string {
  return sessionId.slice(0, 8);
}
