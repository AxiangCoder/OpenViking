/**
 * /app/search 统一检索页（11 §69，06 §13.7，P3-E3 AC②③④）。
 *
 * - 两种模式：快速检索（默认，不加载 Session）与结合会话检索
 *   （必须选择当前 User 自己且未删除的 Session，AC②）；
 * - 主表单：检索词/模式/内容类型；「更多筛选」：结构化标签 `key=value`
 *   （多项 AND）+ 更新时间范围（固定映射 `updated_at`，无创建时间切换）；
 * - 请求体仅含白名单字段（AC③）：`query/context_type/tags/since/until`
 *   （结合会话模式追加 `session_id`），前端无法构造/透传调试字段；
 * - 默认范围=我的私有 + Account 共享（服务端固定，AC②）；
 * - 结果列表 + 只读抽屉（AC④，组件 SearchResultList）；
 * - 空状态/错误按 11 §69.5：未检索提示输入；无结果保留条件提示修改；
 *   Session 不可见 → SESSION_NOT_FOUND 提示；引擎不可用 → 可重试错误。
 */

import { useEffect, useRef, useState, type FormEvent } from "react";
import { isPlatformError } from "@/lib/platform-client";
import {
  parseTagsInput,
  searchFind,
  searchWithSession,
  type SearchContextType,
  type SearchHit,
} from "@/features/search/search";
import { listSessions, type SessionListItem } from "@/features/sessions/sessions";
import SearchResultList from "@/components/search/SearchResultList";

type SearchMode = "find" | "with-session";

