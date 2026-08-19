/**
 * /platform/accounts/{accountId}/resources 平台代管共享 Resource 管理
 * （09 §38.2/§40.1、05 §12.6 平台表，14 号计划 §98.9，P4-E4 AC②⑨）。
 *
 * - 目标 Account 是管理浏览的 Subject：页头明确 Account 上下文，不改变登录者
 *   身份、无 Account 切换入口（AC②）；
 * - 平台代管共享 Resource（`resource.account_shared.*.platform`）：列表/新增
 *   （上传/网页/Git 导入）/删除（AC⑨）；删除无平台级 deletion-preview 端点，
 *   弹窗展示静态影响说明（30 天回收期、Watch 暂停、在途任务取消由系统处理）；
 * - Skill 始终只读：本页只有 Resource，Skill 走只读页面（10 §60，AC⑨）；
 * - 列表筛选：来源/状态为服务端筛选（05 §12.6 同款），关键词过滤为对已加载
 *   行过滤；cursor 分页；
 * - 按钮按 Permission 隐藏（06 §13.4）；前端不是安全边界（AC①）。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { useMe } from "@/features/auth/useAuth";
import { canPerform } from "@/lib/permissions";
import PlatformImportDialog from "@/components/platform/PlatformImportDialog";
import {
  deletePlatformAccountResource,
  listPlatformAccountResources,
  listPlatformAccounts,
  platformErrorMessage,
  type PlatformAccount,
} from "@/features/iam/platform";
import {
  formatDateTime,
  LIFECYCLE_LABELS,
  latestOperationText,
  SOURCE_TYPE_LABELS,
  watchColumnText,
  type ResourceSourceTypeFilter,
  type ResourceSummary,
} from "@/features/resources";

type SortKey = "updated_at" | "name" | "processed_at";

function sortValueOf(row: ResourceSummary, key: SortKey): string | number {
  if (key === "name") return row.name.toLowerCase();
  if (key === "processed_at") return row.processing.last_succeeded_at ?? "";
  return row.updated_at ?? "";
}

export default function PlatformAccountResourcesPage() {
  const params = useParams({ strict: false }) as Record<string, string>;
  const accountId = params["accountId"] ?? "";
  const me = useMe();

  const [account, setAccount] = useState<PlatformAccount | null>(null);
  const [items, setItems] = useState<ResourceSummary[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<ResourceSourceTypeFilter | "all">("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortKey, setSortKey] = useState<SortKey>("updated_at");
  const [tagFilter, setTagFilter] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ResourceSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const accountLabel = account?.name ?? account?.code ?? accountId ?? "—";
  const canWrite = canPerform(me, "resource.account_shared.write.platform");
  const canDelete = canPerform(me, "resource.account_shared.delete.platform");

  const load = useCallback(() => {
    setLoadError(null);
    setItems(null);
    setNextCursor(null);
    listPlatformAccounts()
      .then((page) => {
        setAccount(page.items.find((a) => a.id === accountId) ?? null);
      })
      .catch(() => setAccount(null));
    listPlatformAccountResources(accountId, {
      sourceType: sourceFilter === "all" ? undefined : sourceFilter,
      status: statusFilter === "all" ? undefined : statusFilter,
    })
      .then((page) => {
        setItems(page.items);
        setNextCursor(page.next_cursor);
      })
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "无法加载 Resource 列表" : "无法加载 Resource 列表",
          requestId: isPlatformError(error) ? error.requestId : null,
        });
      });
  }, [accountId, sourceFilter, statusFilter]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleLoadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setActionError(null);
    try {
      const page = await listPlatformAccountResources(accountId, {
        sourceType: sourceFilter === "all" ? undefined : sourceFilter,
        status: statusFilter === "all" ? undefined : statusFilter,
        cursor: nextCursor,
      });
      setItems((prev) => [...(prev ?? []), ...page.items]);
      setNextCursor(page.next_cursor);
    } catch (error) {
      setActionError(platformErrorMessage(error, "加载更多失败，请稍后重试"));
    } finally {
      setLoadingMore(false);
    }
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

  function handleConfirmDelete() {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    setActionError(null);
    deletePlatformAccountResource(accountId, deleteTarget.id, deleteTarget.version)
      .then((result) => {
        setNotice(
          `已删除「${deleteTarget.name || "（未命名）"}」：进入 30 天回收期，恢复截止 ${formatDateTime(result.restore_until)}。`,
        );
        setDeleteTarget(null);
        load();
      })
      .catch((error) => {
        setActionError(platformErrorMessage(error, "删除失败，请稍后重试"));
      })
      .finally(() => setDeleting(false));
  }

  return (
    <div className="admin-page" data-testid="platform-account-resources-page">
      <div className="admin-page-header">
        <div>
          <h2>共享 Resource（平台代管）</h2>
          <p className="admin-account-context" data-testid="platform-resources-account-context">
            管理浏览目标 Account：<strong>{accountLabel}</strong>
            （选择目标 Account 是管理浏览，不改变登录者身份，无 Account 切换入口，AC②）
          </p>
        </div>
        {canWrite ? (
          <button
            type="button"
            className="primary-button"
            onClick={() => {
              setActionError(null);
              setImportOpen(true);
            }}
            data-testid="platform-resource-import-open"
          >
            新增 Resource
          </button>
        ) : null}
      </div>
      <div className="profile-actions">
        <Link to="/platform/accounts" className="placeholder-back" data-testid="platform-resources-back-accounts">
          ← 返回 Accounts
        </Link>
      </div>

      <div className="admin-filter-bar" data-testid="platform-resources-filter">
        <label>
          来源
          <select
            value={sourceFilter}
            onChange={(event) => setSourceFilter(event.target.value as ResourceSourceTypeFilter | "all")}
          >
            <option value="all">全部来源</option>
            <option value="upload">文件</option>
            <option value="web">网页</option>
            <option value="git">Git 仓库</option>
          </select>
        </label>
        <label>
          状态
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">全部状态</option>
            <option value="active">可用</option>
            <option value="provisioning">首次处理中</option>
            <option value="failed">首次处理失败</option>
          </select>
        </label>
        <label>
          排序
          <select value={sortKey} onChange={(event) => setSortKey(event.target.value as SortKey)}>
            <option value="updated_at">最近更新</option>
            <option value="name">名称</option>
            <option value="processed_at">最近处理</option>
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
          <button type="button" className="filter-clear" onClick={() => { setSourceFilter("all"); setStatusFilter("all"); setTagFilter(""); }}>
            清除筛选
          </button>
        ) : null}
      </div>

      {loadError ? (
        <div className="admin-load-error" role="alert" data-testid="platform-resources-load-error">
          <p className="login-error">{loadError.message}</p>
          {loadError.requestId ? (
            <p className="error-page-request-id">
              请求 ID：<code>{loadError.requestId}</code>
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="button" onClick={load} data-testid="platform-resources-retry">
              重试
            </button>
          </div>
        </div>
      ) : null}

      {items === null && !loadError ? (
        <p className="profile-hint" data-testid="platform-resources-loading">
          加载中…
        </p>
      ) : null}

      {items !== null && items.length === 0 ? (
        <div className="empty-state" data-testid="platform-resources-empty">
          该 Account 暂无共享 Resource。
          {canWrite ? <p className="profile-hint">点击「新增 Resource」为 {accountLabel} 代管共享内容（09 §40.1）。</p> : null}
        </div>
      ) : null}

      {items !== null && items.length > 0 && visible.length === 0 ? (
        <div className="empty-state">
          <p>当前筛选无结果</p>
          <button type="button" className="filter-clear" onClick={() => { setSourceFilter("all"); setStatusFilter("all"); setTagFilter(""); }}>
            清除筛选
          </button>
        </div>
      ) : null}

      {items !== null && visible.length > 0 ? (
        <section className="card" data-testid="platform-resources-list">
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
              {visible.map((row) => (
                <tr key={row.id} data-testid={`platform-resource-row-${row.id}`}>
                  <td>
                    <Link to={`/platform/accounts/${accountId}/resources/${row.id.replace(/^res_/, "")}`}>
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
                      <Link to={`/platform/accounts/${accountId}/resources/${row.id.replace(/^res_/, "")}`}>
                        查看
                      </Link>
                      {canDelete && row.lifecycle_status === "active" ? (
                        <button
                          type="button"
                          className="link-danger"
                          onClick={() => {
                            setActionError(null);
                            setDeleteTarget(row);
                          }}
                          data-testid={`platform-resource-delete-${row.id}`}
                        >
                          删除
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {nextCursor ? (
            <div className="admin-pagination">
              <button
                type="button"
                onClick={() => void handleLoadMore()}
                disabled={loadingMore}
                data-testid="platform-resources-load-more"
              >
                {loadingMore ? "加载中…" : "加载更多"}
              </button>
            </div>
          ) : null}
        </section>
      ) : null}

      {actionError ? (
        <p className="login-error" role="alert" data-testid="platform-resources-action-error">
          {actionError}
        </p>
      ) : null}
      {notice ? (
        <p className="profile-success" role="status" data-testid="platform-resources-notice">
          {notice}
        </p>
      ) : null}

      {importOpen ? (
        <PlatformImportDialog
          accountId={accountId}
          targetLabel={`${accountLabel} 共享 Resource（平台代管）`}
          onClose={() => setImportOpen(false)}
          onImported={() => setImportOpen(false)}
        />
      ) : null}

      {/* 删除确认（平台无 deletion-preview 端点 → 静态影响说明，09 §45.1 语义） */}
      {deleteTarget ? (
        <div className="confirm-dialog" role="dialog" aria-label="删除 Resource 确认">
          <h3>删除 Resource</h3>
          <ul className="admin-dialog-list" data-testid="platform-resource-delete-impact">
            <li>名称：{deleteTarget.name || "（未命名）"}</li>
            <li>范围：{accountLabel} 共享 Resource（平台代管）</li>
            <li>删除后立即从正常列表与检索中隐藏，进入 30 天回收期（恢复截止以删除结果为准）。</li>
            <li>已配置的自动同步将暂停；在途处理任务由系统请求取消（09 §45.1/§45.3）。</li>
          </ul>
          <p className="profile-hint">确认后立即删除，无需再次输入密码或 Account 名称。</p>
          <div className="profile-actions">
            <button type="button" onClick={() => setDeleteTarget(null)} disabled={deleting}>
              取消
            </button>
            <button
              type="button"
              className="danger-button"
              onClick={handleConfirmDelete}
              disabled={deleting}
              data-testid="platform-resource-delete-confirm"
            >
              {deleting ? "删除中…" : "确认删除"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
