/**
 * /app/resources/private 我的 Resource（09 §39/§40，14 号计划 §98.4）。
 *
 * - 私有列表：服务端筛选/Cursor 分页 + 客户端排序；新增入口固定私有归属
 *   （AC①：「保存到：我的 Resource」无归属下拉）；
 * - 普通 User 可见私有占位行（provisioning/failed，09 §39.2 服务端返回）；
 * - 回收站区：自己的私有 Resource 30 天恢复，恢复后 Watch 保持 paused（AC⑥）；
 * - 页面/请求无 URI、原始 Task ID、宿主机路径（AC⑧）。
 */

import { useState } from "react";
import { useMe } from "@/features/auth/useAuth";
import { canPerform } from "@/lib/permissions";
import { recordPartition } from "@/features/resources";
import ResourceList, { type ResourceListRowAction } from "@/components/resources/ResourceList";
import ImportDialog from "@/components/resources/ImportDialog";
import DeleteDialog from "@/components/resources/DeleteDialog";
import RestoreSection from "@/components/resources/RestoreSection";
import type { ResourceSummary } from "@/features/resources";

export default function ResourcesPrivatePage() {
  const me = useMe();
  const [importOpen, setImportOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ResourceSummary | null>(null);
  const [deletedNotice, setDeletedNotice] = useState<string | null>(null);
  const [listVersion, setListVersion] = useState(0);

  const canWrite =
    me != null && canPerform(me, "resource.user_private.write.self");
  const canDelete =
    me != null && canPerform(me, "resource.user_private.delete.self");

  const rowActions = (row: ResourceSummary): ResourceListRowAction[] => {
    if (!canDelete) return [];
    return [{ label: "删除", danger: true, onRun: () => setDeleteTarget(row) }];
  };

  return (
    <div data-testid="resources-private-page">
      {deletedNotice ? (
        <p className="profile-success" data-testid="private-notice">
          {deletedNotice}
        </p>
      ) : null}
      <ResourceList
        key={listVersion}
        scope="private"
        detailBase="private"
        title="我的 Resource"
        subtitle="私有内容仅自己可见；可上传文件或添加公开链接（09 §38.1 入口固定私有归属）"
        actions={
          canWrite ? (
            <button type="button" className="primary-button" onClick={() => setImportOpen(true)}>
              新增 Resource
            </button>
          ) : null
        }
        rowActions={rowActions}
        emptyHint="还没有私有 Resource"
        emptyDescription="可上传文件、或添加公开网页/Git 链接开始使用（上传每个文件生成一个 Resource）。"
        onLoaded={() => recordPartition("private")}
      />
      {importOpen ? (
        <ImportDialog
          scope="private"
          targetLabel="我的 Resource"
          onClose={() => setImportOpen(false)}
          onImported={() => setImportOpen(false)}
        />
      ) : null}
      {deleteTarget ? (
        <DeleteDialog
          scope="private"
          resource={deleteTarget}
          onClose={() => setDeleteTarget(null)}
          onDeleted={() => setDeleteTarget(null)}
          onDeletedWithNotice={(notice) => {
            setDeletedNotice(notice);
            setDeleteTarget(null);
          }}
        />
      ) : null}
      <RestoreSection onRestored={() => setListVersion((v) => v + 1)} />
    </div>
  );
}