function filtersTextOf(
  contextType: SearchContextType | "" | null,
  tags: string[],
  since: string,
  until: string,
): string | null {
  const parts: string[] = [];
  if (contextType) parts.push(`类型=${contextType}`);
  if (tags.length > 0) parts.push(`标签=${tags.join(" 且 ")}`);
  if (since) parts.push(`更新不早于 ${since}`);
  if (until) parts.push(`更新不晚于 ${until}`);
  return parts.length > 0 ? parts.join("；") : null;
}

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("find");
  const [contextType, setContextType] = useState<SearchContextType | "">("");
  const [tagsRaw, setTagsRaw] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [sessionId, setSessionId] = useState("");

  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [sessionsState, setSessionsState] = useState<"idle" | "loading" | "ready" | "error">("idle");

  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  // AC②：结合会话检索只列当前 User 自己且未删除的 Session（服务端过滤）；
  // 快速检索不加载 Session（仅切换到结合会话模式时才请求）
  const sessionsFetchStarted = useRef(false);
  useEffect(() => {
    if (mode !== "with-session" || sessionsFetchStarted.current) return;
    sessionsFetchStarted.current = true;
    let cancelled = false;
    setSessionsState("loading");
    listSessions()
      .then((result) => {
        if (cancelled) return;
        setSessions(result.items);
        setSessionsState("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setSessionsState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [mode]);

  async function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (searching) return;
    setError(null);
    const trimmed = query.trim();
    if (!trimmed) {
      setError("请输入检索内容。");
      return;
    }
    if (mode === "with-session" && !sessionId) {
      setError("结合会话检索需要先选择一个自己的 Session。");
      return;
    }
    const tags = parseTagsInput(tagsRaw);
    const params = {
      query: trimmed,
      context_type: contextType || null,
      tags,
      since: since || null,
      until: until || null,
    };
    setSearching(true);
    setHits(null);
    try {
      const items =
        mode === "find"
          ? await searchFind(params)
          : await searchWithSession(params, sessionId);
      setHits(items);
      setSearched(true);
    } catch (err) {
      setHits(null);
      setSearched(true);
      if (isPlatformError(err) && err.code === "SESSION_NOT_FOUND") {
        setError("所选 Session 不存在或已被删除，请重新选择。");
      } else if (isPlatformError(err) && err.code === "SEARCH_UNAVAILABLE") {
        setError("检索引擎暂不可用，请稍后重试。");
      } else if (isPlatformError(err) && err.code === "INVALID_SEARCH_FILTER") {
        setError("筛选条件不合法：标签需为 key=value 形式，时间范围需为合法日期。");
      } else {
        setError(isPlatformError(err) ? err.message || "检索失败，请稍后重试" : "检索失败，请稍后重试");
      }
    } finally {
      setSearching(false);
    }
  }

  const filtersText = filtersTextOf(contextType, parseTagsInput(tagsRaw), since, until);
  const showEmptyPrompt = !searched && hits === null;

  return (
    <div className="search-page" data-testid="search-page">
      <h2 className="page-title">统一检索</h2>

      <form className="card search-form" onSubmit={handleSearch} data-testid="search-form">
        <label className="login-field">
          <span>检索词</span>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="输入检索内容"
            disabled={searching}
            data-testid="search-query"
          />
        </label>

        <div className="search-mode-row">
          <label className="search-inline-field">
            <span>模式</span>
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as SearchMode)}
              disabled={searching}
              data-testid="search-mode"
            >
              <option value="find">快速检索</option>
              <option value="with-session">结合会话检索</option>
            </select>
          </label>
          <label className="search-inline-field">
            <span>内容类型</span>
            <select
              value={contextType}
              onChange={(e) => setContextType(e.target.value as SearchContextType | "")}
              disabled={searching}
              data-testid="search-context-type"
            >
              <option value="">全部</option>
              <option value="memory">Memory</option>
              <option value="resource">Resource</option>
              <option value="skill">Skill</option>
            </select>
          </label>
          {mode === "with-session" ? (
            <label className="search-inline-field search-session-field">
              <span>Session（仅自己的未删除 Session）</span>
              <select
                value={sessionId}
                onChange={(e) => setSessionId(e.target.value)}
                disabled={searching || sessionsState !== "ready"}
                data-testid="search-session-select"
              >
                <option value="">选择 Session…</option>
                {sessions.map((session) => (
                  <option key={session.id} value={session.id}>
                    {session.title}
                  </option>
                ))}
              </select>
              {sessionsState === "error" ? (
                <span className="profile-hint">Session 列表加载失败，请稍后重试。</span>
              ) : null}
            </label>
          ) : null}
        </div>

        <details className="search-more-filters">
          <summary>更多筛选</summary>
          <div className="search-more-filters-body">
            <label className="login-field">
              <span>结构化标签（key=value，多项用空格分隔，需全部匹配）</span>
              <input
                type="text"
                value={tagsRaw}
                onChange={(e) => setTagsRaw(e.target.value)}
                placeholder="例如 project=openviking"
                disabled={searching}
                data-testid="search-tags"
              />
            </label>
            <div className="search-time-row">
              <label className="search-inline-field">
                <span>更新时间不早于</span>
                <input
                  type="date"
                  value={since}
                  onChange={(e) => setSince(e.target.value)}
                  disabled={searching}
                  data-testid="search-since"
                />
              </label>
              <label className="search-inline-field">
                <span>更新时间不晚于</span>
                <input
                  type="date"
                  value={until}
                  onChange={(e) => setUntil(e.target.value)}
                  disabled={searching}
                  data-testid="search-until"
                />
              </label>
            </div>
            <p className="profile-hint">时间范围固定按更新时间（updated_at）过滤。</p>
          </div>
        </details>

        {error ? (
          <p className="login-error" role="alert" data-testid="search-error">
            {error}
          </p>
        ) : null}

        <div className="row-actions">
          <button type="submit" className="primary-button" disabled={searching} data-testid="search-submit">
            {searching ? "检索中…" : "检索"}
          </button>
        </div>
      </form>

      {showEmptyPrompt && !error ? (
        <div className="empty-state" data-testid="search-prompt">
          输入检索内容开始检索。默认范围：我的私有数据 + 当前 Account 共享数据。
        </div>
      ) : null}

      {hits !== null ? (
        <SearchResultList hits={hits} filtersText={filtersText} loading={searching} />
      ) : null}
    </div>
  );
}
