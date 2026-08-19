/**
 * /app/resources/shared/$resourceId Account 共享 Resource 详情（09 §42，P3-E4）。
 * 普通 User 只读（无编辑/Refresh/Watch/删除按钮）；Account Admin 可管理。
 */

import ResourceDetailView from "@/components/resources/ResourceDetailView";

export default function ResourceSharedDetailPage() {
  return (
    <ResourceDetailView
      scope="shared"
      visibilityLabel="Account 共享 Resource"
      backToList="/app/resources/shared"
      onPublishedNavigate={() => undefined}
    />
  );
}
