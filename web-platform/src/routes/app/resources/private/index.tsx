import PlaceholderPage from "@/components/PlaceholderPage";
import { PermissionButton } from "@/components/ui/PermissionGate";

export default function ResourcesPrivatePage() {
  return (
    <PlaceholderPage
      title="我的 Resource"
      plannedIn="P3-E4"
      description="查看、新增、编辑元数据、替换/Refresh、Watch、删除；默认目标为私有区（06 §13.3）。"
    >
      <div className="demo-actions">
        <PermissionButton code="resource.user_private.write.self">添加 Resource</PermissionButton>
      </div>
    </PlaceholderPage>
  );
}
