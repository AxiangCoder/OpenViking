/**
 * 新建共享 Skill（10 §54.1，P3-E5 AC⑦）。
 * Account Admin 从共享管理入口创建，固定进入当前 Account 共享区。
 */

import { SkillCreatePage } from "@/components/skills/SkillCreatePage";

export default function AdminSharedSkillsNewPage() {
  return (
    <SkillCreatePage
      scope="account"
      detailPrefix="/admin/shared-skills"
      backHref="/admin/shared-skills"
    />
  );
}
