/**
 * /app/resources/private/$resourceId 我的 Resource 详情（09 §42，P3-E4）。
 * 私有详情：概览/内容/自动同步/活动四标签；编辑/Refresh/替换/Watch/删除/
 * 发布为共享（仅 Account Admin 自己的私有，09 §44.1）。
 */

import { useNavigate } from "@tanstack/react-router";
import ResourceDetailView from "@/components/resources/ResourceDetailView";

export default function ResourcePrivateDetailPage() {
  const navigate = useNavigate();
  return (
    <ResourceDetailView
      scope="private"
      visibilityLabel="我的私有 Resource"
      backToList="/app/resources/private"
      onPublishedNavigate={(newSharedResourceId) => {
        // AC⑦：发布生成新共享 ID，跳转到共享详情（原私有对象保留）
        void navigate({
          to: "/app/resources/shared/$resourceId",
          params: { resourceId: newSharedResourceId.replace(/^res_/, "") },
        });
      }}
    />
  );
}
