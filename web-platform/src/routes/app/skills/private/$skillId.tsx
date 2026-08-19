/**
 * 我的 Skill 详情（10 §53.3/§55.2/§56/§59，P3-E5）。
 */

import { SkillDetailPage } from "@/components/skills/SkillDetailPage";
import { useParams } from "@tanstack/react-router";

export default function SkillPrivateDetailPage() {
  const { skillId } = useParams({ strict: false });
  return (
    <SkillDetailPage
      scope="me"
      skillId={skillId}
      ownershipLabel="我的私有"
      canManage
      backHref="/app/skills/private"
    />
  );
}
