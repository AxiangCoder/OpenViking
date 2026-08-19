/**
 * Memory Impact 面板（11 §71.3，P3-E3 AC⑥）。
 *
 * - 仅 `commit_count > 0` 时显示入口；
 * - Phase 2 状态区分：
 *   - `pending/running`：显示「正在整理记忆」；
 *   - `completed` 且无操作：显示「本次未产生长期记忆变更」；
 *   - `completed` 且有操作：显示统计与差异（Add/Before-After/Delete）；
 *   - `failed`：显示可重试状态并提示进入 Activity，不伪造空结果；
 * - 只读视图：无回滚/恢复/编辑/删除（11 §71.3）；差异 DTO 由服务端脱敏
 *   （无 Archive/Memory URI）。
 */

import { useState } from "react";
import type { CommitImpact, MemoryImpact } from "@/features/sessions/sessions";

const DIFF_ACTION_LABELS: Record<string, string> = {
  add: "新增",
  update: "更新",
  delete: "删除",
};

export default function MemoryImpactPanel({ impact }: { impact: MemoryImpact }) {
  const [selectedCommit, setSelectedCommit] = useState<CommitImpact | null>(
    impact.items[0] ?? null,
  );

  if (impact.commit_count === 0) {
    return (
      <div className="card memory-impact" data-testid="memory-impact">
        <h3>Memory Impact</h3>
        <p className="profile-hint">该 Session 尚未进行归档整理（Commit）。</p>
      </div>
    );
  }

  const pendingCount = impact.items.filter((i) => i.phase2_status === "pending" || i.phase2_status === "running").length;

  return (
    <div className="card memory-impact" data-testid="memory-impact">
      <h3>Memory Impact</h3>

      <dl className="memory-impact-totals" data-testid="memory-impact-totals">
        <div>
          <dt>Commit 数</dt>
          <dd>{impact.commit_count}</dd>
        </div>
        <div>
          <dt>新增</dt>
          <dd data-testid="memory-impact-added">{impact.totals.added}</dd>
        </div>
        <div>
          <dt>更新</dt>
          <dd data-testid="memory-impact-updated">{impact.totals.updated}</dd>
        </div>
        <div>
          <dt>删除</dt>
          <dd data-testid="memory-impact-deleted">{impact.totals.deleted}</dd>
        </div>
      </dl>

      {pendingCount > 0 ? (
        <p className="profile-hint" data-testid="memory-impact-pending">
          正在整理记忆（{pendingCount} 个 Commit 仍在处理中）。
        </p>
      ) : null}

      {impact.items.map((commit) => (
        <section key={commit.commit_id} className="memory-impact-commit">
          <div className="memory-impact-commit-head">
            <button
              type="button"
              className={selectedCommit?.commit_id === commit.commit_id ? "impact-selected" : ""}
              onClick={() => setSelectedCommit(commit)}
              data-testid={`impact-commit-${commit.commit_number}`}
            >
              Commit #{commit.commit_number}
            </button>
            <span className="status-badge" data-testid={`impact-status-${commit.commit_number}`}>
              {commit.phase2_status === "pending" || commit.phase2_status === "running"
                ? "正在整理记忆"
                : commit.phase2_status === "failed"
                  ? "整理失败"
                  : commit.has_operations
                    ? "已产生变更"
                    : "无变更"}
            </span>
          </div>

          {selectedCommit?.commit_id === commit.commit_id ? (
            <div className="memory-impact-detail">
              {commit.phase2_status === "failed" ? (
                <p className="login-error" role="alert" data-testid="impact-failed">
                  本次整理失败，可在 Activity 中查看重试状态。
                </p>
              ) : null}
              {commit.phase2_status === "completed" && !commit.has_operations ? (
                <p className="profile-hint" data-testid="impact-no-changes">
                  本次未产生长期记忆变更。
                </p>
              ) : null}
              {commit.phase2_status === "completed" && commit.has_operations ? (
                <ul className="admin-dialog-list" data-testid="impact-diffs">
                  {(commit.diffs ?? []).map((diff, index) => (
                    <li key={`${commit.commit_id}-${index}`} data-testid="impact-diff">
                      <strong>{DIFF_ACTION_LABELS[diff.action] ?? diff.action}</strong>
                      <span className="profile-hint">
                        {diff.memory_type}：
                        {diff.action === "add" || diff.action === "update"
                          ? diff.after ?? "—"
                          : diff.before ?? "—"}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}
              <p className="profile-hint">
                提取时间：{commit.created_at ? new Date(commit.created_at).toLocaleString() : "—"}
                {commit.completed_at
                  ? ` · 完成时间：${new Date(commit.completed_at).toLocaleString()}`
                  : ""}
              </p>
            </div>
          ) : null}
        </section>
      ))}

      <p className="profile-hint" data-testid="memory-impact-readonly-note">
        只读视图：Memory 由归档整理自动生成与更新，不提供回滚、恢复、编辑或删除。
      </p>
    </div>
  );
}
