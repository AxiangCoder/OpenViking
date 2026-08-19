/**
 * 自动同步（Watch）面板（09 §43.2，P3-E4 AC⑤）。
 *
 * - 仅稳定远程来源（网页/Git）可 Watch；上传文件显示不可用（AC⑤）；
 * - 周期只接受 Capabilities 返回的预设值（60/360/720/1440/10080，09 §43.2）；
 * - 启用/暂停/恢复/立即同步/删除；当前已有摄取类 Operation 时
 *   RESOURCE_BUSY → 按钮禁用（AC⑤）；
 * - 无写权限只读（09 §42.2）；恢复后 Watch 保持 paused 由后端保证，
 *   页面提示需手动重新启用（09 §45.3）。
 */

import { useState } from "react";
import { isPlatformError } from "@/lib/platform-client";
import {
  configureWatch,
  deleteWatch,
  formatDateTime,
  OPERATION_STATUS_LABELS,
  pauseWatch,
  resourceErrorMessage,
  resumeWatch,
  triggerWatch,
  WATCH_STATE_LABELS,
  type ResourceCapabilities,
  type ResourceDetail,
  type ResourceScopeKind,
  type ResourceWatchSummary,
} from "@/features/resources";

export interface WatchPanelProps {
  scope: ResourceScopeKind;
  resource: ResourceDetail;
  capabilities: ResourceCapabilities | null;
  canWrite: boolean;
  onChanged: (watch: ResourceWatchSummary) => void;
}

export default function WatchPanel({
  scope,
  resource,
  capabilities,
  canWrite,
  onChanged,
}: WatchPanelProps) {
  const [interval, setIntervalMinutes] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const watch = resource.watch;
  const isRemote = resource.source_type === "web" || resource.source_type === "git";
  const watchEnabled = capabilities?.watch.enabled ?? false;
  const presets = capabilities?.watch.interval_presets_minutes ?? [];
  const ingestRunning =
    resource.current_operation != null &&
    ["pending", "running"].includes(resource.current_operation.status);

  if (!isRemote) {
    return (
      <div className="card">
        <h3>自动同步</h3>
        <p className="profile-hint">
          上传文件不支持自动同步（09 §40.5）。更新内容请使用「替换文件」。
        </p>
      </div>
    );
  }

  if (!watchEnabled) {
    return (
      <div className="card">
        <h3>自动同步</h3>
        <p className="profile-hint">当前部署未启用自动同步能力（Capabilities 返回）。</p>
      </div>
    );
  }

  async function run(action: () => Promise<ResourceWatchSummary>) {
    if (busy || !canWrite) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      onChanged(await action());
    } catch (actionError) {
      if (isPlatformError(actionError) && actionError.code === "RESOURCE_BUSY") {
        setError("该 Resource 正在执行其他处理任务，请稍后再试（RESOURCE_BUSY）。");
      } else {
        setError(resourceErrorMessage(actionError, "操作失败，请稍后重试。"));
      }
    } finally {
      setBusy(false);
    }
  }

  const buttonsDisabled = busy || ingestRunning || !canWrite;
  const inProgress = watch.state === "active" && ingestRunning;

  return (
    <div className="card" data-testid="watch-panel">
      <h3>自动同步</h3>
      {error ? (
        <p className="login-error" data-testid="watch-error">
          {error}
        </p>
      ) : null}
      {notice ? <p className="profile-success">{notice}</p> : null}
      {inProgress ? (
        <p className="profile-hint">正在同步中，旧版本保持可读；完成后原子切换（09 §41.2）。</p>
      ) : null}
      <dl className="profile-session-card">
        <div className="profile-row">
          <dt>状态</dt>
          <dd>{WATCH_STATE_LABELS[watch.state] ?? watch.state}</dd>
        </div>
        {watch.interval_minutes ? (
          <div className="profile-row">
            <dt>周期</dt>
            <dd>每 {watch.interval_minutes} 分钟</dd>
          </div>
        ) : null}
        <div className="profile-row">
          <dt>上次执行</dt>
          <dd>{formatDateTime(watch.last_run_at)}</dd>
        </div>
        <div className="profile-row">
          <dt>下次执行</dt>
          <dd>{formatDateTime(watch.next_run_at)}</dd>
        </div>
        {watch.last_result ? (
          <div className="profile-row">
            <dt>最近结果</dt>
            <dd>
              {OPERATION_STATUS_LABELS[watch.last_result] ?? watch.last_result}
              {watch.last_error ? (
                <span className="login-error"> · {watch.last_error}</span>
              ) : null}
            </dd>
          </div>
        ) : null}
      </dl>

      {canWrite ? (
        <div className="profile-actions">
          {watch.state === "not_configured" ? (
            <>
              <label>
                周期预设（Capabilities 返回）
                <select
                  value={interval ?? presets[0] ?? 360}
                  onChange={(event) => setIntervalMinutes(Number(event.target.value))}
                  disabled={buttonsDisabled}
                >
                  {presets.map((minutes) => (
                    <option key={minutes} value={minutes}>
                      {minutes < 60 * 24 ? `每 ${minutes} 分钟` : `每 ${minutes / (60 * 24)} 天`}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="primary-button"
                disabled={buttonsDisabled || presets.length === 0}
                onClick={() =>
                  run(() => configureWatch(scope, resource.id, interval ?? presets[0] ?? 360))
                }
              >
                {busy ? "提交中…" : "启用自动同步"}
              </button>
            </>
          ) : (
            <>
              {watch.state === "active" ? (
                <button
                  type="button"
                  disabled={buttonsDisabled}
                  onClick={() => run(() => pauseWatch(scope, resource.id))}
                >
                  暂停
                </button>
              ) : (
                <button
                  type="button"
                  disabled={buttonsDisabled}
                  onClick={() => run(() => resumeWatch(scope, resource.id))}
                >
                  恢复
                </button>
              )}
              <button
                type="button"
                disabled={buttonsDisabled}
                onClick={() =>
                  run(() =>
                    triggerWatch(scope, resource.id).then(() => resource.watch),
                  )
                }
              >
                立即同步
              </button>
              <button
                type="button"
                className="link-danger"
                disabled={busy || ingestRunning}
                onClick={() =>
                  run(async () => {
                    await deleteWatch(scope, resource.id);
                    return { state: "not_configured" };
                  })
                }
              >
                停止自动同步（不删除 Resource）
              </button>
            </>
          )}
        </div>
      ) : (
        <p className="profile-hint">无写权限，自动同步配置只读。</p>
      )}
    </div>
  );
}
