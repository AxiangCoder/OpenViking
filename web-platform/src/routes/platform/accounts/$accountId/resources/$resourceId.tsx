/**
 * /platform/accounts/{accountId}/resources/{resourceId} 平台代管共享 Resource 详情
 * （09 §38.2/§42，05 §12.6 平台表，14 号计划 §98.9，P4-E4 AC⑨）。
 *
 * - 概览：名称/状态/描述/来源/标签/内容统计/自动同步摘要/创建与更新时间；
 * - 管理动作按权限展示：编辑信息（PATCH 乐观锁）、Refresh（POST）、删除
 *   （确认弹窗 + If-Match 乐观锁）——`resource.account_shared.write.platform` /
 *   `resource.account_shared.delete.platform`（AC⑨）；
 * - 内容：节点只读列表（平台端点不提供单节点读取/下载，05 §12.6 同款限制）；
 * - 活动：Operations 只读列表（平台端点无取消）；
 * - 自动同步只展示摘要（v0.1 平台代管不提供 Watch 配置入口，范围 In 之外）；
 * - 跳转参数契约：URL 只携带产品 ID（lib/links.ts，非法 ID 404 语义）。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { useMe } from "@/features/auth/useAuth";
import { canPerform } from "@/lib/permissions";
import { isProductId } from "@/lib/links";
import {
  deletePlatformAccountResource,
  fetchPlatformAccountResource,
  listPlatformAccountResourceNodes,
  listPlatformAccountResourceOperations,
  listPlatformAccounts,
  patchPlatformAccountResource,
  platformErrorMessage,
  refreshPlatformAccountResource,
  type PlatformAccount,
} from "@/features/iam/platform";
import {
  formatBytes,
  formatDateTime,
  LIFECYCLE_LABELS,
  OPERATION_STATUS_LABELS,
  operationTypeLabel,
  SOURCE_TYPE_LABELS,
  WATCH_STATE_LABELS,
  type OperationItem,
  type ResourceDetail,
  type ResourceNode,
  type ResourceSummary,
} from "@/features/resources";

type DetailTab = "overview" | "content" | "activity";

export default function PlatformAccountResourceDetailPage() {
  const params = useParams({ strict: false }) as Record<string, string>;
  const accountId = params["accountId"] ?? "";
  const resourceId = params["resourceId"] ?? "";
  const me = useMe();

  const [account, setAccount] = useState<PlatformAccount | null>(null);
  const [detail, setDetail] = useState<ResourceDetail | null>(null);
  const [nodes, setNodes] = useState<ResourceNode[] | null>(null);
  const [operations, setOperations] = useState<OperationItem[] | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(null);
  const [tab, setTab] = useState<DetailTab>("overview");
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const accountLabel = account?.name ?? account?.code ?? accountId ?? "—";
  const canWrite = canPerform(me, "resource.account_shared.write.platform");
  const canDelete = canPerform(me, "resource.account_shared.delete.platform");
  const canReadActivity = canPerform(me, "task.read.platform");

  const load = useCallback(() => {
    setLoadError(null);
    setDetail(null);
    setNodes(null);
    setOperations(null);
    listPlatformAccounts()
      .then((page) => {
        setAccount(page.items.find((a) => a.id === accountId) ?? null);
      })
      .catch(() => setAccount(null));
    // 非法产品 ID 直接 404 语义，不发起后端详情请求（跳转契约）
    if (!isProductId(resourceId)) return;
    fetchPlatformAccountResource(accountId, resourceId)
      .then(setDetail)
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "无法加载 Resource" : "无法加载 Resource",
          requestId: isPlatformError(error) ? error.requestId : null,
        });
      });
  }, [accountId, resourceId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (tab !== "content" || nodes != null || loadError) return;
    listPlatformAccountResourceNodes(accountId, resourceId)
      .then((page) => setNodes(page.items))
      .catch(() => setNodes([]));
  }, [tab, nodes, loadError, accountId, resourceId]);

  useEffect(() => {
    if (tab !== "activity" || operations != null || loadError) return;
    listPlatformAccountResourceOperations(accountId, resourceId)
      .then((page) => setOperations(page.items))
      .catch(() => setOperations([]));
  }, [tab, operations, loadError, accountId, resourceId]);

  const invalidId = !isProductId(resourceId);

  async function handleRefresh() {
    if (refreshBusy || !detail) return;
    setRefreshBusy(true);
    setActionError(null);
    try {
      const result = await refreshPlatformAccountResource(accountId, resourceId);
      setNotice(`已提交刷新（Operation ${result.operation_id}）；处理期间旧版本保持可读（09 §41.2）。`);
      load();
    } catch (error) {
      setActionError(platformErrorMessage(error, "刷新失败，请稍后重试"));
    } finally {
      setRefreshBusy(false);
    }
  }

  function handleSaved(updated: ResourceSummary) {
    setEditOpen(false);
    setNotice(`已保存「${updated.name}」的元数据。`);
    load();
  }

  async function handleReload() {
    try {
      return await fetchPlatformAccountResource(accountId, resourceId);
    } catch {
      return null;
    }
  }

  function handleConfirmDelete() {
    if (!detail || deleting) return;
    setDeleting(true);
    setActionError(null);
    deletePlatformAccountResource(accountId, resourceId, detail.version)
      .then((result) => {
        setNotice(
          `已删除「${detail.name || "（未命名）"}」：进入 30 天回收期，恢复截止 ${formatDateTime(result.restore_until)}。`,
        );
        setDeleteOpen(false);
        load();
      })
      .catch((error) => {
        setActionError(platformErrorMessage(error, "删除失败，请稍后重试"));
      })
      .finally(() => setDeleting(false));
  }

  const tabs: { key: DetailTab; label: string }[] = useMemo(
    () => [
      { key: "overview", label: "概览" },
      { key: "content", label: "内容" },
      ...(canReadActivity ? [{ key: "activity" as DetailTab, label: "活动" }] : []),
    ],
    [canReadActivity],
  );

  if (invalidId) {
    return (
      <div className="card" data-testid="platform-resource-invalid-id">
        <h2>未找到该 Resource</h2>
        <p className="profile-hint">
          地址中的产品 ID 无效（404 语义）。URL 只接受产品 ID，不接受 Viking URI / Account / User 标识符。
        </p>
        <Link to={`/platform/accounts/${accountId}/resources`} className="placeholder-back">
          ← 返回共享 Resource
        </Link>
      </div>
    );
  }

  return (
    <div className="admin-page" data-testid="platform-resource-detail-page">
      <div className="admin-page-header">
        <div>
          <h2>共享 Resource 详情（平台代管）</h2>
          <p className="admin-account-context" data-testid="platform-resource-detail-account">
            管理浏览目标 Account：<strong>{accountLabel}</strong>
            （选择目标 Account 是管理浏览，不改变登录者身份，AC②）
          </p>
        </div>
      </div>
      <div className="profile-actions">
        <Link to={`/platform/accounts/${accountId}/resources`} className="placeholder-back" data-testid="platform-resource-back-list">
          ← 返回共享 Resource
        </Link>
      </div>

      {loadError ? (
        <div className="admin-load-error" role="alert" data-testid="platform-resource-detail-load-error">
          <p className="login-error">{loadError.message}</p>
          {loadError.requestId ? (
            <p className="error-page-request-id">
              请求 ID：<code>{loadError.requestId}</code>
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="button" onClick={load} data-testid="platform-resource-detail-retry">
              重试
            </button>
          </div>
        </div>
      ) : null}

      {detail == null && !loadError ? (
        <p className="profile-hint" data-testid="platform-resource-detail-loading">
          加载中…
        </p>
      ) : null}

      {detail != null ? (
        <>
          <section className="card" data-testid="platform-resource-detail">
            <div className="admin-page-header">
              <h2 data-testid="platform-resource-detail-name">{detail.name}</h2>
              <span className="status-badge">
                {LIFECYCLE_LABELS[detail.lifecycle_status] ?? detail.lifecycle_status}
              </span>
              {detail.overview ? <span className="status-badge active">含概览</span> : null}
            </div>
            <dl className="profile-row">
              <dt>描述</dt>
              <dd>{detail.description ?? "—"}</dd>
              <dt>来源</dt>
              <dd>{SOURCE_TYPE_LABELS[detail.source_type] ?? detail.source_type} · {detail.source_display ?? "—"}</dd>
              <dt>标签</dt>
              <dd>{detail.tags.length > 0 ? detail.tags.join("、") : "—"}</dd>
              <dt>内容</dt>
              <dd>
                {detail.content.node_count} 个节点 · {formatBytes(detail.content.size_bytes)}
              </dd>
              <dt>最近成功处理</dt>
              <dd>{formatDateTime(detail.processing.last_succeeded_at)}</dd>
              <dt>自动同步</dt>
              <dd data-testid="platform-resource-detail-watch">
                {WATCH_STATE_LABELS[detail.watch.state] ?? detail.watch.state}
                {detail.watch.interval_minutes ? ` · 每 ${detail.watch.interval_minutes} 分钟` : ""}
              </dd>
              <dt>创建时间</dt>
              <dd>{formatDateTime(detail.created_at)}</dd>
              <dt>更新时间</dt>
              <dd>{formatDateTime(detail.updated_at)}</dd>
            </dl>
            {canWrite ? (
              <div className="profile-actions" data-testid="platform-resource-detail-actions">
                <button
                  type="button"
                  onClick={() => {
                    setActionError(null);
                    setEditOpen(true);
                  }}
                  data-testid="platform-resource-edit-open"
                >
                  编辑信息
                </button>
                <button
                  type="button"
                  onClick={() => void handleRefresh()}
                  disabled={refreshBusy}
                  data-testid="platform-resource-refresh"
                >
                  {refreshBusy ? "刷新中…" : "Refresh"}
                </button>
              </div>
            ) : null}
            {canDelete ? (
              <div className="profile-actions">
                <button
                  type="button"
                  className="danger-button"
                  onClick={() => {
                    setActionError(null);
                    setDeleteOpen(true);
                  }}
                  data-testid="platform-resource-delete-open"
                >
                  删除
                </button>
              </div>
            ) : null}
          </section>

          <div className="skill-tabs" role="tablist" aria-label="Resource 区块">
            {tabs.map((t) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                aria-selected={tab === t.key}
                onClick={() => setTab(t.key)}
                data-testid={`platform-resource-tab-${t.key}`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {tab === "overview" && detail.overview ? (
            <section className="card" data-testid="platform-resource-detail-overview">
              <h3>概览</h3>
              <pre className="skill-markdown">{detail.overview}</pre>
            </section>
          ) : null}

          {tab === "content" ? (
            <section className="card" data-testid="platform-resource-nodes">
              <h3>内容（节点只读）</h3>
              <p className="profile-hint" data-testid="platform-resource-nodes-note">
                平台端点不提供单节点读取与下载（05 §12.6 同款只读限制）。
              </p>
              {nodes == null ? (
                <p className="profile-hint">加载中…</p>
              ) : nodes.length === 0 ? (
                <div className="empty-state">该 Resource 暂无可见节点。</div>
              ) : (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>名称</th>
                      <th>类型</th>
                      <th>大小</th>
                    </tr>
                  </thead>
                  <tbody>
                    {nodes.map((node) => (
                      <tr key={node.node_id} data-testid={`platform-resource-node-${node.node_id}`}>
                        <td>{node.name}</td>
                        <td>{node.type === "dir" ? "目录" : "文件"}</td>
                        <td>{node.type === "file" ? formatBytes(node.size_bytes) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          ) : null}

          {tab === "activity" ? (
            <section className="card" data-testid="platform-resource-operations">
              <h3>活动（只读）</h3>
              <p className="profile-hint">
                平台端点不提供 Operation 取消（05 §12.6：内部任务禁止取消；平台活动取消按
                `task.cancel.platform` + 目标对象写权限，v0.1 平台代管不提供）。
              </p>
              {operations == null ? (
                <p className="profile-hint">加载中…</p>
              ) : operations.length === 0 ? (
                <div className="empty-state">暂无处理任务。</div>
              ) : (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>任务</th>
                      <th>状态</th>
                      <th>开始时间</th>
                      <th>结束时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {operations.map((op) => (
                      <tr key={op.id} data-testid={`platform-resource-operation-${op.id}`}>
                        <td>
                          {operationTypeLabel(op.operation_type)}
                          {op.error ? (
                            <p className="profile-hint">{op.error.summary || op.error.code || "处理失败"}</p>
                          ) : null}
                        </td>
                        <td>
                          <span className={`status-badge ${op.status === "failed" || op.status === "cancelled" ? "failed" : ""} ${op.status === "succeeded" ? "active" : ""}`}>
                            {OPERATION_STATUS_LABELS[op.status as keyof typeof OPERATION_STATUS_LABELS] ?? op.status}
                          </span>
                        </td>
                        <td>{op.created_at ? new Date(op.created_at).toLocaleString() : "—"}</td>
                        <td>{op.completed_at ? new Date(op.completed_at).toLocaleString() : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          ) : null}
        </>
      ) : null}

      {actionError ? (
        <p className="login-error" role="alert" data-testid="platform-resource-detail-action-error">
          {actionError}
        </p>
      ) : null}
      {notice ? (
        <p className="profile-success" role="status" data-testid="platform-resource-detail-notice">
          {notice}
        </p>
      ) : null}

      {/* ── 编辑信息（09 §42.4：乐观锁 version，冲突重新加载）── */}
      {editOpen && detail ? (
        <div className="confirm-dialog" role="dialog" aria-label="编辑 Resource 信息">
          <EditMetaForm
            resource={detail}
            onCancel={() => setEditOpen(false)}
            onSaved={handleSaved}
            onReload={handleReload}
            onError={(message) => setActionError(message)}
          />
        </div>
      ) : null}

      {/* ── 删除确认（09 §45.1 语义；无平台级 preview 端点 → 静态影响说明）── */}
      {deleteOpen && detail ? (
        <div className="confirm-dialog" role="dialog" aria-label="删除 Resource 确认">
          <h3>删除 Resource</h3>
          <ul className="admin-dialog-list">
            <li>名称：{detail.name || "（未命名）"}</li>
            <li>范围：{accountLabel} 共享 Resource（平台代管）</li>
            <li>删除后立即从正常列表与检索中隐藏，进入 30 天回收期（09 §45.2）。</li>
            <li>已配置的自动同步将暂停；在途处理任务由系统请求取消（09 §45.1/§45.3）。</li>
          </ul>
          <p className="profile-hint">确认后立即删除，无需再次输入密码或 Account 名称。</p>
          <div className="profile-actions">
            <button type="button" onClick={() => setDeleteOpen(false)} disabled={deleting}>
              取消
            </button>
            <button
              type="button"
              className="danger-button"
              onClick={handleConfirmDelete}
              disabled={deleting}
              data-testid="platform-resource-detail-delete-confirm"
            >
              {deleting ? "删除中…" : "确认删除"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/* ── 编辑信息表单（可编辑字段仅 display_name/description/tags，09 §42.4）── */

function EditMetaForm({
  resource,
  onCancel,
  onSaved,
  onReload,
  onError,
}: {
  resource: ResourceSummary;
  onCancel: () => void;
  onSaved: (updated: ResourceSummary) => void;
  onReload: () => Promise<ResourceDetail | null>;
  onError: (message: string) => void;
}) {
  const params = useParams({ strict: false }) as Record<string, string>;
  const accountId = params["accountId"] ?? "";
  const [name, setName] = useState(resource.name);
  const [description, setDescription] = useState(resource.description ?? "");
  const [tagsText, setTagsText] = useState((resource.tags ?? []).join(", "));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    if (saving) return;
    const invalid = name.trim().length === 0 ? "名称不能为空。" : name.trim().length > 128 ? "名称最多 128 字符。" : description.length > 1000 ? "说明最多 1000 字符。" : null;
    if (invalid) {
      setError(invalid);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await patchPlatformAccountResource(accountId, resource.id, {
        displayName: name.trim(),
        description: description.trim() === "" ? undefined : description.trim(),
        tags: tagsText
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        version: resource.version,
      });
      onSaved(updated);
    } catch (err) {
      if (isPlatformError(err) && err.code === "RESOURCE_VERSION_CONFLICT") {
        const latest = await onReload();
        if (latest) {
          setName(latest.name);
          setDescription(latest.description ?? "");
          setTagsText((latest.tags ?? []).join(", "));
          setError("信息已在其他窗口更新，页面已重新加载，请确认后再次提交。");
          return;
        }
      }
      setError(platformErrorMessage(err, "保存失败，请稍后重试"));
      onError(platformErrorMessage(err, "保存失败，请稍后重试"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <h3>编辑 Resource 信息</h3>
      <form
        className="admin-form"
        onSubmit={(event) => {
          event.preventDefault();
          void handleSubmit();
        }}
        data-testid="platform-resource-edit-form"
      >
        <label className="login-field">
          <span>名称（1–128 字符）</span>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} maxLength={128} disabled={saving} />
        </label>
        <label className="login-field">
          <span>说明（可选，最多 1000 字符）</span>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1000} rows={2} disabled={saving} />
        </label>
        <label className="login-field">
          <span>标签（可选，最多 20 个，严格 key=value，逗号分隔）</span>
          <input type="text" value={tagsText} onChange={(e) => setTagsText(e.target.value)} disabled={saving} placeholder="type=requirement, project=openviking" />
        </label>
        {error ? (
          <p className="login-error" role="alert" data-testid="platform-resource-edit-error">
            {error}
          </p>
        ) : null}
        <div className="profile-actions">
          <button type="submit" className="primary-button" disabled={saving} data-testid="platform-resource-edit-submit">
            {saving ? "保存中…" : "保存"}
          </button>
          <button type="button" onClick={onCancel} disabled={saving}>
            取消
          </button>
        </div>
      </form>
    </div>
  );
}
