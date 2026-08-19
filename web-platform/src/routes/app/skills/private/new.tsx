/**
 * 新建私有 Skill（10 §54，P3-E5 AC②）。
 * 普通 User 的创建入口固定进入自己的私有区（10 §54.1）。
 */

import { SkillCreatePage } from "@/components/skills/SkillCreatePage";

export default function SkillsPrivateNewPage() {
  return (
    <SkillCreatePage
      scope="me"
      detailPrefix="/app/skills/private"
      backHref="/app/skills/private"
    />
  );
}
