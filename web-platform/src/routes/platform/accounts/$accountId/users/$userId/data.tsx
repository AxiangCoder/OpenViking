/**
 * /platform/accounts/{accountId}/users/{userId}/data 平台级 Subject 数据视图
 * （13 §89.3 同 84.2，14 号计划 §98.9，P4-E4 AC②⑦）。
 *
 * - 固定 Actor/Subject 横幅（79.3：操作者与数据所属用户，不允许无痕替换身份；
 *   AC② 选择目标 Account 是管理浏览，不改变登录者身份）；
 * - 四个只读区块按权限展示：成员检索（memory.read.platform）、Session 历史
 *   （session.read.platform）、私有 Resource（resource.user_private.read.platform）、
 *   私有 Skill（skill.user_private.read.platform）；
 * - 全页无修改/导出/下载/Watch/发布/删除按钮（AC⑦）；Skill 始终只读（10 §60）；
 * - 检索结果只跳平台成员只读详情路由（不跳 /app，AC⑦）；
 * - 加载失败保留页面框架，展示 Request ID 与重试（13 §84.3）。
 */

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { useMe } from "@/features/auth/useAuth";
import { canPerform } from "@/lib/permissions";
import { isProductId } from "@/lib/links";
import PlatformSubjectDataBanner from "@/components/subject/PlatformSubjectDataBanner";
import SessionMessages from "@/components/sessions/SessionMessages";
import MemoryImpactPanel from "@/components/sessions/MemoryImpactPanel";
import { SkillList } from "@/components/skills/SkillList";
import { formatIsoDateTime, type AdminUserRecord } from "@/features/iam/admin-users";
import {
  fetchPlatformMemberMemoryImpact,
  fetchPlatformMemberSessionDetail,
  fetchPlatformMemberSessionMessages,
  listPlatformAccountUsers,
  listPlatformAccounts,
  listPlatformMemberResources,
  listPlatformMemberSessions,
  listPlatformMemberSkills,
  platformErrorMessage,
  platformSearchFind,
} from "@/features/iam/platform";
import {
  parseTagsInput,
  type SearchContextType,
  type SearchHit,
} from "@/features/search/search";
import { LIFECYCLE_LABELS, SOURCE_TYPE_LABELS } from "@/features/resources";
import type { ResourceSummary } from "@/features/resources";
import type { SessionDetail, SessionListItem, SessionMessage, MemoryImpact } from "@/features/sessions/sessions";
import type { SkillRecord } from "@/features/skills";

type DataTab = "search" | "sessions" | "resources" | "skills";

