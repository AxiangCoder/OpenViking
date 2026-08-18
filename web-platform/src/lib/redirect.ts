/**
 * 登录回跳参数净化（14 号计划 §98.1 AC①，P3-E1）。
 *
 * 规则（06 §13.2 / §13.8）：
 * - 只允许同源内部路径（以单个 "/" 开头、不含协议/`//`/反斜杠）；
 * - `/oauth/*` 只允许回跳同源授权路由 `/oauth/consent`、`/oauth/verify`；
 * - `/login` 与 `/` 拒绝（防回跳循环）。
 */

import { ROLES } from "./permissions";

const OAUTH_REDIRECT_ROUTES = ["/oauth/consent", "/oauth/verify"] as const;

export function sanitizeRedirect(raw: unknown): string | null {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > 2048) return null;
  if (!raw.startsWith("/")) return null;
  if (raw.startsWith("//")) return null;
  if (raw.includes("\\")) return null;
  if (raw === "/" || raw === "/login") return null;
  if (raw.startsWith("/oauth/")) {
    if (!(OAUTH_REDIRECT_ROUTES as readonly string[]).includes(raw)) return null;
  }
  return raw;
}

/** 登录后的默认入口（P3-E2 按角色进入默认入口，本处提供骨架默认）。 */
export function defaultEntryPath(roles: readonly string[] | undefined): string {
  return roles?.includes(ROLES.PLATFORM_SUPER_ADMIN) ? "/platform/accounts" : "/app";
}

/** 保护路由被拦截时构造的 redirect 参数值（同源 pathname + search）。 */
export function redirectParamFor(href: string): string | null {
  try {
    const url = new URL(href, globalThis.location.origin);
    const path = `${url.pathname}${url.search}${url.hash}`;
    return sanitizeRedirect(path);
  } catch {
    return null;
  }
}
