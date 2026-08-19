/**
 * /app/resources/shared Account 共享 Resource（06 §13.3，P3-E4）。
 * 普通 User 只读 + 「共享内容由 Account 管理员维护」；Account Admin 与
 * /admin/shared-resources 相同管理能力（同组件，P4-E3 只复用不重写）。
 */

import { useMe } from "@/features/auth/useAuth";
import { canManageSharedContent } from "@/lib/permissions";
import SharedResourcesManager from "@/components/resources/SharedResourcesManager";

export default function ResourcesSharedPage() {
  const me = useMe();
  const accountLabel = me?.account?.name ?? me?.account?.code ?? "当前 Account";
  const isAdmin = canManageSharedContent(me);

  return (
    <SharedResourcesManager
      title="Account 共享 Resource"
      subtitle={
        isAdmin
          ? `${accountLabel} 共享区管理（与 /admin/shared-resources 相同能力）`
          : "共享内容由 Account 管理员维护；普通 User 可查看、检索、引用（06 §13.3）"
      }
      detailBase="shared"
    />
  );
}
