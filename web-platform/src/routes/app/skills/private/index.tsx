/**
 * 我的 Skill（私有分区列表）（10 §53.2/§61.1，P3-E5 AC①）。
 *
 * - 展示名称/描述/标签/归属/更新时间/辅助文件标识；无 URI 与控制文件（AC①）；
 * - 「新建 Skill」入口固定进入私有区（06 §13.3，AC②）；
 * - 访问分区时记录最近打开路径偏好，供裸路由 `/app/skills` 默认分区跳转。
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { setPreference } from "@/lib/storage";
import { listSkills, type SkillRecord } from "@/features/skills";
import { SkillList } from "@/components/skills/SkillList";

export default function SkillsPrivatePage() {
  const [items, setItems] = useState<SkillRecord[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoadError(null);
    listSkills("me")
      .then((data) => setItems(data.items))
      .catch((error) => {
        setLoadError(
          isPlatformError(error) ? error.message || "无法加载我的 Skill" : "无法加载我的 Skill",
        );
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    setPreference("platform.last-open-path", "/app/skills/private");
  }, []);

  return (
    <div className="skill-page" data-testid="skills-private-page">
      <div className="admin-page-header">
        <h2>我的 Skill</h2>
        <Link to="/app/skills/private/new" className="primary-button" data-testid="skill-new">
          新建 Skill
        </Link>
      </div>
      <p className="profile-hint">你的私有 Skill 仅本人可见；共享内容在「共享 Skill」分区查看。</p>
      {loadError ? (
        <p className="login-error" role="alert">
          {loadError}
        </p>
      ) : null}
      {items == null && !loadError ? <p>加载中…</p> : null}
      {items != null ? (
        <SkillList
          items={items}
          ownershipLabel="我的私有"
          detailHref={(skillId) => `/app/skills/private/${skillId}`}
          emptyText="还没有私有 Skill。点击「新建 Skill」在线创建，或上传 SKILL.md / ZIP。"
        />
      ) : null}
    </div>
  );
}
