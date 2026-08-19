/**
 * /admin/activity 共享对象任务页（13 §87.3，05 §12.6，14 号计划 §98.8，P4-E3 AC③）。
 *
 * - 仅当前 Account 共享对象任务（`task.read.account_shared`）；成员私有对象任务不显示；
 * - 复用 P3-E3 ActivityList 共享组件（只挂载不改写）；挂载点差异仅在后端 scope；
 * - 不展示原始 Task ID、内部堆栈与 Worker 路径（13 §87.3，AC③）；
 * - 取消需 `task.cancel.account_shared` + 目标对象写权限（05 §12.6，后端校验，AC③）。
 */

import { fetchAdminActivity, cancelAdminActivity } from "@/features/activity/activity";
import ActivityList from "@/components/activity/ActivityList";

export default function AdminActivityPage() {
  return (
    <div className="activity-page" data-testid="admin-activity-page">
      <h2 className="page-title">Activity（共享任务）</h2>
      <p className="profile-hint">
        仅展示当前 Account 共享对象任务（成员私有对象任务日志不显示，04 §10.12）；仅展示脱敏摘要，
        不包含原始 Task ID、内部堆栈与 Worker 路径；取消按共享任务权限与目标对象写权限校验（05 §12.6）。
      </p>
      <ActivityList fetchItems={fetchAdminActivity} cancelItem={cancelAdminActivity} />
    </div>
  );
}
