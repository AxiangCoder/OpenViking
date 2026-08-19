/**
 * Resource 详情页（09 §42，P3-E4 AC③④⑤⑥⑦）。
 *
 * - 头部：名称/私有/共享标识、来源类型与脱敏来源、可用/处理中/最近同步
 *   失败/待删除状态、最近成功处理/更新时间、标签、按 Permission 显示
 *   「编辑信息」「Refresh/替换文件」「自动同步」「删除」（09 §42.1）；
 * - 四标签：概览 / 内容（树+预览+下载+资源内检索）/ 自动同步 / 活动
 *   （09 §42.2）；「复制链接」复制产品 URL 不复制 Viking URI（09 §42.1）；
 * - 元数据编辑乐观锁冲突重新加载（AC④）；Refresh/替换/Watch 期间旧版本
 *   可读、RESOURCE_BUSY 禁用按钮（AC⑤）；删除预览弹窗完整（AC⑥）；
 * - 发布仅 Account Admin 自己的私有 Resource（AC⑦，09 §44.1）；
 * - 详情页不展示 Viking URI/宿主机路径/原始 Task ID（09 §48 #3，AC⑧）。
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { useMe } from "@/features/auth/useAuth";
import { canPerform, isAccountAdmin } from "@/lib/permissions";
import { isProductId } from "@/lib/links";
import {
  fetchCapabilities,
  fetchResourceDetail,
  formatDateTime,
  LIFECYCLE_LABELS,
  refreshResource,
  resourceErrorMessage,
  SOURCE_TYPE_LABELS,
  WATCH_STATE_LABELS,
  type ResourceCapabilities,
  type ResourceDetail,
  type ResourceScopeKind,
  type ResourceSummary,
  type ResourceWatchSummary,
} from "@/features/resources";
import ActivityPanel from "./ActivityPanel";
import DeleteDialog from "./DeleteDialog";
import EditMetaDialog from "./EditMetaDialog";
import NodeTree from "./NodeTree";
import PublishDialog from "./PublishDialog";
import ReplaceDialog from "./ReplaceDialog";
import WatchPanel from "./WatchPanel";

export interface ResourceDetailViewProps {
  scope: ResourceScopeKind;
  /** 详情归属展示（私有/共享管理页传各自标题）。 */
  visibilityLabel: string;
  /** 404 回退链接（当前分区列表）。 */
  backToList: string;
  /** 发布成功后跳转（共享详情）。 */
  onPublishedNavigate: (newSharedResourceId: string) => void;
  /** 测试注入：不传时从路由参数 $resourceId 读取。 */
  resourceId?: string;
}

type DetailTab = "overview" | "content" | "watch" | "activity";

const TABS: { key: DetailTab; label: string }[] = [
  { key: "overview", label: "概览" },
  { key: "content", label: "内容" },
  { key: "watch", label: "自动同步" },
  { key: "activity", label: "活动" },
];

