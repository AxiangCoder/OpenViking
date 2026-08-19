/**
 * Account 共享 Resource 管理组件（06 §13.3，14 号计划 §98.4）。
 *
 * 本组件由 P3-E4 开发，同时服务 `/app/resources/shared` 与
 * `/admin/shared-resources` 两个挂载点；P4-E3 只复用不重写。
 *
 * - 普通 User：只读列表 + 「共享内容由 Account 管理员维护」提示，无管理
 *   按钮与新增入口（AC①，09 §39.4）；
 * - Account Admin：与 /admin 相同管理能力（新增/删除），行内删除走完整
 *   删除预览弹窗（09 §45.1，AC⑥）；其余编辑/Refresh/Watch 在详情页；
 * - 新增入口固定归属：弹窗显示「保存到：{Account} 共享 Resource」，无
 *   归属下拉（AC①，09 §40.1）。
 */

import { useState } from "react";
import { useMe } from "@/features/auth/useAuth";
import { canManageSharedContent } from "@/lib/permissions";
import { recordPartition } from "@/features/resources";
import ResourceList, { type ResourceListRowAction } from "./ResourceList";
import ImportDialog from "./ImportDialog";
import DeleteDialog from "./DeleteDialog";
import type { ResourceSummary } from "@/features/resources";

export interface SharedResourcesManagerProps {
  /** /app 挂载传「Account 共享 Resource」、/admin 挂载传「共享 Resource 管理」。 */
  title: string;
  /** 列表副标题（固定 Account 上下文，无切换入口）。 */
  subtitle: string;
  /** 跳转详情 base：/app 挂载与 /admin 挂载都指向共享详情。 */
  detailBase: "shared";
}

export default function SharedResourcesManager({
  title,
  subtitle,
  detailBase,
}: SharedResourcesManagerProps) {
  const me = useMe();
  const [importOpen, setImportOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ResourceSummary | null>(null);
  const [deletedNotice, setDeletedNotice] = useState<string | null>(null);

  const isAdmin = canManageSharedContent(me);
  const accountLabel = me?.account?.name ?? me?.account?.code ?? "当前 Account";

  const rowActions = (row: ResourceSummary): ResourceListRowAction[] => {
    if (!isAdmin) return [];
    return [{ label: "删除", danger: true, onRun: () => setDeleteTarget(row) }];
  };

  return (
    <div data-testid="shared-resources-manager">
      {deletedNotice ? (
        <p className="profile-success" data-testid="shared-notice">
          {deletedNotice}
        </p>
      ) : null}
      <ResourceList
        scope="shared"
        detailBase={detailBase}
        title={title}
        subtitle={subtitle}
        actions={
          isAdmin ? (
            <button type="button" className="primary-button" onClick={() => setImportOpen(true)}>
              新增 Resource
            </button>
          ) : (
            <span className="profile-hint" data-testid="shared-managed-by-admin">
              共享内容由 Account 管理员维护
            </span>
          )
        }
        rowActions={rowActions}
        emptyHint="暂无 Account 共享资料"
        emptyDescription={
          isAdmin ? undefined : "共享内容由 Account 管理员维护；普通 User 共享页只读。"
        }
        onLoaded={() => recordPartition("shared")}
      />
      {importOpen ? (
        <ImportDialog
          scope="shared"
          targetLabel={`${accountLabel} 共享 Resource`}
          onClose={() => setImportOpen(false)}
          onImported={() => setImportOpen(false)}
        />
      ) : null}
      {deleteTarget ? (
        <DeleteDialog
          scope="shared"
          resource={deleteTarget}
          onClose={() => setDeleteTarget(null)}
          onDeleted={() => setDeleteTarget(null)}
          onDeletedWithNotice={(notice) => {
            setDeletedNotice(notice);
            setDeleteTarget(null);
          }}
        />
      ) : null}
    </div>
  );
}
