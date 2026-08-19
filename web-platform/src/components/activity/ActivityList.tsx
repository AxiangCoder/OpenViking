/**
 * Activity 共享组件（P3-E3 开发，/app/activity 与 /admin/activity、/platform/activity
 * 复用，P4-E3 只复用不重写）。
 *
 * - 仅展示脱敏 Operation 摘要（09 §43.3：产品 ID、类型、状态、阶段、发起方式、
 *   时间、可取消标志、脱敏错误），无原始 Task ID/堆栈/Worker 路径（AC⑩）；
 * - 仅 `cancellable` 项显示取消按钮（pending/running 且底层支持协作取消）；
 * - 取消弹窗展示目标对象/任务类型/当前状态/影响（06 §13.7）；
 * - 取消成功后状态进入 `cancelling`（09 §43.3：系统已接受并最终停止）。
 */

import { useEffect, useState } from "react";
import { isPlatformError } from "@/lib/platform-client";
import {
  cancelImpactText,
  operationStatusLabel,
  operationTypeLabel,
  type ActivityItem,
} from "@/features/activity/activity";

export interface ActivityListProps {
  /** 读取活动列表（scope 差异仅在后端）。 */
  fetchItems: () => Promise<{ items: ActivityItem[] }>;
  /** 取消任务（挂载点差异仅在后端 scope）。 */
  cancelItem: (operationId: string) => Promise<{ status: string }>;
}

export default function ActivityList({ fetchItems, cancelItem }: ActivityListProps) {
  const [items, setItems] = useState<ActivityItem[] | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [confirming, setConfirming] = useState<ActivityItem | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoadState("loading");
    fetchItems()
      .then((result) => {
        if (cancelled) return;
        setItems(result.items);
        setLoadState("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setLoadState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [fetchItems, reloadTick]);

  async function handleCancel(item: ActivityItem) {
    if (cancelling) return;
    setCancelling(true);
    setActionError(null);
    try {
      const result = await cancelItem(item.id);
      // AC⑩：取消后状态进入 cancelling（系统已接受并最终停止，非立即终止）
      setItems((current) =>
        (current ?? []).map((op) =>
          op.id === item.id ? { ...op, status: (result.status as ActivityItem["status"]) ?? "cancelling", cancellable: false } : op,
        ),
      );
      setConfirming(null);
    } catch (err) {
      setActionError(
        isPlatformError(err) && err.code === "RESOURCE_OPERATION_NOT_CANCELLABLE"
          ? "该任务当前不可取消（可能已结束或状态已变化）。"
          : isPlatformError(err)
            ? err.message || "取消失败，请稍后重试"
            : "取消失败，请稍后重试",
      );
      setConfirming(null);
      setReloadTick((t) => t + 1);
    } finally {
      setCancelling(false);
    }
  }

  return (
    <section className="card" data-testid="activity-list">
      {actionError ? (
        <p className="login-error" role="alert" data-testid="activity-action-error">
          {actionError}
        </p>
      ) : null}

      {loadState === "loading" ? <p>加载中…</p> : null}
      {loadState === "error" ? <p className="profile-hint">无法加载任务列表，请稍后重试。</p> : null}

      {loadState === "ready" && items?.length === 0 ? (
        <div className="empty-state" data-testid="activity-empty">
          当前没有处理任务。导入、刷新与自动同步等异步任务会显示在这里。
        </div>
      ) : null}

      {loadState === "ready" && items ? (
        <table className="data-table" data-testid="activity-table">
          <thead>
            <tr>
              <th>任务</th>
              <th>状态</th>
              <th>发起方式</th>
              <th>开始时间</th>
              <th>结束时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((op) => (
              <tr key={op.id} data-testid={`activity-item-${op.id}`}>
                <td>
                  <strong>{operationTypeLabel(op.operation_type)}</strong>
                  {op.stage ? (
                    <span className="profile-hint"> · 阶段：{op.stage}</span>
                  ) : null}
                  {op.error ? (
                    <p className="profile-hint" data-testid="activity-error-summary">
                      {op.error.summary || op.error.code || "处理失败"}
                    </p>
                  ) : null}
                </td>
                <td>
                  <span
                    className={`status-badge ${op.status === "failed" || op.status === "cancelled" ? "failed" : ""} ${op.status === "succeeded" ? "active" : ""}`}
                    data-testid="activity-status"
                  >
                    {operationStatusLabel(op.status)}
                  </span>
                </td>
                <td>{op.initiated_by === "user" ? "页面/用户" : "系统/计划"}</td>
                <td>{op.created_at ? new Date(op.created_at).toLocaleString() : "—"}</td>
                <td>{op.completed_at ? new Date(op.completed_at).toLocaleString() : "—"}</td>
                <td>
                  {op.cancellable ? (
                    <button
                      type="button"
                      className="danger-button"
                      onClick={() => {
                        setActionError(null);
                        setConfirming(op);
                      }}
                      data-testid={`cancel-button-${op.id}`}
                    >
                      取消
                    </button>
                  ) : (
                    <span className="profile-hint">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {confirming ? (
        <div className="confirm-dialog" role="dialog" aria-label="取消任务确认" data-testid="cancel-dialog">
          <h3>确认取消任务</h3>
          <ul className="admin-dialog-list">
            <li>
              任务类型：<strong>{operationTypeLabel(confirming.operation_type)}</strong>
            </li>
            <li>
              当前状态：<strong>{operationStatusLabel(confirming.status)}</strong>
            </li>
            <li>
              影响：{cancelImpactText(confirming.operation_type)}
              {confirming.operation_type.startsWith("resource_") ? (
                <span className="profile-hint">（针对目标 Resource 对象）</span>
              ) : null}
            </li>
          </ul>
          <div className="row-actions">
            <button type="button" onClick={() => setConfirming(null)} disabled={cancelling}>
              取消
            </button>
            <button
              type="button"
              className="danger-button"
              onClick={() => void handleCancel(confirming)}
              disabled={cancelling}
              data-testid="cancel-confirm"
            >
              {cancelling ? "提交中…" : "确认取消"}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
