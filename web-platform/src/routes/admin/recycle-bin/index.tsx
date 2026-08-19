/**
 * /admin/recycle-bin 回收站页（13 §88，05 §12.6 注，14 号计划 §98.8，P4-E3 AC⑤⑥）。
 *
 * - 按对象类型分组展示与恢复，权限按对象类型分别校验（不存在单一 Account 范围恢复权限）；
 * - 复用 P3-E3 RecycleBinPanel 共享组件（只挂载不改写）；与 /app/recycle-bin 共用组件；
 * - 仅展示服务端标记可恢复的对象（不可恢复类型不显示，AC⑤）；
 * - `SKILL_NAME_CONFLICT` / `RESTORE_WINDOW_EXPIRED` 正确展示；恢复动作写审计（AC⑥）。
 */

import {
  fetchAdminRecycleBin,
  restoreAdminRecycleItem,
} from "@/features/recycle-bin/recycle-bin";
import RecycleBinPanel from "@/components/recycle-bin/RecycleBinPanel";

export default function AdminRecycleBinPage() {
  return (
    <div className="recycle-bin-page" data-testid="admin-recycle-bin-page">
      <RecycleBinPanel
        fetchItems={fetchAdminRecycleBin}
        restoreItem={restoreAdminRecycleItem}
        title="回收站（Account）"
        emptyHint={
          <>
            当前 Account 没有可恢复的对象。共享 Resource/Skill 与成员 User 软删除后进入 30 天
            回收期；不可恢复类型（如其他用户的私有 Skill/Session）不显示（13 §88.1）。
          </>
        }
      />
    </div>
  );
}