export default function PlatformAccountUserDataPage() {
  const params = useParams({ strict: false }) as Record<string, string>;
  const accountId = params["accountId"] ?? "";
  const userId = params["userId"] ?? "";
  const me = useMe();
  const actorLabel = me?.user.display_name ?? me?.user.email ?? me?.user.id ?? "当前管理员";

  const [accountLabel, setAccountLabel] = useState("—");
  const [subject, setSubject] = useState<AdminUserRecord | null | undefined>(undefined);
  const [subjectError, setSubjectError] = useState<string | null>(null);
  const [subjectRequestId, setSubjectRequestId] = useState<string | null>(null);

  const loadSubject = useCallback(() => {
    setSubjectError(null);
    setSubjectRequestId(null);
    setSubject(undefined);
    listPlatformAccountUsers(accountId)
      .then((page) => {
        const found = page.items.find((u) => u.id === userId) ?? null;
        setSubject(found);
      })
      .catch((error) => {
        setSubject(null);
        setSubjectError(
          isPlatformError(error) ? error.message || "无法解析成员身份" : "无法解析成员身份",
        );
        setSubjectRequestId(isPlatformError(error) ? error.requestId : null);
      });
    listPlatformAccounts()
      .then((page) => {
        const account = page.items.find((a) => a.id === accountId);
        setAccountLabel(account?.name ?? account?.code ?? accountId ?? "—");
      })
      .catch(() => setAccountLabel(accountId ?? "—"));
  }, [accountId, userId]);

  useEffect(() => {
    loadSubject();
  }, [loadSubject]);

  const subjectLabel = useMemo(() => {
    if (subject) {
      return `${subject.display_name ?? subject.username}（${subject.email}）`;
    }
    return `用户 ${userId}`;
  }, [subject, userId]);

  const canSearch = canPerform(me, "memory.read.platform");
  const canSessions = canPerform(me, "session.read.platform");
  const canResources = canPerform(me, "resource.user_private.read.platform");
  const canSkills = canPerform(me, "skill.user_private.read.platform");

  const [tab, setTab] = useState<DataTab>(canSearch ? "search" : canSessions ? "sessions" : canResources ? "resources" : canSkills ? "skills" : "search");

  const tabs: { key: DataTab; label: string; visible: boolean }[] = [
    { key: "search", label: "检索", visible: canSearch },
    { key: "sessions", label: "Sessions", visible: canSessions },
    { key: "resources", label: "Resources", visible: canResources },
    { key: "skills", label: "Skills", visible: canSkills },
  ];

  return (
    <div className="admin-page" data-testid="platform-user-data-page">
      <PlatformSubjectDataBanner actorLabel={actorLabel} accountLabel={accountLabel} subjectLabel={subjectLabel} />
      <div className="profile-actions">
        <Link
          to={`/platform/accounts/${accountId}/users`}
          className="placeholder-back"
          data-testid="platform-subject-back-users"
        >
          ← 返回 Account 用户
        </Link>
      </div>

      {subjectError ? (
        <div className="admin-load-error" role="alert" data-testid="platform-subject-identity-error">
          <p className="login-error">{subjectError}</p>
          {subjectRequestId ? (
            <p className="error-page-request-id">
              请求 ID：<code>{subjectRequestId}</code>
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="button" onClick={loadSubject} data-testid="platform-subject-identity-retry">
              重试
            </button>
          </div>
        </div>
      ) : null}
      <div className="skill-tabs" role="tablist" aria-label="成员数据区块">
        {tabs
          .filter((t) => t.visible)
          .map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              onClick={() => setTab(t.key)}
              data-testid={`platform-subject-tab-${t.key}`}
            >
              {t.label}
            </button>
          ))}
      </div>

      {tab === "search" && canSearch ? <PlatformMemberSearchPanel accountId={accountId} userId={userId} /> : null}
      {tab === "sessions" && canSessions ? <PlatformMemberSessionsPanel accountId={accountId} userId={userId} /> : null}
      {tab === "resources" && canResources ? <PlatformMemberResourcesPanel accountId={accountId} userId={userId} /> : null}
      {tab === "skills" && canSkills ? <PlatformMemberSkillsPanel accountId={accountId} userId={userId} /> : null}
    </div>
  );
}

/* ── 检索（11 §74.1：只读成员范围检索，白名单字段）── */

