/**
 * 个人设置页（13 §81，P3-E2 AC⑧⑨）。
 *
 * - 基本信息区：显示名/邮箱/所属 Account 只读展示，无 Account 切换入口（81.2）；
 * - 修改密码区：必填当前密码；成功后 Session 轮换 → 提示重新登录（81.3，AC⑧）；
 * - 登录设备区：仅当前登录 Session 脱敏摘要（最后活动时间、IP hash、User-Agent
 *   截断），无单会话列表（81.4，AC⑨）；
 * - 操作区：「退出所有设备」撤销全部登录 Session，不影响 API Key 与 OAuth Grant
 *   （81.5，AC⑧）；退出后全量跳登录页以清空内存态。
 */

import { useEffect, useState, type FormEvent } from "react";
import { useMe } from "@/features/auth/useAuth";
import { isPlatformError } from "@/lib/platform-client";
import { leaveToLogin } from "@/features/auth/session";
import {
  changePassword,
  fetchSessionSummary,
  identityFromMe,
  logoutAll,
  type SessionSummary,
} from "@/features/profile/profile";

const MIN_PASSWORD_LENGTH = 12;

export default function ProfilePage() {
  const me = useMe();
  const identity = identityFromMe(me);

  // ── 登录设备区（AC⑨：仅当前 Session 摘要）──
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [summaryState, setSummaryState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    let cancelled = false;
    setSummaryState("loading");
    fetchSessionSummary()
      .then((data) => {
        if (cancelled) return;
        setSummary(data);
        setSummaryState("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setSummaryState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // ── 修改密码（81.3，AC⑧：必填旧密码）──
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordChanged, setPasswordChanged] = useState(false);
  const [changing, setChanging] = useState(false);

  // ── 退出所有设备（81.4，AC⑧）──
  const [confirmingLogoutAll, setConfirmingLogoutAll] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  async function handlePasswordChange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (changing) return;
    setPasswordError(null);
    if (!oldPassword) {
      setPasswordError("请输入当前密码");
      return;
    }
    if (newPassword.length < MIN_PASSWORD_LENGTH) {
      setPasswordError(`新密码至少 ${MIN_PASSWORD_LENGTH} 位`);
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordError("两次输入的新密码不一致");
      return;
    }
    setChanging(true);
    try {
      await changePassword(oldPassword, newPassword);
      setOldPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setPasswordChanged(true);
    } catch (err) {
      setPasswordError(
        isPlatformError(err) && (err.code === "LOGIN_FAILED" || err.code === "LOGIN_RATE_LIMITED")
          ? "当前密码不正确"
          : isPlatformError(err)
            ? err.message || "密码修改失败，请稍后重试"
            : "密码修改失败，请稍后重试",
      );
    } finally {
      setChanging(false);
    }
  }

  async function handleLogoutAll() {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await logoutAll();
      // 全量跳转清空内存态（CSRF 等）；退出不影响 API Key 与 OAuth Grant（AC⑧）
      leaveToLogin("logged_out");
    } catch {
      setLoggingOut(false);
      setConfirmingLogoutAll(false);
    }
  }

  return (
    <div className="profile-page" data-testid="profile-page">
      <section className="card profile-section" aria-label="基本信息">
        <h2>基本信息</h2>
        <dl className="profile-identity">
          <div className="profile-row">
            <dt>显示名</dt>
            <dd data-testid="profile-display-name">{identity?.displayName ?? "—"}</dd>
          </div>
          <div className="profile-row">
            <dt>邮箱</dt>
            <dd data-testid="profile-email">{identity?.email ?? "—"}</dd>
          </div>
          <div className="profile-row">
            <dt>所属 Account</dt>
            <dd data-testid="profile-account">{identity?.accountLabel ?? "—"}</dd>
          </div>
        </dl>
        {identity?.partial ? (
          <p className="profile-hint">部分信息将在后端 /auth/me 补齐后展示（当前为脱敏占位）。</p>
        ) : null}
      </section>

      <section className="card profile-section" aria-label="登录设备">
        <h2>登录设备</h2>
        <div className="profile-session-card" data-testid="profile-session-summary">
          {summaryState === "loading" ? <p>加载中…</p> : null}
          {summaryState === "error" ? <p>无法加载当前会话摘要。</p> : null}
          {summary && summaryState === "ready" ? (
            <>
              <p>
                最后活动时间：
                <strong>{new Date(summary.last_seen_at).toLocaleString()}</strong>
              </p>
              <p>
                登录时间：<strong>{new Date(summary.created_at).toLocaleString()}</strong>
              </p>
              <p>
                设备标识（IP hash）：<code data-testid="session-ip-hash">{summary.ip_hash ?? "—"}</code>
              </p>
              <p>
                浏览器：<span data-testid="session-user-agent">{summary.user_agent ?? "—"}</span>
              </p>
            </>
          ) : null}
        </div>
        <p className="profile-hint">
          仅展示当前登录会话的脱敏摘要；如需撤销其它设备，请使用「退出所有设备」。
        </p>
        <div className="profile-actions">
          <button
            type="button"
            className="danger-button"
            onClick={() => setConfirmingLogoutAll(true)}
            data-testid="logout-all-button"
          >
            退出所有设备
          </button>
        </div>
        {confirmingLogoutAll ? (
          <div className="confirm-dialog" role="dialog" aria-label="退出所有设备确认">
            <p>将撤销当前用户全部网页登录会话（不删除对话与记忆，不影响 API Key 与 OAuth 授权）。</p>
            <div className="profile-actions">
              <button type="button" onClick={() => setConfirmingLogoutAll(false)} disabled={loggingOut}>
                取消
              </button>
              <button
                type="button"
                className="danger-button"
                onClick={() => void handleLogoutAll()}
                disabled={loggingOut}
                data-testid="logout-all-confirm"
              >
                {loggingOut ? "退出中…" : "确认退出"}
              </button>
            </div>
          </div>
        ) : null}
      </section>

      <section className="card profile-section" aria-label="修改密码">
        <h2>修改密码</h2>
        {passwordChanged ? (
          <div className="profile-success" role="status" data-testid="password-changed-notice">
            <p>密码已修改，当前会话已轮换。为安全起见，请重新登录。</p>
            <button type="button" className="primary-button" onClick={() => leaveToLogin("password_changed")}>
              重新登录
            </button>
          </div>
        ) : (
          <form className="profile-form" onSubmit={handlePasswordChange} data-testid="password-form">
            <label className="login-field">
              <span>当前密码</span>
              <input
                type="password"
                name="old_password"
                autoComplete="current-password"
                required
                value={oldPassword}
                onChange={(e) => setOldPassword(e.target.value)}
                disabled={changing}
                data-testid="password-old"
              />
            </label>
            <label className="login-field">
              <span>新密码（至少 {MIN_PASSWORD_LENGTH} 位）</span>
              <input
                type="password"
                name="new_password"
                autoComplete="new-password"
                required
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                disabled={changing}
                data-testid="password-new"
              />
            </label>
            <label className="login-field">
              <span>确认新密码</span>
              <input
                type="password"
                name="confirm_password"
                autoComplete="new-password"
                required
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                disabled={changing}
                data-testid="password-confirm"
              />
            </label>
            {passwordError ? (
              <p className="login-error" role="alert" data-testid="password-error">
                {passwordError}
              </p>
            ) : null}
            <div className="profile-actions">
              <button type="submit" className="primary-button" disabled={changing} data-testid="password-submit">
                {changing ? "提交中…" : "修改密码"}
              </button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
