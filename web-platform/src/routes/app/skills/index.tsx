/**
 * /app/skills 裸路由：默认分区跳转（14 号计划 §98.5 范围 In）。
 * 最近使用的合法分区（platform.last-open-path 非敏感偏好，06 §13.5）；
 * 首次或记录不可用时默认 /app/skills/private。
 */

import { Navigate } from "@tanstack/react-router";
import { getPreference } from "@/lib/storage";

export default function SkillsIndexPage() {
  const lastOpen = getPreference<string>("platform.last-open-path");
  const target =
    typeof lastOpen === "string" && lastOpen.startsWith("/app/skills/shared")
      ? "/app/skills/shared"
      : "/app/skills/private";
  return <Navigate to={target} replace />;
}