function PlatformMemberSearchPanel({ accountId, userId }: { accountId: string; userId: string }) {
  const [query, setQuery] = useState("");
  const [contextType, setContextType] = useState<SearchContextType | "">("");
  const [tags, setTags] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [memoryHit, setMemoryHit] = useState<SearchHit | null>(null);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const q = query.trim();
    if (!q || searching) return;
    setSearching(true);
    setError(null);
    setHits(null);
    platformSearchFind(accountId, userId, {
      query: q,
      context_type: contextType === "" ? null : contextType,
      tags: parseTagsInput(tags),
      since: since || null,
      until: until || null,
    })
      .then(setHits)
      .catch((err) => setError(platformErrorMessage(err, "检索失败，请稍后重试")))
      .finally(() => setSearching(false));
  }

  return (
    <section className="card" data-testid="platform-member-search-panel">
      <form className="admin-filter-bar" onSubmit={handleSubmit} data-testid="platform-member-search-form">
        <input
          type="search"
          placeholder="检索成员私有数据（Memory / Resource / Skill）"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          required
          maxLength={200}
          aria-label="检索词"
          data-testid="platform-member-search-query"
        />
        <select
          value={contextType}
          onChange={(e) => setContextType(e.target.value as SearchContextType | "")}
          aria-label="对象类型"
          data-testid="platform-member-search-context"
        >
          <option value="">全部类型</option>
          <option value="memory">Memory</option>
          <option value="resource">Resource</option>
          <option value="skill">Skill</option>
        </select>
        <input
          type="text"
          placeholder="标签（key=value，空格分隔）"
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          aria-label="标签过滤"
          data-testid="platform-member-search-tags"
        />
        <input
          type="date"
          value={since}
          onChange={(e) => setSince(e.target.value)}
          aria-label="起始时间"
          data-testid="platform-member-search-since"
        />
        <input
          type="date"
          value={until}
          onChange={(e) => setUntil(e.target.value)}
          aria-label="结束时间"
          data-testid="platform-member-search-until"
        />
        <button type="submit" className="primary-button" disabled={searching} data-testid="platform-member-search-submit">
          {searching ? "检索中…" : "检索"}
        </button>
      </form>

      {error ? (
        <p className="login-error" role="alert" data-testid="platform-member-search-error">
          {error}
        </p>
      ) : null}

      {hits != null && hits.length === 0 ? (
        <div className="empty-state" data-testid="platform-member-search-empty">
          没有匹配的结果。
        </div>
      ) : null}

      {hits != null && hits.length > 0 ? (
        <ul className="search-hit-list" data-testid="platform-member-search-hits">
          {hits.map((hit, index) => (
            <li key={`${hit.context_type}-${hit.ref_id ?? hit.display_name}-${index}`}>
              <PlatformMemberSearchHitRow accountId={accountId} userId={userId} hit={hit} onOpenMemory={() => setMemoryHit(hit)} />
            </li>
          ))}
        </ul>
      ) : null}

      {memoryHit ? (
        <div className="memory-drawer" role="dialog" aria-label="Memory 只读详情" data-testid="platform-member-memory-drawer">
          <div className="memory-drawer-head">
            <h4>Memory 详情（只读）</h4>
            <button type="button" onClick={() => setMemoryHit(null)} aria-label="关闭抽屉">
              关闭
            </button>
          </div>
          <dl className="memory-drawer-body">
            <div className="profile-row">
              <dt>Memory 类型</dt>
              <dd data-testid="platform-member-memory-drawer-type">{memoryHit.memory_type || "—"}</dd>
            </div>
            <div className="profile-row">
              <dt>摘要</dt>
              <dd data-testid="platform-member-memory-drawer-abstract">{memoryHit.abstract ?? "—"}</dd>
            </div>
            <div className="profile-row">
              <dt>匹配原因</dt>
              <dd data-testid="platform-member-memory-drawer-reason">{memoryHit.match_reason ?? "—"}</dd>
            </div>
          </dl>
        </div>
      ) : null}
    </section>
  );
}

function PlatformMemberSearchHitRow({
  accountId,
  userId,
  hit,
  onOpenMemory,
}: {
  accountId: string;
  userId: string;
  hit: SearchHit;
  onOpenMemory: () => void;
}) {
  const label = hit.context_type === "memory" ? "Memory" : hit.context_type === "resource" ? "Resource" : "Skill";
  if (hit.context_type === "memory") {
    return (
      <button type="button" className="search-hit" onClick={onOpenMemory} data-testid="platform-member-search-hit-memory">
        <span className="status-badge">{label}</span>
        <strong>{hit.display_name}</strong>
        <span className="profile-hint">{hit.abstract ?? "—"}</span>
      </button>
    );
  }
  if (!hit.ref_id || !isProductId(hit.ref_id)) {
    return (
      <span className="search-hit" data-testid="platform-member-search-hit-invalid">
        <span className="status-badge">{label}</span>
        <strong>{hit.display_name}</strong>
        <span className="profile-hint">{hit.abstract ?? "—"}</span>
      </span>
    );
  }
  // AC⑦：只跳平台成员只读详情（/platform/accounts/{id}/users/{uid}/...），不跳 /app 分区
  const to =
    hit.context_type === "resource"
      ? `/platform/accounts/${accountId}/users/${userId}/resources/${hit.ref_id}`
      : `/platform/accounts/${accountId}/users/${userId}/skills/${hit.ref_id}`;
  return (
    <Link to={to} className="search-hit" data-testid={`platform-member-search-hit-${hit.context_type}`}>
      <span className="status-badge">{label}</span>
      <span className="status-badge">{hit.visibility === "user_private" ? "成员私有" : "Account 共享"}</span>
      <strong>{hit.display_name}</strong>
      <span className="profile-hint">{hit.abstract ?? "—"}</span>
    </Link>
  );
}

