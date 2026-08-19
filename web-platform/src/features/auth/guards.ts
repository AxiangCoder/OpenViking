/**
 * 路由 Guard（06 §13.4，14 号计划 §98.1 AC①）。
 *
 * - 未登录访问受保护路由 → 跳 `/login?redirect=<原路径>`（登录后回跳）；
 * - `/oauth/*` 只允许回跳同源授权路由（redirect 净化见 lib/redirect.ts）；
 * - `/admin` 仅 Account Admin、`/platform` 仅 Platform Super Admin（06 §13.2）；
 *   无权限角色访问 → 回到默认入口（不进入登录页）。
 * - 前端 Guard 不是安全边界（06 §13.4）：后端每次请求仍重复鉴权（AC⑤）。
 */

import { redirect } from "@tanstack/react-router";
import { authReady, getAuthState, type AuthState } from "./auth-state";
import { defaultEntryPath, redirectParamFor } from "@/lib/redirect";
import { isAccountAdmin, isPlatformSuperAdmin } from "@/lib/permissions";

export type GuardZone = "app" | "admin" | "platform" | "oauth";

function entryForMissingRole(me: AuthState["me"]): string {
  return defaultEntryPath(me?.roles);
}

export function requireAuthZone(zone: GuardZone) {
  return async ({ location }: { location: { href: string } }) => {
    const state = getAuthState().status === "idle" ? await authReady() : getAuthState();
    if (state.status !== "authenticated") {
      throw redirect({
        to: "/login",
        search: {
          redirect: redirectParamFor(location.href) ?? undefined,
          reason: state.sessionExpired ? "session_expired" : undefined,
        },
      });
    }
    if (zone === "admin" && !isAccountAdmin(state.me?.roles)) {
      throw redirect({ to: entryForMissingRole(state.me) });
    }
    if (zone === "platform" && !isPlatformSuperAdmin(state.me?.roles)) {
      throw redirect({ to: entryForMissingRole(state.me) });
    }
    return state;
  };
}
