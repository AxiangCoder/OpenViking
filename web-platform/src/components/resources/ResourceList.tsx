/**
 * Resource 列表组件（09 §39，P3-E4）。
 *
 * - 私有/共享两分区共用：scope 由挂载入口固定（AC①：新增归属无下拉）；
 * - 服务端筛选（来源类型/处理状态）+ Cursor 分页（09 §39.1）；
 *   标签/更新时间筛选为对已加载行过滤（05 §12.6 同款客户端过滤模式，
 *   后端列表 API 仅支持 source_type/status）；
 * - 排序：最近更新（默认）/名称/最近处理，对已加载行排序（09 §39.1）；
 * - 行动作由写权限控制：无写权限只提供「查看」与允许时的单文件下载（09 §39.3）；
 * - 加载失败保留页面框架，展示 Request ID 与重试（09 §39.4）。
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { resourceDetailPath } from "@/lib/links";
import {
  formatDateTime,
  latestOperationText,
  LIFECYCLE_LABELS,
  listResources,
  SOURCE_TYPE_LABELS,
  watchColumnText,
  type ResourceScopeKind,
  type ResourceSourceTypeFilter,
  type ResourceSummary,
} from "@/features/resources";

export type ResourceSortKey = "updated_at" | "name" | "processed_at";

export interface ResourceListRowAction {
  label: string;
  onRun: (row: ResourceSummary) => void;
  danger?: boolean;
}

export interface ResourceListProps {
  scope: ResourceScopeKind;
  /** 跳转目标 base（私有/共享分区由挂载入口决定）。 */
  detailBase: "private" | "shared";
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  /** 行级写动作（无写权限时只显示查看，09 §39.3）。 */
  rowActions?: (row: ResourceSummary) => ResourceListRowAction[];
  emptyHint: string;
  /** 列表加载完成回调（记录最近使用的合法分区用，09 §38.1）。 */
  onLoaded?: (loaded: boolean) => void;
  emptyDescription?: string;
}

const SOURCE_FILTERS: { value: ResourceSourceTypeFilter | "all"; label: string }[] = [
  { value: "all", label: "全部来源" },
  { value: "upload", label: "文件" },
  { value: "web", label: "网页" },
  { value: "git", label: "Git 仓库" },
];

const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: "all", label: "全部状态" },
  { value: "active", label: "可用" },
  { value: "provisioning", label: "首次处理中" },
  { value: "failed", label: "首次处理失败" },
];

const SORT_OPTIONS: { value: ResourceSortKey; label: string }[] = [
  { value: "updated_at", label: "最近更新" },
  { value: "name", label: "名称" },
  { value: "processed_at", label: "最近处理" },
];

function sortValueOf(row: ResourceSummary, key: ResourceSortKey): string | number {
  if (key === "name") return row.name.toLowerCase();
  if (key === "processed_at") return row.processing.last_succeeded_at ?? "";
  return row.updated_at ?? "";
}

