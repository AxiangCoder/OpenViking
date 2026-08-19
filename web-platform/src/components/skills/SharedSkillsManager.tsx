/**
 * 共享 Skill 管理组件（06 §13.3，10 §61.2，P3-E5 AC⑦）。
 *
 * - 由本 Epic 开发，同时服务 `/app/skills/shared`（Account Admin 视图）与
 *   `/admin/shared-skills` 两个挂载点，后续 Epic 只复用不重写；
 * - 普通 User 不进入本组件（/app 共享页由路由层决定只读视图）；
 * - 管理能力 = 查看 + 新建（固定进共享区）+ 详情页内编辑/整体替换/删除/恢复；
 * - 共享列表不显示「只有创建者可编辑」暗示（06 §13.3）。
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { listSkills, type SkillRecord } from "@/features/skills";
import { SkillList } from "./SkillList";

export interface SharedSkillsManagerProps {
  /** 新建共享 Skill 的固定入口（固定进当前 Account 共享区，AC⑦）。 */
  newHref: string;
  /** 详情跳转前缀（产品 ID 契约）：/admin/shared-skills 或 /app/skills/shared。 */
  detailPrefix: string;
}

export function SharedSkillsManager({ newHref, detailPrefix }: SharedSkillsManagerProps) {
  const [items, setItems] = useState<SkillRecord[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoadError(null);
    listSkills("account")
      .then((data) => setItems(data.items))
      .catch((error) => {
        setLoadError(
          isPlatformError(error) ? error.message || "无法加载共享 Skill" : "无法加载共享 Skill",
        );
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="skill-page" data-testid="shared-skills-manager">
      <div className="admin-page-header">
        <h2>共享 Skill 管理</h2>
        <Link to={newHref} className="primary-button" data-testid="shared-skill-new">
          新建共享 Skill
        </Link>
      </div>
      <p className="profile-hint">
        共享 Skill 归当前 Account 统一管理；新建、编辑、删除与恢复均由 Account 管理员执行（06 §13.3）。
      </p>
      {loadError ? (
        <p className="login-error" role="alert">
          {loadError}
        </p>
      ) : null}
      {items == null && !loadError ? <p>加载中…</p> : null}
      {items != null ? (
        <SkillList
          items={items}
          ownershipLabel="Account 共享"
          detailHref={(skillId) => `${detailPrefix}/${skillId}`}
          emptyText="当前 Account 还没有共享 Skill。"
        />
      ) : null}
    </div>
  );
}
