import PlaceholderPage from "@/components/PlaceholderPage";
import { PermissionButton } from "@/components/ui/PermissionGate";

export default function SkillsPrivatePage() {
  return (
    <PlaceholderPage
      title="我的 Skill"
      plannedIn="P3-E5"
      description="查看、使用、管理自己的私有 Skill；在线创建与 ZIP 上传在 P3-E5 交付。"
    >
      <div className="demo-actions">
        <PermissionButton code="skill.user_private.manage.self">新建 Skill</PermissionButton>
      </div>
    </PlaceholderPage>
  );
}
