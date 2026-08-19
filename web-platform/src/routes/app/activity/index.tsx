/**
 * /app/activity 聚合任务页（06 §13.7，05 §12.5，P3-E3 AC⑩）。
 *
 * - 当前 User 私有对象任务 + 有权查看的 Account 共享对象任务；
 * - 仅展示脱敏 Operation 摘要与可取消任务；无原始 Task ID/堆栈/Worker 路径（AC⑩）；
 * - 取消弹窗展示目标/任务类型/当前状态/影响（06 §13.7）；取消后进入 cancelling。
 */

import { fetchActivity, cancelActivity } from "@/features/activity/activity";
import ActivityList from "@/components/activity/ActivityList";

export default function ActivityPage() {
  return (
    <div className="activity-page" data-testid="activity-page">
      <h2 className="page-title">Activity</h2>
      <p className="profile-hint">
        聚合当前用户私有对象任务与有权查看的 Account 共享对象任务；仅展示脱敏摘要。
      </p>
      <ActivityList fetchItems={fetchActivity} cancelItem={cancelActivity} />
    </div>
  );
}
