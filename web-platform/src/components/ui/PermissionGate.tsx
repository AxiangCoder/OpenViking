/**
 * 按 Permission 的按钮/入口控制（06 §13.4，AC②）。
 * Guard 骨架与按钮控制在本 Epic 冻结；P4-E1 仅做 Account 固定上下文细化。
 */

import type { ReactNode } from "react";
import { useHasPermission } from "@/features/auth/useAuth";

/** 无权限时整块隐藏（导航/入口类）。 */
export function PermissionGate({
  code,
  children,
}: {
  code: string;
  children: ReactNode;
}) {
  const allowed = useHasPermission(code);
  if (!allowed) return null;
  return <>{children}</>;
}

/** 无权限时禁用并保留占位（按钮类，避免布局抖动）。 */
export function PermissionButton({
  code,
  children,
  ...buttonProps
}: {
  code: string;
  children: ReactNode;
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "disabled">) {
  const allowed = useHasPermission(code);
  return (
    <button {...buttonProps} disabled={!allowed} title={allowed ? undefined : "无此操作权限"}>
      {children}
    </button>
  );
}
