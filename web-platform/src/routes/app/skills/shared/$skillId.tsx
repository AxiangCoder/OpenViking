/**
 * Account 共享 Skill 详情（10 §53.3，P3-E5 AC⑤⑦）。
 * 普通 User 只读（无管理按钮）；Account Admin 可编辑/整体替换/删除/恢复。
 * 私密配置为当前 User 自己的配置（05 §12.5，AC⑤⑧）。
 */

import { useParams } from "@tanstack/react-router";
import { useHasPermission } from "@/features/auth/useAuth";
import { SkillDetailPage } from "@/components/skills/SkillDetailPage";

export default function SkillSharedDetailPage() {
  const { skillId } = useParams({ strict: false }) as Record<string, string>;
  const canManage = useHasPermission("skill.account_shared.manage.account");
  return (
    <SkillDetailPage
      scope="account"
      skillId={skillId}
      ownershipLabel="Account 共享"
      canManage={canManage}
      backHref="/app/skills/shared"
    />
  );
}
