/**
 * 回收站共享组件（P3-E3 开发，/app 与 /admin、/platform 复用，P4-E3 只复用不重写）。
 *
 * - 仅展示服务端标记为可恢复的对象（AC⑧：`restore_allowed` 实时计算）；
 * - 按对象类型分组展示（06 §14.6）；
 * - 恢复按类型携带权限码（`restore_permission` 逐项下发，体验性门禁，非安全边界）；
 * - `RESTORE_WINDOW_EXPIRED` 正确展示：已过恢复截止/已物理清理的对象恢复失败时
 *   提示并刷新列表（AC⑧）。
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { isPlatformError } from "@/lib/platform-client";
import { hasPermission } from "@/lib/permissions";
import { useMe } from "@/features/auth/useAuth";
import {
  formatRestoreDeadline,
  resourceTypeLabel,
  restorableItems,
  type RecycleBinItem,
} from "@/features/recycle-bin/recycle-bin";

export interface RecycleBinPanelProps {
  /** 读取回收站（挂载点差异仅在后端 scope）。 */
  fetchItems: () => Promise<{ items: RecycleBinItem[] }>;
  /** 恢复（按对象类型权限码由服务端校验）。 */
  restoreItem: (jobId: string) => Promise<unknown>;
  /** 页面标题，如「回收站（我的）」。 */
  title: string;
  /** 空状态文案（scope 差异）。 */
  emptyHint?: ReactNode;
}

export default function RecycleBinPanel({
  fetchItems,
  restoreItem,
  title,
  emptyHint,
}: RecycleBinPanelProps) {
  const me = useMe();
  const [items, setItems] = useState<RecycleBinItem[] | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [confirming, setConfirming] = useState<RecycleBinItem | null>(null);
  const [restoring, setRestoring] = useState(false);
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

  // AC⑧：仅展示可恢复对象（服务端按当前角色实时判定）
  const visible = useMemo(() => restorableItems(items ?? []), [items]);

  const groups = useMemo(() => {
    const map = new Map<string, RecycleBinItem[]>();
    for (const item of visible) {
      const list = map.get(item.resource_type) ?? [];
      list.push(item);
      map.set(item.resource_type, list);
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [visible]);

  async function handleRestore(item: RecycleBinItem) {
    if (restoring) return;
    setRestoring(true);
    setActionError(null);
    try {
      await restoreItem(item.id);
      setConfirming(null);
      setReloadTick((t) => t + 1);
    } catch (err) {
      // AC⑧：恢复窗口已过 → RESTORE_WINDOW_EXPIRED，正确展示并刷新（对象已不可恢复）
      if (isPlatformError(err) && err.code === "RESTORE_WINDOW_EXPIRED") {
        setActionError("恢复窗口已过期：该对象已超过 30 天恢复期限或被清理，无法恢复。");
        setConfirming(null);
        setReloadTick((t) => t + 1);
      } else if (isPlatformError(err) && err.code === "ALREADY_RESTORED") {
        setActionError("该对象已恢复。");
        setConfirming(null);
        setReloadTick((t) => t + 1);
      } else {
        setActionError(
          isPlatformError(err) ? err.message || "恢复失败，请稍后重试" : "恢复失败，请稍后重试",
        );
      }
    } finally {
      setRestoring(false);
    }
  }

  return (
    <section className="card" data-testid="recycle-bin-panel">
      <h2 className="page-title">{title}</h2>

      {actionError ? (
        <p className="login-error" role="alert" data-testid="recycle-bin-action-error">
          {actionError}
        </p>
      ) : null}

      {loadState === "loading" ? <p>加载中…</p> : null}
      {loadState === "error" ? (
        <p className="profile-hint">无法加载回收站，请稍后重试。</p>
      ) : null}

      {loadState === "ready" && groups.length === 0 ? (
        <div className="empty-state" data-testid="recycle-bin-empty">
          {emptyHint ?? "当前没有可恢复的对象。删除的对象会进入 30 天回收期，期满后自动清理。"}
        </div>
      ) : null}

      {groups.map(([type, rows]) => (
        <section key={type} className="recycle-group" aria-label={resourceTypeLabel(type)}>
          <h3 className="recycle-group-title">{resourceTypeLabel(type)}</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>删除时间</th>
                <th>恢复截止</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <tr key={item.id} data-testid={`recycle-item-${item.resource_type}`}>
                  <td>{item.target_name || "—"}</td>
                  <td>{new Date(item.deleted_at).toLocaleString()}</td>
                  <td>
                    <span data-testid="restore-deadline">{formatRestoreDeadline(item.purge_after)}</span>
                  </td>
                  <td>
                    {/* 恢复按类型携带权限码（体验性门禁；后端仍按类型分别校验） */}
                    {item.restore_permission && !hasPermission(me?.permissions, item.restore_permission) ? (
                      <span className="profile-hint">无恢复权限</span>
                    ) : (
                      <button
                        type="button"
                        className="primary-button"
                        disabled={restoring}
                        onClick={() => {
                          setActionError(null);
                          setConfirming(item);
                        }}
                        data-testid={`restore-button-${item.resource_type}-${item.resource_id}`}
                      >
                        恢复
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}

      {confirming ? (
        <div className="confirm-dialog" role="dialog" aria-label="恢复确认" data-testid="restore-dialog">
          <h3>确认恢复</h3>
          <ul className="admin-dialog-list">
            <li>对象类型：{resourceTypeLabel(confirming.resource_type)}</li>
            <li>名称：{confirming.target_name || "—"}</li>
            <li>
              恢复截止：<strong data-testid="restore-dialog-deadline">{formatRestoreDeadline(confirming.purge_after)}</strong>
            </li>
            {confirming.resource_type === "session" ? (
              <li>恢复后重新显示历史；不会回滚该 Session 过去已产生的 Memory 变更（11 §72）。</li>
            ) : null}
          </ul>
          <div className="row-actions">
            <button type="button" onClick={() => setConfirming(null)} disabled={restoring}>
              取消
            </button>
            <button
              type="button"
              className="primary-button"
              onClick={() => void handleRestore(confirming)}
              disabled={restoring}
              data-testid="restore-confirm"
            >
              {restoring ? "恢复中…" : "确认恢复"}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