export default function ResourceList({
  scope,
  detailBase,
  title,
  subtitle,
  actions,
  rowActions,
  emptyHint,
  onLoaded,
  emptyDescription,
}: ResourceListProps) {
  const [items, setItems] = useState<ResourceSummary[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(
    null,
  );
  const [loadingMore, setLoadingMore] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<ResourceSourceTypeFilter | "all">("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortKey, setSortKey] = useState<ResourceSortKey>("updated_at");
  const [tagFilter, setTagFilter] = useState("");

  const load = () => {
    setLoadError(null);
    setItems(null);
    setNextCursor(null);
    listResources(scope, {
      sourceType: sourceFilter === "all" ? undefined : sourceFilter,
      status: statusFilter === "all" ? undefined : statusFilter,
    })
      .then((page) => {
        setItems(page.items);
        setNextCursor(page.next_cursor);
        onLoaded?.(true);
      })
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "无法加载 Resource 列表" : "无法加载 Resource 列表",
          requestId: isPlatformError(error) ? error.requestId : null,
        });
        onLoaded?.(false);
      });
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, sourceFilter, statusFilter]);

  function handleLoadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    listResources(scope, {
      sourceType: sourceFilter === "all" ? undefined : sourceFilter,
      status: statusFilter === "all" ? undefined : statusFilter,
      cursor: nextCursor,
    })
      .then((page) => {
        setItems((prev) => [...(prev ?? []), ...page.items]);
        setNextCursor(page.next_cursor);
      })
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "加载更多失败" : "加载更多失败",
          requestId: isPlatformError(error) ? error.requestId : null,
        });
      })
      .finally(() => setLoadingMore(false));
  }

  const visible = useMemo(() => {
    const rows = items ?? [];
    const keyword = tagFilter.trim().toLowerCase();
    const filtered = keyword
      ? rows.filter(
          (row) =>
            row.name.toLowerCase().includes(keyword) ||
            (row.description ?? "").toLowerCase().includes(keyword) ||
            row.tags.some((tag) => tag.toLowerCase().includes(keyword)),
        )
      : rows;
    return [...filtered].sort((a, b) => {
      const va = sortValueOf(a, sortKey);
      const vb = sortValueOf(b, sortKey);
      if (va < vb) return -1;
      if (va > vb) return 1;
      return 0;
    });
  }, [items, tagFilter, sortKey]);

  const hasActiveFilters = sourceFilter !== "all" || statusFilter !== "all" || tagFilter.trim() !== "";

  function clearFilters() {
    setSourceFilter("all");
    setStatusFilter("all");
    setTagFilter("");
  }

  return (
    <section className="admin-page" data-testid={`resource-list-${scope}`}>
      <header className="admin-page-header">
        <div>
          <h2>{title}</h2>
          {subtitle ? <p className="admin-account-context">{subtitle}</p> : null}
        </div>
        {actions}
      </header>

      <div className="admin-filter-bar">
        <label>
          来源
          <select
            value={sourceFilter}
            onChange={(event) => setSourceFilter(event.target.value as ResourceSourceTypeFilter | "all")}
          >
            {SOURCE_FILTERS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          状态
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
          >
            {STATUS_FILTERS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          排序
          <select value={sortKey} onChange={(event) => setSortKey(event.target.value as ResourceSortKey)}>
            {SORT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <input
          className="filter-keyword"
          type="search"
          placeholder="名称 / 说明 / 标签过滤（key=value）"
          value={tagFilter}
          onChange={(event) => setTagFilter(event.target.value)}
          aria-label="关键词过滤"
        />
        {hasActiveFilters ? (
          <button type="button" className="filter-clear" onClick={clearFilters}>
            清除筛选
          </button>
        ) : null}
      </div>

      {loadError ? (
        <div className="admin-load-error" data-testid="resource-list-error">
          <p>{loadError.message}</p>
          {loadError.requestId ? <code>Request ID: {loadError.requestId}</code> : null}
          <button type="button" onClick={load}>
            重试
          </button>
        </div>
      ) : null}

      {items === null && !loadError ? (
        <p className="admin-load-error">加载中…</p>
      ) : null}

      {items !== null && items.length === 0 ? (
        <div className="empty-state">
          <p>{emptyHint}</p>
          {emptyDescription ? <p className="profile-hint">{emptyDescription}</p> : null}
        </div>
      ) : null}

      {items !== null && items.length > 0 && visible.length === 0 ? (
        <div className="empty-state">
          <p>当前筛选无结果</p>
          <button type="button" className="filter-clear" onClick={clearFilters}>
            清除筛选
          </button>
        </div>
      ) : null}

      {items !== null && visible.length > 0 ? (
        <div className="card">
          <table className="data-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>来源</th>
                <th>标签</th>
                <th>状态</th>
                <th>自动同步</th>
                <th>更新时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => {
                const actionsOfRow = rowActions ? rowActions(row) : [];
                return (
                  <tr key={row.id}>
                    <td>
                      <Link to={resourceDetailPath(detailBase, row.id.replace(/^res_/, ""))}>
                        {row.name || "（未命名）"}
                      </Link>
                      <span className="resource-list-desc">
                        {row.description && row.description.length > 80
                          ? `${row.description.slice(0, 80)}…`
                          : row.description}
                      </span>
                    </td>
                    <td>
                      {SOURCE_TYPE_LABELS[row.source_type]}
                      {row.source_display ? (
                        <span className="resource-source-display"> · {row.source_display}</span>
                      ) : null}
                    </td>
                    <td>
                      {row.tags.slice(0, 3).map((tag) => (
                        <span key={tag} className="resource-tag">
                          {tag}
                        </span>
                      ))}
                      {row.tags.length > 3 ? <span className="resource-tag-more">+{row.tags.length - 3}</span> : null}
                    </td>
                    <td>
                      <span className={`status-badge ${row.lifecycle_status}`}>
                        {LIFECYCLE_LABELS[row.lifecycle_status]}
                      </span>
                      <span className="resource-op-line">{latestOperationText(row)}</span>
                    </td>
                    <td>
                      {watchColumnText(row.source_type, row.watch) ?? <span className="resource-muted">—</span>}
                    </td>
                    <td>{formatDateTime(row.updated_at)}</td>
                    <td>
                      <div className="row-actions">
                        <Link
                          to={resourceDetailPath(detailBase, row.id.replace(/^res_/, ""))}
                        >
                          查看
                        </Link>
                        {actionsOfRow.map((action) => (
                          <button
                            key={action.label}
                            type="button"
                            className={action.danger ? "link-danger" : undefined}
                            onClick={() => action.onRun(row)}
                          >
                            {action.label}
                          </button>
                        ))}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {nextCursor ? (
        <div className="admin-pagination">
          <button type="button" onClick={handleLoadMore} disabled={loadingMore}>
            {loadingMore ? "加载中…" : "加载更多"}
          </button>
        </div>
      ) : null}
    </section>
  );
}
