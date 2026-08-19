/**
 * React 绑定：auth 状态 / 权限判定 hooks（06 §13.4）。
 * Role/权限变化后（refreshAuth）store 更新，导航与按钮即时刷新。
 */

import { useSyncExternalStore } from "react";
import { getAuthState, subscribeAuth, type AuthMeResult } from "./auth-state";
import {
  canManageSharedContent,
  canPerform,
  isAccountAdmin,
  isPlatformSuperAdmin,
} from "@/lib/permissions";

export function useAuth() {
  const state = useSyncExternalStore(subscribeAuth, getAuthState, getAuthState);
  return state;
}

export function useMe(): AuthMeResult | null {
  return useAuth().me;
}

export function useHasPermission(code: string): boolean {
  const me = useMe();
  return canPerform(me, code);
}

export function useCanManageSharedContent(): boolean {
  const me = useMe();
  return canManageSharedContent(me);
}

export function useIsAccountAdmin(): boolean {
  const me = useMe();
  return isAccountAdmin(me?.roles);
}

export function useIsPlatformSuperAdmin(): boolean {
  const me = useMe();
  return isPlatformSuperAdmin(me?.roles);
}
