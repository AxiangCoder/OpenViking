/**
 * `/app/resources` 裸路由：默认分区跳转（09 §38.1，P3-E4）。
 * 最近使用的合法分区（localStorage 非敏感偏好）；首次默认 `/app/resources/private`；
 * 存储分区无读取权限时回退私有区（合法分区兜底）。
 */

import { Navigate } from "@tanstack/react-router";
import { useMe } from "@/features/auth/useAuth";
import { canPerform } from "@/lib/permissions";
import { readLastPartition } from "@/features/resources";

export default function ResourcesIndexPage() {
  const me = useMe();
  const last = readLastPartition();
  const target =
    last === "shared" &&
    me != null &&
    canPerform(me, "resource.account_shared.read.account")
      ? "shared"
      : "private";
  return <Navigate to={`/app/resources/${target}`} replace />;
}
