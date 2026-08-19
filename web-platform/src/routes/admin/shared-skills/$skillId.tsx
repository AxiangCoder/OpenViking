/**
 * 共享 Skill 管理详情（/admin 挂载点，P3-E5）。
 */

import { useParams } from "@tanstack/react-router";
import { SkillDetailPage } from "@/components/skills/SkillDetailPage";

export default function AdminSharedSkillPage() {
  const { skillId } = useParams({ strict: false }) as Record<string, string>;
  return (
    <SkillDetailPage
      scope="account"
      skillId={skillId}
      ownershipLabel="Account 共享"
      canManage
      backHref="/admin/shared-skills"
    />
  );
}
