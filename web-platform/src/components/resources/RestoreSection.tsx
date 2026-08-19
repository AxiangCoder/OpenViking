/**
 * 我的私有 Resource 回收站区（09 §45.3，P3-E4 AC⑥）。
 *
 * - 仅展示可恢复对象（当前 User 自己的私有 Resource）；恢复按对象类型
 *   携带权限码（05 §12.5，restore_permission 由服务端返回）；
 * - RESTORE_WINDOW_EXPIRED 正确展示（14 号计划 §98.3 AC⑧）；
 * - 恢复后 Watch 保持 paused，必须手工重新启用（09 §45.3，AC⑥）。
 */

import { useCallback, useEffect, useState } from "react";
import { isPlatformError } from "@/lib/platform-client";
import {
  formatDateTime,
  listRecycleBin,
  resourceErrorMessage,
  restoreResource,
  type RecycleBinRow,
} from "@/features/resources";

export interface RestoreSectionProps {
  /** 恢复成功后的刷新回调（刷新主列表）。 */
  onRestored?: () => void;
}

export default function RestoreSection({ onRestored }: RestoreSectionProps) {
  const [rows, setRows] = useState<RecycleBinRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback((keepError = false) => {
    if (!keepError) setError(null);
    listRecycleBin()
      .then((page) => setRows(page.items.filter((item) => item.resource_type === "resource")))
      .catch((loadError) =>
        setError(resourceErrorMessage(loadError, "无法加载回收站，请稍后重试。")),
      );
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function restore(row: RecycleBinRow) {
    if (restoringId) return;
    setRestoringId(row.id);
    setError(null);
    setNotice(null);
    try {
      await restoreResource(row.id);
      setNotice(
        `「${row.target_name}」已恢复。如之前启用了自动同步，恢复后保持暂停，需手动重新启用（09 §45.3，AC⑥）。`,
      );
      load();
      onRestored?.();
    } catch (restoreError) {
      if (isPlatformError(restoreError) && restoreError.code === "RESTORE_WINDOW_EXPIRED") {
        setError("恢复窗口（30 天）已过期，该对象无法恢复。");
      } else {
        setError(resourceErrorMessage(restoreError, "恢复失败，请稍后重试。"));
      }
      load(true);
    } finally {
      setRestoringId(null);
    }
  }

  if (rows === null && !error) return null;

  return (
    <section className="card" data-testid="restore-section">
      <h3>回收站（我的私有 Resource，30 天可恢复）</h3>
      {error ? (
        <p className="login-error" data-testid="restore-error">
          {error}
        </p>
      ) : null}
      {notice ? <p className="profile-success" data-testid="restore-notice">{notice}</p> : null}
      {rows !== null && rows.length === 0 ? (
        <p className="profile-hint">回收站为空（删除后 30 天内可从这里恢复）。</p>
      ) : null}
      {rows !== null && rows.length > 0 ? (
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
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.target_name}</td>
                <td>{formatDateTime(row.deleted_at)}</td>
                <td>{formatDateTime(row.purge_after)}</td>
                <td>
                  {row.restore_allowed ? (
                    <button
                      type="button"
                      onClick={() => restore(row)}
                      disabled={restoringId !== null}
                    >
                      {restoringId === row.id ? "恢复中…" : "恢复"}
                    </button>
                  ) : (
                    <span className="resource-muted">不可恢复</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}