/* ── Sessions（11 §74.2：只读历史 + Memory Impact，无删除/恢复）── */

function PlatformMemberSessionsPanel({ accountId, userId }: { accountId: string; userId: string }) {
  const [sessions, setSessions] = useState<SessionListItem[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [messages, setMessages] = useState<SessionMessage[] | null>(null);
  const [impact, setImpact] = useState<MemoryImpact | null>(null);
  const [detailState, setDetailState] = useState<"idle" | "loading" | "ready" | "error">("idle");

  const load = useCallback(() => {
    setListError(null);
    setSessions(null);
    listPlatformMemberSessions(accountId, userId)
      .then((page) => setSessions(page.items))
      .catch((err) => setListError(platformErrorMessage(err, "无法加载 Session 列表")));
  }, [accountId, userId]);

  useEffect(() => {
    load();
  }, [load]);

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
      fetchPlatformMemberSessionDetail(accountId, userId, selectedId),
      fetchPlatformMemberSessionMessages(accountId, userId, selectedId),
      fetchPlatformMemberMemoryImpact(accountId, userId, selectedId),
    ])
      .then(([d, msgs, imp]) => {
        if (cancelled) return;
        setDetail(d);
        setMessages(msgs);
        setImpact(imp);
        setDetailState("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setDetailState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [accountId, userId, selectedId]);

  return (
    <section className="card" data-testid="platform-member-sessions-panel">
      {listError ? (
        <div className="admin-load-error" role="alert" data-testid="platform-member-sessions-error">
          <p className="login-error">{listError}</p>
          <button type="button" onClick={load} data-testid="platform-member-sessions-retry">
            重试
          </button>
        </div>
      ) : null}
      {sessions != null && sessions.length === 0 ? (
        <div className="empty-state" data-testid="platform-member-sessions-empty">
          该成员暂无 Session。
        </div>
      ) : null}
      {sessions != null && sessions.length > 0 ? (
        <div className="sessions-layout">
          <aside className="sessions-sidebar" data-testid="platform-member-sessions-list">
            <h3>Session 列表（{sessions.length}，只读）</h3>
            <ul className="session-list">
              {sessions.map((session) => (
                <li key={session.id}>
                  <button
                    type="button"
                    className={`session-list-item ${selectedId === session.id ? "selected" : ""}`}
                    onClick={() => setSelectedId(session.id)}
                    data-testid={`platform-member-session-item-${session.id}`}
                  >
                    <strong>{session.title}</strong>
                    <span className="profile-hint">
                      {session.message_count} 条消息 ·{" "}
                      {session.updated_at ? new Date(session.updated_at).toLocaleString() : "无活动"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </aside>
          <main className="sessions-main" data-testid="platform-member-sessions-main">
            {!selectedId ? (
              <div className="empty-state" data-testid="platform-member-sessions-none">
                选择左侧 Session 查看只读历史。
              </div>
            ) : detailState === "loading" ? (
              <p>加载中…</p>
            ) : detailState === "error" ? (
              <p className="profile-hint">Session 详情加载失败（可能已删除或不可见）。</p>
            ) : detail ? (
              <>
                <header className="sessions-detail-head">
                  <h3>{detail.title}</h3>
                  <dl className="home-count-grid sessions-meta">
                    <div>
                      <dt>来源客户端</dt>
                      <dd>{detail.client_name}</dd>
                    </div>
                    <div>
                      <dt>消息数</dt>
                      <dd>{detail.message_count}</dd>
                    </div>
                    <div>
                      <dt>Commit 数</dt>
                      <dd>{detail.commit_count}</dd>
                    </div>
                    <div>
                      <dt>最后同步</dt>
                      <dd>{detail.last_sync_at ? new Date(detail.last_sync_at).toLocaleString() : "—"}</dd>
                    </div>
                  </dl>
                </header>
                <section className="card" aria-label="消息历史">
                  <h3>消息历史</h3>
                  <SessionMessages messages={messages ?? []} />
                </section>
                {impact ? <MemoryImpactPanel impact={impact} /> : null}
              </>
            ) : null}
          </main>
        </div>
      ) : null}
    </section>
  );
}

/* ── Resources（09 §38.2 成员只读：无导入/编辑/Refresh/Watch/发布/删除/下载）── */

function PlatformMemberResourcesPanel({ accountId, userId }: { accountId: string; userId: string }) {
  const [items, setItems] = useState<{ items: ResourceSummary[]; next_cursor: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const load = useCallback(() => {
    setError(null);
    setItems(null);
    listPlatformMemberResources(accountId, userId)
      .then(setItems)
      .catch((err) => setError(platformErrorMessage(err, "无法加载成员 Resource")));
  }, [accountId, userId]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleLoadMore() {
    if (!items?.next_cursor || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await listPlatformMemberResources(accountId, userId);
      setItems((prev) =>
        prev ? { items: [...prev.items, ...page.items], next_cursor: page.next_cursor } : page,
      );
    } catch (err) {
      setError(platformErrorMessage(err, "加载更多失败，请稍后重试"));
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <section className="card" data-testid="platform-member-resources-panel">
      {error ? (
        <div className="admin-load-error" role="alert" data-testid="platform-member-resources-error">
          <p className="login-error">{error}</p>
          <button type="button" onClick={load} data-testid="platform-member-resources-retry">
            重试
          </button>
        </div>
      ) : null}
      {items != null && items.items.length === 0 ? (
        <div className="empty-state" data-testid="platform-member-resources-empty">
          该成员暂无私有 Resource。
        </div>
      ) : null}
      {items != null && items.items.length > 0 ? (
        <>
          <table className="data-table" data-testid="platform-member-resources-list">
            <thead>
              <tr>
                <th>名称</th>
                <th>状态</th>
                <th>来源</th>
                <th>最近更新</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.items.map((row) => (
                <tr key={row.id} data-testid={`platform-member-resource-row-${row.id}`}>
                  <td>{row.name}</td>
                  <td>
                    <span className={`status-badge ${row.lifecycle_status}`}>
                      {LIFECYCLE_LABELS[row.lifecycle_status] ?? row.lifecycle_status}
                    </span>
                  </td>
                  <td>{SOURCE_TYPE_LABELS[row.source_type] ?? row.source_type}</td>
                  <td>{formatIsoDateTime(row.updated_at)}</td>
                  <td>
                    <Link
                      to={`/platform/accounts/${accountId}/users/${userId}/resources/${row.id.replace(/^res_/, "")}`}
                      data-testid={`platform-member-resource-view-${row.id}`}
                    >
                      查看
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {items.next_cursor ? (
            <div className="admin-pagination">
              <button
                type="button"
                onClick={() => void handleLoadMore()}
                disabled={loadingMore}
                data-testid="platform-member-resources-load-more"
              >
                {loadingMore ? "加载中…" : "加载更多"}
              </button>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

/* ── Skills（10 §61.3 成员只读；平台代发布不在 v0.1，Skill 始终只读，AC⑦）── */

function PlatformMemberSkillsPanel({ accountId, userId }: { accountId: string; userId: string }) {
  const [items, setItems] = useState<{ items: SkillRecord[]; next_cursor: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    setItems(null);
    listPlatformMemberSkills(accountId, userId)
      .then(setItems)
      .catch((err) => setError(platformErrorMessage(err, "无法加载成员 Skill")));
  }, [accountId, userId]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="card" data-testid="platform-member-skills-panel">
      {error ? (
        <div className="admin-load-error" role="alert" data-testid="platform-member-skills-error">
          <p className="login-error">{error}</p>
          <button type="button" onClick={load} data-testid="platform-member-skills-retry">
            重试
          </button>
        </div>
      ) : null}
      {items != null ? (
        <SkillList
          items={items.items}
          ownershipLabel="成员私有"
          detailHref={(skillId) => `/platform/accounts/${accountId}/users/${userId}/skills/${skillId}`}
          emptyText="该成员暂无私有 Skill。"
          hint="平台对成员私有 Skill 始终只读（10 §60）：无发布、编辑、删除或恢复入口（范围 Out：平台代用户发布/写入 Skill）。"
        />
      ) : null}
    </section>
  );
}
