/**
 * 登录页（13 §80，P3-E2 AC①②）。
 *
 * - 仅邮箱+密码两个输入，无注册/邀请/激活/找回密码/企业登录入口（80.2，AC①）；
 * - 错误统一文案（80.5：凭证错误「邮箱或密码不正确」；限流提示稍后重试并冷却；
 *   停用/开通中状态文案逐码映射；网络错误保留输入可重试）；
 * - 登录成功由服务端签发 HttpOnly Cookie（AC②），前端持内存态 CSRF Token
 *   （04 §10.7，绝不落存储），刷新 /auth/me 后按角色进入默认入口或回跳目标
 *   （OAuth 仅回跳同源授权路由，06 §13.8）；
 * - 已登录访问 /login 直接跳默认入口（80.1，AC②）。
 */

import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useAuth } from "@/features/auth/useAuth";
import { refreshAuth, setCsrfToken } from "@/features/auth/auth-state";
import { login, loginErrorMessage } from "@/features/auth/login";
import { defaultEntryPath, sanitizeRedirect } from "@/lib/redirect";

export interface LoginSearch {
  redirect?: string;
  reason?: string;
}

const REASON_NOTICES: Record<string, string> = {
  session_expired: "登录会话已过期，请重新登录（安全状态未丢失，AC③）。",
  logged_out: "已安全退出所有设备。",
  password_changed: "密码已修改，请使用新密码重新登录。",
};

export default function LoginPage() {
  const search = useSearch({ from: "/login" }) as LoginSearch;
  const navigate = useNavigate();
  const { status, me } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [cooldownLeft, setCooldownLeft] = useState(0);
  const [submitting, setSubmitting] = useState(false);

  const redirect = sanitizeRedirect(search.redirect);
  const notice = search.reason ? REASON_NOTICES[search.reason] : undefined;

  // AC②：已登录访问 /login 跳默认入口（或回跳目标）
  useEffect(() => {
    if (status !== "authenticated" || me == null) return;
    void navigate({ to: redirect ?? defaultEntryPath(me.roles) });
  }, [status, me, redirect, navigate]);

  // 限流冷却倒计时（80.5：按钮进入冷却）
  useEffect(() => {
    if (cooldownLeft <= 0) return;
    const timer = globalThis.setTimeout(() => setCooldownLeft((v) => v - 1), 1000);
    return () => globalThis.clearTimeout(timer);
  }, [cooldownLeft]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting || cooldownLeft > 0) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await login(email, password);
      if (result.csrf_token) setCsrfToken(result.csrf_token);
      await refreshAuth();
      // 导航由上方 authenticated effect 完成（回跳目标或默认入口）
    } catch (err) {
      const mapped = loginErrorMessage(err);
      setError(mapped.text);
      if (mapped.cooldownSeconds != null) setCooldownLeft(mapped.cooldownSeconds);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-page" data-testid="login-page">
      <h1 className="login-title">OpenViking Platform</h1>
      {notice ? (
        <p className="login-notice" role="status">
          {notice}
        </p>
      ) : null}
      {redirect ? (
        <p className="login-redirect" data-testid="login-redirect">
          登录后将回跳至：<code>{redirect}</code>
          {redirect.startsWith("/oauth/") ? "（OAuth 仅回跳同源授权路由）" : null}
        </p>
      ) : null}

      <form className="login-form" onSubmit={handleSubmit} data-testid="login-form">
        <label className="login-field">
          <span>邮箱</span>
          <input
            type="email"
            name="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={submitting || cooldownLeft > 0}
            data-testid="login-email"
          />
        </label>
        <label className="login-field">
          <span>密码</span>
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={submitting || cooldownLeft > 0}
            data-testid="login-password"
          />
        </label>
        {error ? (
          <p className="login-error" role="alert" data-testid="login-error">
            {error}
          </p>
        ) : null}
        <button
          type="submit"
          className="login-submit"
          disabled={submitting || cooldownLeft > 0}
          data-testid="login-submit"
        >
          {cooldownLeft > 0
            ? `请稍后再试（${cooldownLeft}s）`
            : submitting
              ? "登录中…"
              : "登录"}
        </button>
      </form>
    </div>
  );
}
