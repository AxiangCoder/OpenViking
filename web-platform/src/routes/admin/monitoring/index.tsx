/**
 * /admin/monitoring 业务健康摘要页（13 §87.4，05 §12.6，14 号计划 §98.8，P4-E3 AC④）。
 *
 * - 数据契约：`GET /admin/monitoring`（`monitoring.read`），仅当前 Account 业务摘要；
 * - 展示：共享对象内容计数、处理任务与失败摘要、待处理上传、回收站待清理数量；
 * - 不展示：Queue、锁、模型、VectorDB、文件系统与原始请求日志（08 §28.4，AC④）；
 * - 加载失败保留页面框架，展示 Request ID 与重试（13 §84.3）。
 */

import { useCallback, useEffect, useState } from "react";
import { isPlatformError } from "@/lib/platform-client";
import { useMe } from "@/features/auth/useAuth";
import { fetchAdminMonitoring, type MonitoringResult } from "@/features/iam/admin-governance";

export default function AdminMonitoringPage() {
  const me = useMe();
  const accountLabel = me?.account?.name ?? me?.account?.code ?? me?.account?.id ?? "—";
  const [data, setData] = useState<MonitoringResult | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(
    null,
  );

  const load = useCallback(() => {
    setLoadError(null);
    setData(null);
    fetchAdminMonitoring()
      .then(setData)
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "无法加载监控摘要" : "无法加载监控摘要",
          requestId: isPlatformError(error) ? error.requestId : null,
        });
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const summary = data?.summary;

  return (
    <div className="admin-page" data-testid="admin-monitoring-page">
      <div className="admin-page-header">
        <div>
          <h2>监控（业务摘要）</h2>
          <p className="admin-account-context" data-testid="admin-monitoring-account">
            仅展示当前登录 Account（<strong>{accountLabel}</strong>）的业务健康摘要（05 §12.6）
          </p>
        </div>
      </div>

      {loadError ? (
        <div className="admin-load-error" role="alert" data-testid="admin-monitoring-load-error">
          <p className="login-error">{loadError.message}</p>
          {loadError.requestId ? (
            <p className="error-page-request-id">
              请求 ID：<code>{loadError.requestId}</code>
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="button" onClick={load} data-testid="admin-monitoring-retry">
              重试
            </button>
          </div>
        </div>
      ) : null}

      {data == null && !loadError ? (
        <p className="profile-hint" data-testid="admin-monitoring-loading">
          加载中…
        </p>
      ) : null}

      {data != null && summary ? (
        <section className="card" data-testid="admin-monitoring-summary">
          <div className="home-count-grid">
            <div className="card" data-testid="monitoring-content-refs">
              <h4>共享内容</h4>
              <p className="monitoring-big">
                {(summary.content_refs_active_by_type.resource ?? 0) +
                  (summary.content_refs_active_by_type.skill ?? 0)}
              </p>
              <p className="profile-hint">
                Resource {summary.content_refs_active_by_type.resource ?? 0} ｜ Skill{" "}
                {summary.content_refs_active_by_type.skill ?? 0}
              </p>
            </div>
            <div className="card" data-testid="monitoring-open-operations">
              <h4>处理中任务</h4>
              <p className="monitoring-big">{summary.open_operations}</p>
              <p className="profile-hint">共享对象导入/刷新等异步任务（含失败项）</p>
            </div>
            <div className="card" data-testid="monitoring-pending-uploads">
              <h4>待处理上传</h4>
              <p className="monitoring-big">{summary.pending_uploads}</p>
              <p className="profile-hint">已上传未消费的一次性文件</p>
            </div>
            <div className="card" data-testid="monitoring-deletion-jobs">
              <h4>回收站待清理</h4>
              <p className="monitoring-big">{summary.deletion_jobs_in_recycle}</p>
              <p className="profile-hint">软删除对象，30 天后由系统物理清理（06 §14.6）</p>
            </div>
            <div className="card" data-testid="monitoring-users">
              <h4>成员</h4>
              <p className="monitoring-big">
                {summary.active_users}/{summary.users}
              </p>
              <p className="profile-hint">正常 / 全部（含删除期）</p>
            </div>
          </div>
          <p className="profile-hint" data-testid="monitoring-scope-note">
            生成时间：{new Date(data.generated_at).toLocaleString()} ｜ 不展示 Queue、锁、模型、VectorDB、文件系统与原始请求日志等底层状态（08 §28.4，AC④）
          </p>
        </section>
      ) : null}
    </div>
  );
}
