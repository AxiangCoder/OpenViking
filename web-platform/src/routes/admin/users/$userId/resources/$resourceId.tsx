/**
 * /admin/users/{userId}/resources/{resourceId} 成员私有 Resource 只读预览
 * （13 §84.2、05 §12.6，14 号计划 §98.7，P4-E2 AC②⑥）。
 *
 * - 只读：概览 + 内容统计 + 处理状态 + 自动同步状态摘要；
 *   无编辑信息/替换/Refresh/Watch/发布/删除/下载/导出按钮（AC②）；
 * - 成员端点不提供节点树与下载（05 §12.6：只读预览，不提供下载/导出）；
 * - 跳转参数契约：URL 只携带产品 ID（lib/links.ts，非法 ID 404 语义）；
 * - 加载失败保留页面框架，展示 Request ID 与重试（13 §84.3）。
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { useMe } from "@/features/auth/useAuth";
import { isProductId } from "@/lib/links";
import SubjectDataBanner from "@/components/subject/SubjectDataBanner";
import { formatIsoDateTime, listAdminUsers, type AdminUserRecord } from "@/features/iam/admin-users";
import {
  fetchMemberResource,
} from "@/features/iam/member-data";
import {
  LIFECYCLE_LABELS,
  SOURCE_TYPE_LABELS,
  WATCH_STATE_LABELS,
  formatBytes,
  type ResourceDetail,
} from "@/features/resources";

export default function AdminUserMemberResourcePage() {
  const params = useParams({ strict: false }) as Record<string, string>;
  const userId = params["userId"] ?? "";
  const resourceId = params["resourceId"] ?? "";
  const me = useMe();
  const accountLabel = me?.account?.name ?? me?.account?.code ?? me?.account?.id ?? "—";
  const actorLabel = me?.user.display_name ?? me?.user.email ?? me?.user.id ?? "当前管理员";

  const [subject, setSubject] = useState<AdminUserRecord | null | undefined>(undefined);
  const [detail, setDetail] = useState<ResourceDetail | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(null);

  const load = useCallback(() => {
    setLoadError(null);
    setDetail(null);
    listAdminUsers()
      .then((page) => {
        setSubject(page.items.find((u) => u.id === userId) ?? null);
      })
      .catch(() => setSubject(null));
    // 非法产品 ID 直接 404 语义，不发起后端详情请求（AC⑥）
    if (!isProductId(resourceId)) return;
    fetchMemberResource(userId, resourceId)
      .then(setDetail)
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "无法加载 Resource" : "无法加载 Resource",
          requestId: isPlatformError(error) ? error.requestId : null,
        });
      });
  }, [userId, resourceId]);

  useEffect(() => {
    load();
  }, [load]);

  const invalidId = !isProductId(resourceId);

  const subjectLabel = subject
    ? `${subject.display_name ?? subject.username}（${subject.email}）`
    : `用户 ${userId}`;

  if (invalidId) {
    return (
      <div className="card" data-testid="member-resource-invalid-id">
        <h2>未找到该 Resource</h2>
        <p className="profile-hint">
          地址中的产品 ID 无效（404 语义）。URL 只接受产品 ID，不接受 Viking URI / Account / User 标识符（AC⑥）。
        </p>
        <Link to={`/admin/users/${userId}/data`} className="placeholder-back">
          ← 返回成员数据
        </Link>
      </div>
    );
  }

  return (
    <div className="admin-page" data-testid="member-resource-page">
      <SubjectDataBanner actorLabel={actorLabel} subjectLabel={subjectLabel} accountLabel={accountLabel} />
      <div className="profile-actions">
        <Link to={`/admin/users/${userId}/data`} className="placeholder-back" data-testid="member-resource-back">
          ← 返回成员数据
        </Link>
      </div>

      {loadError ? (
        <div className="admin-load-error" role="alert" data-testid="member-resource-load-error">
          <p className="login-error">{loadError.message}</p>
          {loadError.requestId ? (
            <p className="error-page-request-id">
              请求 ID：<code>{loadError.requestId}</code>
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="button" onClick={load} data-testid="member-resource-retry">
              重试
            </button>
          </div>
        </div>
      ) : null}

      {detail == null && !loadError ? <p className="profile-hint">加载中…</p> : null}

      {detail != null ? (
        <section className="card" data-testid="member-resource-detail">
          <div className="admin-page-header">
            <h2 data-testid="member-resource-name">{detail.name}</h2>
            <span className="status-badge">
              {LIFECYCLE_LABELS[detail.lifecycle_status] ?? detail.lifecycle_status}
            </span>
          </div>
          <dl className="profile-row">
            <dt>描述</dt>
            <dd>{detail.description ?? "—"}</dd>
            <dt>来源</dt>
            <dd>{SOURCE_TYPE_LABELS[detail.source_type] ?? detail.source_type} · {detail.source_display ?? "—"}</dd>
            <dt>标签</dt>
            <dd>{detail.tags.length > 0 ? detail.tags.join("、") : "—"}</dd>
            <dt>内容</dt>
            <dd data-testid="member-resource-content">
              {detail.content.node_count} 个节点 · {formatBytes(detail.content.size_bytes)}
            </dd>
            <dt>最近成功处理</dt>
            <dd>{formatIsoDateTime(detail.processing.last_succeeded_at)}</dd>
            <dt>自动同步</dt>
            <dd data-testid="member-resource-watch">
              {WATCH_STATE_LABELS[detail.watch.state] ?? detail.watch.state}
              {detail.watch.interval_minutes
                ? ` · 每 ${detail.watch.interval_minutes} 分钟`
                : ""}
            </dd>
            <dt>创建时间</dt>
            <dd>{formatIsoDateTime(detail.created_at)}</dd>
            <dt>更新时间</dt>
            <dd>{formatIsoDateTime(detail.updated_at)}</dd>
          </dl>
          {detail.overview ? (
            <div className="card" data-testid="member-resource-overview">
              <h3>概览</h3>
              <pre className="skill-markdown">{detail.overview}</pre>
            </div>
          ) : null}
          <p className="profile-hint" data-testid="member-resource-readonly-note">
            成员 Resource 只读预览：不提供修改、替换、Refresh、Watch、发布、删除、下载或导出（05 §12.6）。
          </p>
        </section>
      ) : null}
    </div>
  );
}
