/**
 * /app 首页（06 §13.7，05 §12.5 dashboard，P3-E3 AC①⑨）。
 *
 * - 内容数量：Session/消息/私有与共享 Resource/Skill/回收站计数；
 * - 最近 Session：最近活动的脱敏摘要（客户端名/消息数/最后活动时间）；
 * - 处理失败摘要：Commit 整理失败与 sync 失败计数（仅业务级，无底层状态）；
 * - 不请求/展示 Queue、锁、模型、VectorDB 等底层状态（AC①）；
 * - 全局导航无 Account 切换入口（AC⑨，07 §21 条目 14）。
 */

import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMe } from "@/features/auth/useAuth";
import {
  contentCounts,
  failureSummary,
  fetchDashboard,
  type Dashboard,
} from "@/features/home/home";

const SYNC_STATUS_LABELS: Record<string, string> = {
  active: "正常",
  commit_pending: "待归档",
  committing: "归档中",
  commit_failed: "归档失败",
  retrying: "重试中",
  deletion_pending: "删除中",
};

function syncStatusLabel(status: string): string {
  return SYNC_STATUS_LABELS[status] ?? status;
}

export default function HomePage() {
  const me = useMe();
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    let cancelled = false;
    setLoadState("loading");
    fetchDashboard()
      .then((data) => {
        if (cancelled) return;
        setDashboard(data);
        setLoadState("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setLoadState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loadState === "loading") return <p>加载中…</p>;
  if (loadState === "error" || !dashboard) {
    return <p className="profile-hint">无法加载首页数据，请稍后重试。</p>;
  }

  const counts = contentCounts(dashboard.summary);
  const failures = failureSummary(dashboard.summary);
  const displayName =
    me?.user.display_name ?? me?.user.email ?? (me?.user.id ? `${me.user.id.slice(0, 8)}…` : "");

  return (
    <div className="home-page" data-testid="home-page">
      <h2 className="page-title">首页</h2>
      {displayName ? <p className="profile-hint">你好，{displayName}</p> : null}

      <section className="card" aria-label="内容数量" data-testid="home-counts">
        <h3>内容数量</h3>
        <dl className="home-count-grid">
          <div>
            <dt>对话 Session</dt>
            <dd data-testid="count-sessions">{counts.sessions}</dd>
          </div>
          <div>
            <dt>消息</dt>
            <dd data-testid="count-messages">{counts.messages}</dd>
          </div>
          <div>
            <dt>我的 Resource</dt>
            <dd data-testid="count-resources">{counts.resources}</dd>
          </div>
          <div>
            <dt>我的 Skill</dt>
            <dd data-testid="count-skills">{counts.skills}</dd>
          </div>
          <div>
            <dt>Account 共享 Resource</dt>
            <dd data-testid="count-shared-resources">{counts.shared_resources}</dd>
          </div>
          <div>
            <dt>Account 共享 Skill</dt>
            <dd data-testid="count-shared-skills">{counts.shared_skills}</dd>
          </div>
        </dl>
      </section>

      {failures.hasFailures ? (
        <section className="card" aria-label="处理失败摘要" data-testid="home-failures">
          <h3>处理失败摘要</h3>
          <ul className="admin-dialog-list">
            <li data-testid="failures-commits">
              {failures.commits_failed} 个 Commit 的 Memory 整理失败
            </li>
            <li data-testid="failures-sessions">
              {failures.sessions_with_commit_failed} 个 Session 同步失败
            </li>
          </ul>
        </section>
      ) : (
        <section className="card" aria-label="处理失败摘要" data-testid="home-failures">
          <h3>处理失败摘要</h3>
          <p className="profile-hint">当前没有失败的处理任务。</p>
        </section>
      )}

      <section className="card" aria-label="最近 Session" data-testid="home-recent-sessions">
        <h3>最近 Session</h3>
        {dashboard.recent_activity.length === 0 ? (
          <p className="profile-hint">
            还没有 Session。可以在 Codex、其他 Agent、插件或 MCP 客户端中连接 OpenViking 开始使用。
          </p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Session</th>
                <th>消息数</th>
                <th>Commit 数</th>
                <th>同步状态</th>
                <th>最后活动</th>
              </tr>
            </thead>
            <tbody>
              {dashboard.recent_activity.map((session) => (
                <tr key={session.id} data-testid="recent-session">
                  <td>
                    <Link to="/app/sessions" className="recent-session-link">
                      {session.client_name} #{session.id.slice(0, 8)}
                    </Link>
                  </td>
                  <td>{session.message_count}</td>
                  <td>{session.commit_count}</td>
                  <td>
                    <span className="status-badge">{syncStatusLabel(session.sync_status)}</span>
                  </td>
                  <td>{session.updated_at ? new Date(session.updated_at).toLocaleString() : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {counts.sessions_in_recycle > 0 ? (
        <p className="profile-hint">
          <Link to="/app/recycle-bin">回收站中有 {counts.sessions_in_recycle} 个可恢复对象</Link>
        </p>
      ) : null}
    </div>
  );
}
