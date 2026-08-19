/**
 * /app/sessions Session 查看管理页（11 §70–§72，P3-E3 AC⑤⑥⑦）。
 *
 * - 双栏只读浏览：左栏 Session 列表 + 删除入口；右栏详情/消息历史/Memory Impact；
 * - 无消息输入框、无 Composer、无「新建会话」/发送/停止生成/模型选择（AC⑤）；
 * - 标题=客户端名 + Session ID 短标识（11 §70.2，AC⑤）；
 * - Memory Impact 区分 pending/running/completed(有/无变更)/failed（AC⑥）；
 * - 软删弹窗展示 Session 标识、消息数、Commit 数、Impact 仍可查看与恢复截止
 *   （11 §72，AC⑦）；恢复走回收站（属主自助恢复）。
 */

import { useEffect, useState } from "react";
import { isPlatformError } from "@/lib/platform-client";
import {
  fetchMemoryImpact,
  fetchSessionDetail,
  fetchSessionMessages,
  listSessions,
  shortSessionId,
  softDeleteSession,
  type MemoryImpact,
  type SessionDetail,
  type SessionListItem,
  type SessionMessage,
} from "@/features/sessions/sessions";
import SessionMessages from "@/components/sessions/SessionMessages";
import MemoryImpactPanel from "@/components/sessions/MemoryImpactPanel";

export default function SessionsPage() {
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [listState, setListState] = useState<"loading" | "ready" | "error">("loading");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [messages, setMessages] = useState<SessionMessage[] | null>(null);
  const [impact, setImpact] = useState<MemoryImpact | null>(null);
  const [detailState, setDetailState] = useState<"idle" | "loading" | "ready" | "error">("idle");

  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setListState("loading");
    listSessions()
      .then((result) => {
        if (cancelled) return;
        setSessions(result.items);
        setListState("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setListState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setMessages(null);
      setImpact(null);
      setDetailState("idle");
      return;
    }
    let cancelled = false;
    setDetailState("loading");
    setDetail(null);
    setMessages(null);
    setImpact(null);
    Promise.all([
      fetchSessionDetail(selectedId),
      fetchSessionMessages(selectedId),
      fetchMemoryImpact(selectedId),
    ])
      .then(([d, msgs, imp]) => {
        if (cancelled) return;
        setDetail(d);
        setMessages(msgs);
        setImpact(imp);
        setDetailState("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        setDetailState("error");
        void err;
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  async function handleDelete() {
    if (!selectedId || deleting) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await softDeleteSession(selectedId);
      setSessions((current) => current.filter((s) => s.id !== selectedId));
      setSelectedId(null);
      setConfirmingDelete(false);
    } catch (err) {
      setDeleteError(
        isPlatformError(err) ? err.message || "删除失败，请稍后重试" : "删除失败，请稍后重试",
      );
    } finally {
      setDeleting(false);
    }
  }

  const selected = sessions.find((s) => s.id === selectedId) ?? null;

  return (
    <div className="sessions-page" data-testid="sessions-page">
      <h2 className="page-title">Sessions</h2>
      <div className="sessions-layout">
        <aside className="card sessions-sidebar" data-testid="sessions-sidebar">
          <h3>Session 列表（{sessions.length}）</h3>
          {listState === "loading" ? <p>加载中…</p> : null}
          {listState === "error" ? <p className="profile-hint">Session 列表加载失败。</p> : null}
          {listState === "ready" && sessions.length === 0 ? (
            <p className="profile-hint" data-testid="sessions-empty">
              还没有 Session。可以在 Codex、其他 Agent、插件或 MCP 客户端中连接 OpenViking 开始使用。
            </p>
          ) : null}
          <ul className="session-list">
            {sessions.map((session) => (
              <li key={session.id}>
                <button
                  type="button"
                  className={`session-list-item ${selectedId === session.id ? "selected" : ""}`}
                  onClick={() => setSelectedId(session.id)}
                  data-testid={`session-item-${session.id}`}
                >
                  <strong>{session.title}</strong>
                  <span className="profile-hint">
                    {session.message_count} 条消息 ·{" "}
                    {session.updated_at
                      ? new Date(session.updated_at).toLocaleString()
                      : "无活动"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <main className="sessions-main" data-testid="sessions-main">
          {!selected ? (
            <div className="empty-state" data-testid="sessions-none-selected">
              选择左侧 Session 查看历史。
              <p className="profile-hint">
                Session 由 Codex、其他 Agent、插件或 MCP 客户端写入；本页不提供消息发送。
              </p>
            </div>
          ) : detailState === "loading" ? (
            <p>加载中…</p>
          ) : detailState === "error" ? (
            <p className="profile-hint">Session 详情加载失败（可能已删除或不可见）。</p>
          ) : detail ? (
            <>
              <header className="sessions-detail-head" data-testid="sessions-detail">
                <h3>{detail.title}</h3>
                <dl className="home-count-grid sessions-meta">
                  <div>
                    <dt>来源客户端</dt>
                    <dd data-testid="session-client-name">{detail.client_name}</dd>
                  </div>
                  <div>
                    <dt>Session ID</dt>
                    <dd data-testid="session-short-id">#{shortSessionId(detail.id)}</dd>
                  </div>
                  <div>
                    <dt>消息数</dt>
                    <dd data-testid="session-message-count">{detail.message_count}</dd>
                  </div>
                  <div>
                    <dt>Commit 数</dt>
                    <dd data-testid="session-commit-count">{detail.commit_count}</dd>
                  </div>
                  <div>
                    <dt>最后同步</dt>
                    <dd>
                      {detail.last_sync_at ? new Date(detail.last_sync_at).toLocaleString() : "—"}
                    </dd>
                  </div>
                </dl>
                <div className="row-actions">
                  <button
                    type="button"
                    className="danger-button"
                    onClick={() => {
                      setDeleteError(null);
                      setConfirmingDelete(true);
                    }}
                    data-testid="session-delete-button"
                  >
                    删除 Session
                  </button>
                </div>
              </header>

              <section className="card sessions-messages-block" aria-label="消息历史">
                <h3>消息历史</h3>
                <SessionMessages messages={messages ?? []} />
              </section>

              {impact ? <MemoryImpactPanel impact={impact} /> : null}
            </>
          ) : null}
        </main>
      </div>

      {confirmingDelete && selected ? (
        <div className="confirm-dialog" role="dialog" aria-label="删除 Session 确认" data-testid="delete-session-dialog">
          <h3>确认删除 Session</h3>
          <ul className="admin-dialog-list">
            <li>
              Session 标识：<strong>{selected.title}</strong>（客户端：{selected.client_name}）
            </li>
            <li>
              消息数量：<strong>{selected.message_count}</strong>
            </li>
            <li>
              Commit 数：<strong>{selected.commit_count}</strong>（Memory Impact 记录在回收期内仍可查看）
            </li>
            <li>
              影响：删除后从列表与检索中隐藏；30 天内可在回收站自助恢复；恢复不回滚已产生的 Memory 变更（11 §72）。
            </li>
            <li>
              恢复截止：删除时间 + 30 天（届时将物理清理）。
            </li>
          </ul>
          {deleteError ? (
            <p className="login-error" role="alert" data-testid="session-delete-error">
              {deleteError}
            </p>
          ) : null}
          <div className="row-actions">
            <button type="button" onClick={() => setConfirmingDelete(false)} disabled={deleting}>
              取消
            </button>
            <button
              type="button"
              className="danger-button"
              onClick={() => void handleDelete()}
              disabled={deleting}
              data-testid="session-delete-confirm"
            >
              {deleting ? "删除中…" : "确认删除"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
