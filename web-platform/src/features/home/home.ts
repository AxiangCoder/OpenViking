/**
 * 首页数据层（05 §12.5 `GET /api/platform/v1/dashboard`，P3-E3 AC①）。
 *
 * - 受控聚合：内容数量、最近 Session 活动、处理失败摘要；
 * - 不请求/展示 Queue、锁、模型、VectorDB 等底层状态（06 §13.7，AC①）；
 * - 全部计数已由服务端过滤软删除（Session 从正常列表隐藏）。
 */

import { request } from "@/lib/platform-client";

export type Phase2Status = "pending" | "running" | "completed" | "failed";

export interface DashboardSummary {
  sessions: number;
  messages: number;
  resources_private: number;
  skills_private: number;
  resources_account_shared: number;
  skills_account_shared: number;
  sessions_in_recycle: number;
  commits_by_phase2: Record<Phase2Status, number>;
  sessions_with_commit_failed: number;
}

export interface DashboardRecentSession {
  id: string;
  client_name: string;
  sync_status: string;
  commit_count: number;
  message_count: number;
  updated_at: string | null;
}

export interface Dashboard {
  generated_at: string;
  summary: DashboardSummary;
  recent_activity: DashboardRecentSession[];
}

export async function fetchDashboard(): Promise<Dashboard> {
  return request<Dashboard>("/api/platform/v1/dashboard");
}

export interface DashboardContentCounts {
  sessions: number;
  messages: number;
  resources: number;
  skills: number;
  shared_resources: number;
  shared_skills: number;
  sessions_in_recycle: number;
}

export function contentCounts(summary: DashboardSummary): DashboardContentCounts {
  return {
    sessions: summary.sessions,
    messages: summary.messages,
    resources: summary.resources_private,
    skills: summary.skills_private,
    shared_resources: summary.resources_account_shared,
    shared_skills: summary.skills_account_shared,
    sessions_in_recycle: summary.sessions_in_recycle,
  };
}

/** 处理失败摘要（仅业务级失败计数，无底层任务/堆栈信息）。 */
export interface FailureSummary {
  commits_failed: number;
  sessions_with_commit_failed: number;
  hasFailures: boolean;
}

export function failureSummary(summary: DashboardSummary): FailureSummary {
  const commitsFailed = summary.commits_by_phase2.failed ?? 0;
  return {
    commits_failed: commitsFailed,
    sessions_with_commit_failed: summary.sessions_with_commit_failed,
    hasFailures: commitsFailed > 0 || summary.sessions_with_commit_failed > 0,
  };
}
