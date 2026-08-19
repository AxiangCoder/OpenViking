/**
 * /platform/activity 平台范围任务页（13 §90，05 §12.6，14 号计划 §98.9，P4-E4 AC⑩）。
 *
 * - 复用 P4-E3/P3-E3 ActivityList 共享组件（只挂载不改写，AC⑩）；
 * - `GET /platform/activity`（`task.read.platform`）：平台范围任务，可按目标
 *   Account 过滤（服务端 `account_id` 参数，13 §90）；
 * - 不展示原始 Task ID、内部堆栈与 Worker 路径（13 §87.3）；
 * - 取消需 `task.cancel.platform` + 目标对象写权限（05 §12.6，后端校验）。
 */

import { useEffect, useMemo, useState } from "react";
import {
  cancelPlatformActivity,
  fetchPlatformActivity,
} from "@/features/activity/activity";
import ActivityList from "@/components/activity/ActivityList";
import {
  listPlatformAccounts,
  type PlatformAccount,
} from "@/features/iam/platform";

export default function PlatformActivityPage() {
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [targetAccountId, setTargetAccountId] = useState("");

  useEffect(() => {
    listPlatformAccounts()
      .then((page) => setAccounts(page.items))
      .catch(() => setAccounts([]));
  }, []);

  const fetchItems = useMemo(
    () => () => fetchPlatformActivity(targetAccountId || undefined),
    [targetAccountId],
  );

  return (
    <div className="activity-page" data-testid="platform-activity-page">
      <h2 className="page-title">Activity（平台任务）</h2>
      <p className="profile-hint">
        平台范围任务（05 §12.6，`task.read.platform`），可按目标 Account 过滤；仅展示脱敏摘要，
        不包含原始 Task ID、内部堆栈与 Worker 路径；取消按平台任务权限与目标对象写权限校验。
      </p>
      <div className="admin-filter-bar" data-testid="platform-activity-filter">
        <label>
          目标 Account
          <select
            value={targetAccountId}
            onChange={(e) => setTargetAccountId(e.target.value)}
            data-testid="platform-activity-account-filter"
          >
            <option value="">全部 Account</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name ?? account.code}（{account.code}）
              </option>
            ))}
          </select>
        </label>
        <span className="profile-hint">筛选由服务端执行（`account_id` 查询参数，13 §90）</span>
      </div>
      <ActivityList fetchItems={fetchItems} cancelItem={cancelPlatformActivity} />
    </div>
  );
}
