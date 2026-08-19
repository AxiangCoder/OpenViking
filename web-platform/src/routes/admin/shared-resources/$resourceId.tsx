/**
 * /admin/shared-resources/$resourceId 共享 Resource 详情（P3-E4）。
 * 与 /app/resources/shared/$resourceId 同组件。
 */

import ResourceDetailView from "@/components/resources/ResourceDetailView";

export default function AdminSharedResourceDetailPage() {
  return (
    <ResourceDetailView
      scope="shared"
      visibilityLabel="Account 共享 Resource"
      backToList="/admin/shared-resources"
      onPublishedNavigate={() => undefined}
    />
  );
}
