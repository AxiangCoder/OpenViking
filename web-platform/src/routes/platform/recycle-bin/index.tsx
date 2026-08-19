/**
 * /platform/recycle-bin 平台回收站页（13 §90，05 §12.6 注，14 号计划 §98.9，P4-E4 AC⑧⑩）。
 *
 * - 复用 P4-E3/P3-E3 RecycleBinPanel 共享组件（只挂载不改写，AC⑩）；
 * - 平台范围回收站：恢复按对象类型校验（05 §12.6 注、88.2）；对 Skill 始终只读
 *   （10 §60：服务端对 Skill 不下发恢复权限 → `restore_allowed=false` 不展示，AC⑧）；
 * - `SKILL_NAME_CONFLICT` / `RESTORE_WINDOW_EXPIRED` 正确展示（AC⑧）。
 */

import {
  fetchPlatformRecycleBin,
  restorePlatformRecycleItem,
} from "@/features/recycle-bin/recycle-bin";
import RecycleBinPanel from "@/components/recycle-bin/RecycleBinPanel";

export default function PlatformRecycleBinPage() {
  return (
    <div className="recycle-bin-page" data-testid="platform-recycle-bin-page">
      <RecycleBinPanel
        fetchItems={fetchPlatformRecycleBin}
        restoreItem={restorePlatformRecycleItem}
        title="回收站（平台）"
        emptyHint={
          <>
            平台没有可恢复的对象。Account/共享 Resource/Skill/User 软删除后进入 30 天回收期；
            平台对 Skill 始终只读（10 §60，不提供恢复入口）；不可恢复类型不显示（13 §88.1）。
          </>
        }
      />
    </div>
  );
}
