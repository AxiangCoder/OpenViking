/**
 * /app/recycle-bin 我的回收站页（05 §12.5，11 §72，P3-E3 AC⑦⑧）。
 *
 * - 仅展示当前 User 可恢复对象（Session/Resource/Skill 等，服务端按
 *   `restore_allowed` 实时计算），按类型分组恢复；
 * - 恢复按对象类型携带权限码（P4-E3 与 /admin/recycle-bin 共用后端与组件）；
 * - `RESTORE_WINDOW_EXPIRED` 正确展示（AC⑧）；
 * - Session 属主自助恢复（AC⑦）。
 */

import { fetchRecycleBin, restoreRecycleItem } from "@/features/recycle-bin/recycle-bin";
import RecycleBinPanel from "@/components/recycle-bin/RecycleBinPanel";

export default function RecycleBinPage() {
  return (
    <div className="recycle-bin-page" data-testid="recycle-bin-page">
      <RecycleBinPanel
        fetchItems={fetchRecycleBin}
        restoreItem={restoreRecycleItem}
        title="回收站（我的）"
      />
    </div>
  );
}
