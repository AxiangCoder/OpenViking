/**
 * Account 共享 Skill（06 §13.3，10 §61.2，P3-E5 AC⑦）。
 *
 * - 普通 User：只读列表 + 「共享内容由 Account 管理员维护」提示，无管理入口；
 * - Account Admin：渲染与 /admin/shared-skills 相同的管理组件（本 Epic 开发，
 *   同时服务 /app 与 /admin 挂载点；共享新增固定进共享区，AC⑦）。
 */

import { useCallback, useEffect, useState } from "react";
import { isPlatformError } from "@/lib/platform-client";
import { setPreference } from "@/lib/storage";
import { useHasPermission } from "@/features/auth/useAuth";
import { listSkills, type SkillRecord } from "@/features/skills";
import { SkillList } from "@/components/skills/SkillList";
import { SharedSkillsManager } from "@/components/skills/SharedSkillsManager";

export default function SkillsSharedPage() {
  const canManage = useHasPermission("skill.account_shared.manage.account");

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
    if (canManage) return;
    load();
  }, [canManage, load]);

  useEffect(() => {
    setPreference("platform.last-open-path", "/app/skills/shared");
  }, []);

  if (canManage) {
    return (
      <SharedSkillsManager
        newHref="/admin/shared-skills/new"
        detailPrefix="/app/skills/shared"
      />
    );
  }

  return (
    <div className="skill-page" data-testid="skills-shared-page">
      <div className="admin-page-header">
        <h2>Account 共享 Skill</h2>
      </div>
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
          detailHref={(skillId) => `/app/skills/shared/${skillId}`}
          emptyText="当前 Account 还没有共享 Skill。"
          hint="共享内容由 Account 管理员维护。"
        />
      ) : null}
    </div>
  );
}
