/**
 * /platform/audit 平台审计事件页（13 §90，05 §12.6，06 §17，14 号计划 §98.9，P4-E4 AC⑧⑩）。
 *
 * - 数据契约：`GET /platform/audit-events`（平台范围）；复用 P4-E3 展示规则
 *   （87.2 字段白名单：时间/Actor/Subject/动作/Scope/结果/Request ID，AC⑩）；
 * - 可按目标 Account 筛选（13 §90，AC⑧）：下拉选择目标 Account，对已加载列表
 *   按 Subject Account 或 Actor Account 匹配（列表接口无筛选参数，05 §12.6
 *   同款客户端过滤模式）；
 * - 管理员跨用户事件同时展示 Actor 与 Subject（06 §17.2，AC⑧）；
 * - 详情/列表均不渲染：密码、Cookie、API Key 明文或完整 hash、Token、业务正文
 *   （metadata）、完整来源 URL、会话/凭据内部 ID、拒绝原因（13 §87.2、04 §10.8）。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { isPlatformError } from "@/lib/platform-client";
import { useMe } from "@/features/auth/useAuth";
import {
  AUDIT_RESULT_FILTERS,
  AUDIT_RESULT_LABELS,
  auditActorLabel,
  auditSubjectLabel,
  fetchPlatformAuditEvents,
  filterAuditEvents,
  type AuditEvent,
  type AuditFilter,
} from "@/features/iam/admin-governance";
import {
  filterAuditEventsByAccount,
  listPlatformAccounts,
  type PlatformAccount,
} from "@/features/iam/platform";

const EMPTY_FILTER: AuditFilter = {
  result: "all",
  action: "",
  actor: "",
  subject: "",
  since: "",
  until: "",
};

export default function PlatformAuditPage() {
  const me = useMe();
  const actorLabel = me?.user.display_name ?? me?.user.email ?? me?.user.id ?? "当前管理员";

  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [targetAccountId, setTargetAccountId] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(
    null,
  );
  const [loadingMore, setLoadingMore] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [filter, setFilter] = useState<AuditFilter>(EMPTY_FILTER);

  const load = useCallback(() => {
    setLoadError(null);
    setEvents(null);
    setNextCursor(null);
    listPlatformAccounts()
      .then((page) => setAccounts(page.items))
      .catch(() => setAccounts([]));
    fetchPlatformAuditEvents()
      .then((page) => {
        setEvents(page.items);
        setNextCursor(page.next_cursor);
      })
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "无法加载审计事件" : "无法加载审计事件",
          requestId: isPlatformError(error) ? error.requestId : null,
        });
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleLoadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setActionError(null);
    try {
      const page = await fetchPlatformAuditEvents(nextCursor);
      setEvents((prev) => [...(prev ?? []), ...page.items]);
      setNextCursor(page.next_cursor);
    } catch (error) {
      setActionError("加载更多失败，请稍后重试");
    } finally {
      setLoadingMore(false);
    }
  }

  const accountFiltered = useMemo(
    () => filterAuditEventsByAccount(events ?? [], targetAccountId),
    [events, targetAccountId],
  );
  const filtered = useMemo(() => filterAuditEvents(accountFiltered, filter), [accountFiltered, filter]);
  const hasActiveFilters =
    filter.result !== "all" || filter.action !== "" || filter.actor !== "" || filter.subject !== "" || filter.since !== "" || filter.until !== "" || targetAccountId !== "";

  function clearFilters() {
    setFilter(EMPTY_FILTER);
    setTargetAccountId("");
  }

  function updateFilter(patch: Partial<AuditFilter>) {
    setFilter((prev) => ({ ...prev, ...patch }));
  }

  return (
    <div className="admin-page" data-testid="platform-audit-page">
      <div className="admin-page-header">
        <div>
          <h2>审计事件（平台）</h2>
          <p className="admin-account-context" data-testid="platform-audit-actor">
            以 <strong>{actorLabel}</strong>（Platform Super Admin）身份查看平台范围审计；
            可按目标 Account 筛选（13 §90，AC⑧）
          </p>
        </div>
      </div>

      <section className="card" data-testid="platform-audit-list">
        <div className="admin-filter-bar" data-testid="platform-audit-filter">
          <label>
            目标 Account
            <select
              value={targetAccountId}
              onChange={(e) => setTargetAccountId(e.target.value)}
              data-testid="audit-filter-account"
            >
              <option value="">全部 Account</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name ?? account.code}（{account.code}）
                </option>
              ))}
            </select>
          </label>
          <label>
            结果
            <select
              value={filter.result}
              onChange={(e) => updateFilter({ result: e.target.value as AuditFilter["result"] })}
              data-testid="audit-filter-result"
            >
              {AUDIT_RESULT_FILTERS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            动作
            <input
              type="search"
              placeholder="如 user.create / password.reset"
              value={filter.action}
              onChange={(e) => updateFilter({ action: e.target.value })}
              data-testid="audit-filter-action"
            />
          </label>
          <label>
            Actor
            <input
              type="search"
              placeholder="系统组件名 / User ID"
              value={filter.actor}
              onChange={(e) => updateFilter({ actor: e.target.value })}
              data-testid="audit-filter-actor"
            />
          </label>
          <label>
            Subject
            <input
              type="search"
              placeholder="User / Account ID"
              value={filter.subject}
              onChange={(e) => updateFilter({ subject: e.target.value })}
              data-testid="audit-filter-subject"
            />
          </label>
          <label>
            起
            <input
              type="date"
              value={filter.since}
              onChange={(e) => updateFilter({ since: e.target.value })}
              data-testid="audit-filter-since"
            />
          </label>
          <label>
            止
            <input
              type="date"
              value={filter.until}
              onChange={(e) => updateFilter({ until: e.target.value })}
              data-testid="audit-filter-until"
            />
          </label>
          {hasActiveFilters ? (
            <button type="button" className="filter-clear" onClick={clearFilters} data-testid="audit-filter-clear">
              清除筛选
            </button>
          ) : null}
        </div>
        <p className="profile-hint">
          筛选作用于已加载列表（列表接口无筛选参数，05 §12.6）；Account 筛选匹配事件中的
          Subject Account 或 Actor Account；详情不展示密码、Cookie、API Key、Token、业务正文
          与内部会话/凭据 ID（13 §87.2）。
        </p>

        {loadError ? (
          <div className="admin-load-error" role="alert" data-testid="platform-audit-load-error">
            <p className="login-error">{loadError.message}</p>
            {loadError.requestId ? (
              <p className="error-page-request-id">
                请求 ID：<code>{loadError.requestId}</code>
              </p>
            ) : null}
            <div className="profile-actions">
              <button type="button" onClick={load} data-testid="platform-audit-retry">
                重试
              </button>
            </div>
          </div>
        ) : null}

        {events == null && !loadError ? (
          <p className="profile-hint" data-testid="platform-audit-loading">
            加载中…
          </p>
        ) : null}

        {events != null && events.length === 0 ? (
          <div className="empty-state" data-testid="platform-audit-empty">
            平台暂无审计事件。
          </div>
        ) : null}

        {events != null && events.length > 0 && filtered.length === 0 ? (
          <div className="empty-state" data-testid="platform-audit-filter-empty">
            <p>当前筛选无结果</p>
            <button type="button" className="filter-clear" onClick={clearFilters}>
              清除筛选
            </button>
          </div>
        ) : null}

        {filtered.length > 0 ? (
          <table className="data-table" data-testid="platform-audit-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>Actor</th>
                <th>Subject</th>
                <th>动作</th>
                <th>Scope</th>
                <th>结果</th>
                <th>Request ID</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((event) => (
                <tr key={event.id} data-testid={`platform-audit-row-${event.id}`}>
                  <td>{new Date(event.occurred_at).toLocaleString()}</td>
                  <td data-testid={`platform-audit-actor-${event.id}`}>{auditActorLabel(event)}</td>
                  <td data-testid={`platform-audit-subject-${event.id}`}>{auditSubjectLabel(event)}</td>
                  <td>
                    <code>{event.action}</code>
                  </td>
                  <td>{event.scope ?? "—"}</td>
                  <td>
                    <span className={`status-badge ${event.result === "success" ? "active" : event.result === "denied" || event.result === "failed" ? "failed" : ""}`}>
                      {AUDIT_RESULT_LABELS[event.result] ?? event.result}
                    </span>
                  </td>
                  <td>
                    {event.request_id ? (
                      <code data-testid={`platform-audit-request-id-${event.id}`}>{event.request_id}</code>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}

        {nextCursor ? (
          <div className="admin-pagination">
            <button
              type="button"
              onClick={() => void handleLoadMore()}
              disabled={loadingMore}
              data-testid="platform-audit-load-more"
            >
              {loadingMore ? "加载中…" : "加载更多"}
            </button>
          </div>
        ) : null}
      </section>

      {actionError ? (
        <p className="login-error" role="alert" data-testid="platform-audit-action-error">
          {actionError}
        </p>
      ) : null}
    </div>
  );
}