export default function ResourceDetailView({
  scope,
  visibilityLabel,
  backToList,
  onPublishedNavigate,
  resourceId: resourceIdProp,
}: ResourceDetailViewProps) {
  const resourceId =
    resourceIdProp ??
    (useParams({ strict: false }) as Record<string, string>)["resourceId"] ??
    "";
  const me = useMe();

  const [detail, setDetail] = useState<ResourceDetail | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(
    null,
  );
  const [tab, setTab] = useState<DetailTab>("overview");
  const [capabilities, setCapabilities] = useState<ResourceCapabilities | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [replaceOpen, setReplaceOpen] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const invalidId = !isProductId(resourceId);

  const canWrite =
    me != null &&
    canPerform(
      me,
      scope === "private"
        ? "resource.user_private.write.self"
        : "resource.account_shared.write.account",
    );
  const canDelete =
    me != null &&
    canPerform(
      me,
      scope === "private"
        ? "resource.user_private.delete.self"
        : "resource.account_shared.delete.account",
    );
  const canReadActivity =
    me != null && canPerform(me, scope === "private" ? "task.read.self" : "task.read.account_shared");
  const canCancelActivity =
    me != null &&
    canPerform(me, scope === "private" ? "task.cancel.self" : "task.cancel.account_shared");
  /** AC⑦：发布仅 Account Admin 自己的私有 Resource（09 §44.1）。 */
  const canPublish =
    scope === "private" &&
    me != null &&
    isAccountAdmin(me.roles) &&
    canPerform(me, "resource.account_shared.write.account");

  const load = useCallback(() => {
    setLoadError(null);
    setDetail(null);
    fetchResourceDetail(scope, resourceId)
      .then(setDetail)
      .catch((error) =>
        setLoadError({
          message: isPlatformError(error)
            ? error.message || "无法加载 Resource 详情"
            : "无法加载 Resource 详情",
          requestId: isPlatformError(error) ? error.requestId : null,
        }),
      );
  }, [scope, resourceId]);

  useEffect(() => {
    if (invalidId) return;
    load();
    fetchCapabilities().then(setCapabilities).catch(() => setCapabilities(null));
  }, [invalidId, load]);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  if (invalidId) {
    return (
      <div className="error-page" role="alert" data-testid="detail-invalid-id">
        <h2>Resource 详情</h2>
        <p className="error-page-code">404</p>
        <p className="error-page-message">地址中的产品 ID 无效（404 语义）。</p>
        <p className="error-page-hint">
          URL 只接受产品 ID（UUID），不接受 Viking URI / Account / User 标识符（AC⑧）。
        </p>
      </div>
    );
  }

  if (loadError) {
    return (
      <section className="admin-page">
        <header className="admin-page-header">
          <h2>Resource 详情</h2>
        </header>
        <div className="admin-load-error" data-testid="detail-error">
          <p>{loadError.message}</p>
          {loadError.requestId ? <code>Request ID: {loadError.requestId}</code> : null}
          <button type="button" onClick={load}>
            重试
          </button>
        </div>
      </section>
    );
  }

  if (!detail) {
    return (
      <section className="admin-page">
        <p className="admin-load-error">加载中…</p>
      </section>
    );
  }

  const ingestRunning =
    detail.current_operation != null &&
    ["pending", "running"].includes(detail.current_operation.status);

  /** AC⑤：同一 Resource 同时只允许一个摄取类 Operation（RESOURCE_BUSY 禁用按钮）。 */
  const ingestDisabled = actionBusy || ingestRunning;

  function handleUpdated(updated: ResourceSummary) {
    setDetail((prev) => (prev ? { ...prev, ...updated } : prev));
    setNotice("已保存。名称修改不影响链接与索引（09 §42.4）。");
  }

  function handleDeleted(noticeText: string) {
    setNotice(noticeText);
    setTab("overview");
    // 09 §41.2：删除中对象从正常列表隐藏；保留页面供回收站入口访问
  }

  async function handleRefresh() {
    if (ingestDisabled) return;
    setActionBusy(true);
    setActionError(null);
    try {
      await refreshResource(scope, resourceId);
      setNotice("已提交刷新，处理期间旧版本保持可读，完成后原子切换（09 §41.2，AC⑤）。");
      await load();
    } catch (error) {
      if (isPlatformError(error) && error.code === "RESOURCE_BUSY") {
        setActionError("该 Resource 正在执行其他处理任务（RESOURCE_BUSY）。");
      } else {
        setActionError(resourceErrorMessage(error, "刷新失败，请稍后重试。"));
      }
      await load();
    } finally {
      setActionBusy(false);
    }
  }

  function handleWatchChanged(watch: ResourceWatchSummary) {
    setDetail((prev) => (prev ? { ...prev, watch } : prev));
    setNotice(
      watch.state === "paused"
        ? "已暂停自动同步（保留周期与配置，不再调度；恢复后重新计算下次执行时间，09 §43.2）。"
        : watch.state === "active"
          ? "自动同步已启用。"
          : "已停止自动同步（不影响 Resource 本身）。",
    );
  }

  function copyProductLink() {
    const url = new URL(globalThis.location.href);
    void navigator.clipboard?.writeText(url.href).catch(() => undefined);
    setCopied(true);
  }

  const publishable =
    canPublish && detail.visibility === "user_private" && detail.lifecycle_status === "active";

  return (
    <section className="admin-page" data-testid="resource-detail">
      <header className="admin-page-header">
        <div>
          <h2>{detail.name || "（未命名）"}</h2>
          <p className="admin-account-context">
            {visibilityLabel} · {SOURCE_TYPE_LABELS[detail.source_type]}
            {detail.source_display ? ` · ${detail.source_display}` : ""} ·{" "}
            <span className={`status-badge ${detail.lifecycle_status}`}>
              {LIFECYCLE_LABELS[detail.lifecycle_status]}
            </span>
            {detail.watch.state !== "not_configured" && detail.source_type !== "upload"
              ? ` · 自动同步：${WATCH_STATE_LABELS[detail.watch.state] ?? detail.watch.state}`
              : ""}
          </p>
          <p className="profile-hint">
            最近成功处理 {formatDateTime(detail.processing.last_succeeded_at)} · 更新时间{" "}
            {formatDateTime(detail.updated_at)}
          </p>
          {detail.tags.length > 0 ? (
            <p>
              {detail.tags.map((tag) => (
                <span key={tag} className="resource-tag">
                  {tag}
                </span>
              ))}
            </p>
          ) : null}
        </div>
        <div className="profile-actions">
          <button type="button" onClick={copyProductLink} title="复制产品 URL（不复制 Viking URI）">
            {copied ? "已复制链接" : "复制链接"}
          </button>
          {canWrite ? (
            <button type="button" onClick={() => setEditOpen(true)}>
              编辑信息
            </button>
          ) : null}
          {canWrite && detail.source_type === "upload" ? (
            <button type="button" onClick={() => setReplaceOpen(true)} disabled={ingestDisabled}>
              {ingestDisabled ? "处理中" : "替换文件"}
            </button>
          ) : null}
          {canWrite && detail.source_type !== "upload" ? (
            <button type="button" onClick={handleRefresh} disabled={ingestDisabled}>
              {ingestDisabled ? "处理中" : "Refresh"}
            </button>
          ) : null}
          {publishable ? (
            <button type="button" onClick={() => setPublishOpen(true)}>
              发布为共享
            </button>
          ) : null}
          {canDelete ? (
            <button type="button" className="link-danger" onClick={() => setDeleteOpen(true)}>
              删除
            </button>
          ) : null}
        </div>
      </header>

      {actionError ? (
        <p className="login-error" data-testid="detail-action-error">
          {actionError}
        </p>
      ) : null}
      {notice ? (
        <p className="profile-success" data-testid="detail-notice">
          {notice}
        </p>
      ) : null}
      {ingestRunning && detail.current_operation ? (
        <p className="profile-hint" data-testid="ingest-banner">
          正在同步（{detail.current_operation.operation_type}），旧版本保持可读；完成后原子切换。
        </p>
      ) : null}

      <div className="import-source-tabs" role="tablist">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            className={tab === item.key ? "tab-active" : undefined}
            onClick={() => setTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "overview" ? (
        <div className="card" data-testid="overview-tab">
          {detail.description ? (
            <p>
              <strong>说明：</strong>
              {detail.description}
            </p>
          ) : (
            <p className="profile-hint">无说明。</p>
          )}
          <dl className="profile-session-card">
            <div className="profile-row">
              <dt>L0 摘要</dt>
              <dd>
                {detail.overview ? (
                  detail.overview
                ) : detail.lifecycle_status === "active" ? (
                  <span className="profile-hint">摘要生成中…</span>
                ) : (
                  <span className="profile-hint">处理中，暂未生成摘要。</span>
                )}
              </dd>
            </div>
            <div className="profile-row">
              <dt>来源</dt>
              <dd>
                {SOURCE_TYPE_LABELS[detail.source_type]}
                {detail.source_display ? ` · ${detail.source_display}` : ""}
              </dd>
            </div>
            <div className="profile-row">
              <dt>内容</dt>
              <dd>
                {detail.content.node_count} 个文件，约{" "}
                {detail.content.size_bytes > 0
                  ? `${(detail.content.size_bytes / 1024).toFixed(1)} KB`
                  : "0 KB"}
              </dd>
            </div>
            <div className="profile-row">
              <dt>处理摘要</dt>
              <dd>
                最近成功 {formatDateTime(detail.processing.last_succeeded_at)}；当前版本{" "}
                {detail.version}
              </dd>
            </div>
          </dl>
          <p className="profile-hint">
            页面不展示底层 URI / 解析模式；内容更新通过替换文件或 Refresh 完成（09 §42.2/§42.3）。
          </p>
        </div>
      ) : null}

      {tab === "content" ? (
        <NodeTree
          key={`${scope}-${resourceId}`}
          scope={scope}
          resourceId={resourceId}
          resourceName={detail.name}
        />
      ) : null}

      {tab === "watch" ? (
        <WatchPanel
          scope={scope}
          resource={detail}
          capabilities={capabilities}
          canWrite={canWrite}
          onChanged={handleWatchChanged}
        />
      ) : null}

      {tab === "activity" ? (
        canReadActivity ? (
          <ActivityPanel
            scope={scope}
            resourceId={resourceId}
            resourceName={detail.name}
            canCancel={canCancelActivity}
            onOperationChanged={() => void load()}
          />
        ) : (
          <p className="profile-hint">无查看处理活动的权限。</p>
        )
      ) : null}

      <Link to={backToList} className="placeholder-back">
        返回列表
      </Link>

      {editOpen ? (
        <EditMetaDialog
          scope={scope}
          resource={detail}
          onClose={() => setEditOpen(false)}
          onReload={async () => {
            const latest = await fetchResourceDetail(scope, resourceId);
            setDetail(latest);
            return latest;
          }}
          onSaved={handleUpdated}
        />
      ) : null}

      {deleteOpen ? (
        <DeleteDialog
          scope={scope}
          resource={detail}
          onClose={() => setDeleteOpen(false)}
          onDeleted={() => setDeleteOpen(false)}
          onDeletedWithNotice={handleDeleted}
        />
      ) : null}

      {replaceOpen ? (
        <ReplaceDialog
          scope={scope}
          resourceId={resourceId}
          capabilities={capabilities}
          onClose={() => setReplaceOpen(false)}
          onReplaced={() => {
            setReplaceOpen(false);
            setNotice("已提交替换，处理期间旧成功版本保持可读，完成后原子切换（09 §43.1，AC⑤）。");
            void load();
          }}
        />
      ) : null}

      {publishOpen ? (
        <PublishDialog
          resource={detail}
          onClose={() => setPublishOpen(false)}
          onPublished={(newSharedResourceId) => {
            setPublishOpen(false);
            onPublishedNavigate(newSharedResourceId);
          }}
        />
      ) : null}
    </section>
  );
}
