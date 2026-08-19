/**
 * 共享 Skill 管理（/admin 挂载点，06 §13.3，P3-E5 AC⑦）。
 * 与 /app/skills/shared（Account Admin 视图）共用 SharedSkillsManager 组件。
 */

import { SharedSkillsManager } from "@/components/skills/SharedSkillsManager";

export default function AdminSharedSkillsPage() {
  return (
    <SharedSkillsManager
      newHref="/admin/shared-skills/new"
      detailPrefix="/admin/shared-skills"
    />
  );
}
