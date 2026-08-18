/**
 * 登录页占位（P3-E2 交付正式表单）。
 * 本 Epic 冻结的是回跳机制：展示 sanitizeRedirect 后的回跳目标（AC①），
 * 登录表单与默认入口跳转由 P3-E2 实现。
 */

import { useSearch } from "@tanstack/react-router";
import { sanitizeRedirect } from "@/lib/redirect";

export interface LoginSearch {
  redirect?: string;
  reason?: string;
}

export default function LoginPage() {
  const search = useSearch({ from: "/login" }) as LoginSearch;
  const redirect = sanitizeRedirect(search.redirect);
  return (
    <div className="login-page" data-testid="login-page">
      <h1>OpenViking Platform</h1>
      {search.reason === "session_expired" ? (
        <p className="login-notice" role="status">
          登录会话已过期，请重新登录（安全状态未丢失，AC③）。
        </p>
      ) : null}
      <p className="login-meta">登录表单由 P3-E2 交付（邮箱 + 密码，05 §12.3）。</p>
      {redirect ? (
        <p className="login-redirect" data-testid="login-redirect">
          登录后将回跳至：<code>{redirect}</code>
          {redirect.startsWith("/oauth/")
            ? "（OAuth 仅回跳同源授权路由）"
            : null}
        </p>
      ) : null}
    </div>
  );
}
