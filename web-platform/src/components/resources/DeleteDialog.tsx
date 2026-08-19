/**
 * 删除确认弹窗（09 §45.1，P3-E4 AC⑥）。
 *
 * - 先请求 deletion-preview，展示：名称与范围、内部文件/节点数量与估算大小、
 *   Active Watch 是否被暂停、在途任务是否请求取消、30 天恢复截止时间、
 *   共享 Resource 的成员影响提示（09 §45.1）；
 * - 只确认/取消，不重输密码（09 §45.1）；
 * - 确认调用 DELETE（If-Match 乐观锁，09 §45.4）；删除后对象从正常列表
 *   立即隐藏，仅通过回收站入口访问（09 §41.2）。
 */

import { useEffect, useState } from "react";
import { isPlatformError } from "@/lib/platform-client";
import {
  deleteResource,
  fetchDeletionPreview,
  formatBytes,
  formatDateTime,
  resourceErrorMessage,
  type ResourceDeletionPreview,
  type ResourceScopeKind,
  type ResourceSummary,
} from "@/features/resources";

export interface DeleteDialogProps {
  scope: ResourceScopeKind;
  resource: ResourceSummary;
  onClose: () => void;
  onDeleted: () => void;
  /** 删除进入回收期后的提示（Watch 保持暂停，09 §45.3）。 */
  onDeletedWithNotice?: (notice: string) => void;
}

export default function DeleteDialog({
  scope,
  resource,
  onClose,
  onDeleted,
  onDeletedWithNotice,
}: DeleteDialogProps) {
  const [preview, setPreview] = useState<ResourceDeletionPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleted, setDeleted] = useState(false);

  useEffect(() => {
    fetchDeletionPreview(scope, resource.id)
      .then(setPreview)
      .catch((error) =>
        setPreviewError(resourceErrorMessage(error, "无法获取删除影响范围，请稍后重试。")),
      );
  }, [scope, resource.id]);

  async function confirmDelete() {
    if (deleting || !preview) return;
    setDeleting(true);
    try {
      const result = await deleteResource(scope, resource.id, resource.version);
      setDeleted(true);
      onDeleted();
      onDeletedWithNotice?.(
        `已进入 30 天回收期（恢复截止 ${formatDateTime(result.restore_until)}）。如之前启用了自动同步，恢复后保持暂停，需手动重新启用（09 §45.3）。`,
      );
    } catch (error) {
      if (isPlatformError(error) && error.code === "RESOURCE_VERSION_CONFLICT") {
        setPreviewError(resourceErrorMessage(error, "版本冲突，请关闭弹窗后重试。"));
      } else {
        setPreviewError(resourceErrorMessage(error, "删除失败，请稍后重试。"));
      }
      setDeleting(false);
    }
  }

  const isShared = resource.visibility === "account_shared";

  return (
    <div className="confirm-dialog-backdrop" role="dialog" aria-modal="true" aria-label="删除 Resource">
      <div className="confirm-dialog">
        <h3>删除 Resource</h3>
        {previewError ? (
          <p className="login-error" data-testid="delete-error">
            {previewError}
          </p>
        ) : null}
        {preview ? (
          <dl className="deletion-preview-list" data-testid="delete-preview">
            <div>
              <dt>名称</dt>
              <dd>{preview.name || "（未命名）"}</dd>
            </div>
            <div>
              <dt>范围</dt>
              <dd>{isShared ? "Account 共享 Resource" : "我的私有 Resource"}</dd>
            </div>
            <div>
              <dt>内部内容</dt>
              <dd>
                {preview.content.node_count} 个文件，约 {formatBytes(preview.content.size_bytes)}
              </dd>
            </div>
            <div>
              <dt>自动同步</dt>
              <dd>
                {preview.watch.configured
                  ? preview.watch.will_be_paused
                    ? "运行中，删除后将立即暂停（不再调度新同步）"
                    : "已配置（暂停状态）"
                  : "未配置"}
              </dd>
            </div>
            <div>
              <dt>在途处理任务</dt>
              <dd>
                {preview.inflight_cancelled.length > 0
                  ? `${preview.inflight_cancelled.length} 个任务将被请求取消`
                  : "无"}
              </dd>
            </div>
            <div>
              <dt>恢复截止</dt>
              <dd>{formatDateTime(preview.restore_until)}（30 天回收期）</dd>
            </div>
            {preview.shared_impact_note ? (
              <div>
                <dt>共享影响</dt>
                <dd>{preview.shared_impact_note}</dd>
              </div>
            ) : null}
          </dl>
        ) : previewError ? null : (
          <p className="profile-hint">正在获取删除影响范围…</p>
        )}
        {deleted ? (
          <p className="profile-success" data-testid="delete-done">
            已进入删除流程，将从列表与检索中立即移除（09 §45.2）。
          </p>
        ) : null}
        <div className="confirm-dialog-actions">
          <button
            type="button"
            className="danger-button"
            onClick={confirmDelete}
            disabled={deleting || !preview || deleted}
          >
            {deleting ? "删除中…" : "确认删除"}
          </button>
          <button type="button" onClick={onClose} disabled={deleting}>
            {deleted ? "关闭" : "取消"}
          </button>
        </div>
      </div>
    </div>
  );
}
