/**
 * /admin/shared-resources 共享 Resource 集中管理（06 §13.3，P3-E4）。
 * 与 /app/resources/shared 同组件（组件本 Epic 开发、双挂载点；
 * P4-E3 只复用不重写，14 号计划 §98.4/§98.8）。
 */

import { useMe } from "@/features/auth/useAuth";
import SharedResourcesManager from "@/components/resources/SharedResourcesManager";

export default function AdminSharedResourcesPage() {
  const me = useMe();
  const accountLabel = me?.account?.name ?? me?.account?.code ?? "当前 Account";

  return (
    <SharedResourcesManager
      title="共享 Resource 管理"
      subtitle={`${accountLabel} 共享区集中管理（新增、删除见本页；编辑/Refresh/Watch 在详情页，06 §13.3）`}
      detailBase="shared"
    />
  );
}
