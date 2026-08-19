/**
 * Resource 详情「活动」面板（09 §43.3，P3-E4）。
 *
 * - 只显示与当前 Resource 绑定的 Operation；不返回堆栈/Worker 路径/
 *   原始 Task ID（09 §43.3，AC⑧）；
 * - 仅 pending/running 且可取消时显示取消；取消进入 cancelling
 *   （09 §43.3：系统已接受并最终停止，不保证立即终止）；
 * - 取消前展示目标 Resource、任务类型、当前状态与影响（06 §13.7）。
 */

import { useEffect, useState } from "react";
import {
  cancelResourceOperation,
  formatDateTime,
  initiatedByLabel,
  listResourceOperations,
  OPERATION_STATUS_LABELS,
  operationTypeLabel,
  resourceErrorMessage,
  STAGE_LABELS,
  type OperationItem,
  type ResourceScopeKind,
} from "@/features/resources";

export interface ActivityPanelProps {
  scope: ResourceScopeKind;
  resourceId: string;
  resourceName: string;
  canCancel: boolean;
  onOperationChanged?: () => void;
}

export default function ActivityPanel({
  scope,
  resourceId,
  resourceName,
  canCancel,
  onOperationChanged,
}: ActivityPanelProps) {
  const [items, setItems] = useState<OperationItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<OperationItem | null>(null);
  const [cancelling, setCancelling] = useState(false);

  const load = () => {
    setItems(null);
    setError(null);
    listResourceOperations(scope, resourceId)
      .then((page) => setItems(page.items))
      .catch((loadError) =>
        setError(resourceErrorMessage(loadError, "无法加载处理活动，请稍后重试。")),
      );
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, resourceId]);

  async function confirmCancel() {
    if (!confirming || cancelling) return;
    setCancelling(true);
    try {
      await cancelResourceOperation(scope, resourceId, confirming.id);
      setConfirming(null);
      load();
      onOperationChanged?.();
    } catch (cancelError) {
      setError(resourceErrorMessage(cancelError, "取消失败，请稍后重试。"));
      setConfirming(null);
      load();
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div data-testid="activity-panel">
      {error ? (
        <p className="login-error" data-testid="activity-error">
          {error}
        </p>
      ) : null}
      {items === null && !error ? <p className="profile-hint">加载中…</p> : null}
      {items !== null && items.length === 0 ? (
        <p className="empty-state">暂无处理活动（导入、刷新、自动同步等记录会显示在这里）。</p>
      ) : null}
      {items !== null && items.length > 0 ? (
        <div className="card">
          <table className="data-table">
            <thead>
              <tr>
                <th>类型</th>
                <th>状态</th>
                <th>发起</th>
                <th>发起时间</th>
                <th>完成时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((op) => (
                <tr key={op.id}>
                  <td>{operationTypeLabel(op.operation_type)}</td>
                  <td>
                    {OPERATION_STATUS_LABELS[op.status] ?? op.status}
                    {op.status === "pending" || op.status === "running" ? (
                      <span className="resource-op-line">
                        {op.stage ? STAGE_LABELS[op.stage] ?? op.stage : ""}
                      </span>
                    ) : null}
                    {op.error ? (
                      <span className="login-error">
                        {" "}
                        {op.error.summary ?? op.error.code}
                        {op.error.retryable ? "（可重试）" : ""}
                      </span>
                    ) : null}
                  </td>
                  <td>{initiatedByLabel(op.initiated_by)}</td>
                  <td>{formatDateTime(op.created_at)}</td>
                  <td>{formatDateTime(op.completed_at)}</td>
                  <td>
                    {canCancel && op.cancellable ? (
                      <button type="button" className="link-danger" onClick={() => setConfirming(op)}>
                        取消
                      </button>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {confirming ? (
        <div className="confirm-dialog-backdrop" role="dialog" aria-modal="true" aria-label="取消任务">
          <div className="confirm-dialog">
            <h3>取消处理任务</h3>
            <dl className="deletion-preview-list">
              <div>
                <dt>目标 Resource</dt>
                <dd>{resourceName}</dd>
              </div>
              <div>
                <dt>任务类型</dt>
                <dd>{operationTypeLabel(confirming.operation_type)}</dd>
              </div>
              <div>
                <dt>当前状态</dt>
                <dd>{OPERATION_STATUS_LABELS[confirming.status] ?? confirming.status}</dd>
              </div>
              <div>
                <dt>影响</dt>
                <dd>取消成功后该任务停止；已有可用版本不受影响（09 §43.3）。</dd>
              </div>
            </dl>
            <div className="confirm-dialog-actions">
              <button
                type="button"
                className="danger-button"
                onClick={confirmCancel}
                disabled={cancelling}
              >
                {cancelling ? "取消中…" : "确认取消"}
              </button>
              <button type="button" onClick={() => setConfirming(null)} disabled={cancelling}>
                关闭
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
