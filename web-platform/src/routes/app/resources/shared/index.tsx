import PlaceholderPage from "@/components/PlaceholderPage";
import { PermissionGate, PermissionButton } from "@/components/ui/PermissionGate";

export default function ResourcesSharedPage() {
  return (
    <PlaceholderPage
      title="Account 共享 Resource"
      plannedIn="P3-E4"
      description="普通 User 只读/检索/引用；共享内容由 Account 管理员维护（06 §13.3）。"
    >
      <PermissionGate code="resource.account_shared.write.account">
        <div className="demo-actions">
          <PermissionButton code="resource.account_shared.write.account">
            发布为共享（管理员）
          </PermissionButton>
        </div>
      </PermissionGate>
    </PlaceholderPage>
  );
}
