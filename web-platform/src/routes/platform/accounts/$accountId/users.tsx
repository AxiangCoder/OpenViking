/**
 * /platform/accounts/{accountId}/users 目标 Account 用户管理
 * （13 §89.3，05 §12.6，14 号计划 §98.9，P4-E4 AC②⑥）。
 *
 * - 目标 Account 是管理浏览的 Subject（89.1）：页头明确 Account 上下文，
 *   不改变登录者 Actor 身份、无 Account 切换入口（AC②）；
 * - 列表：code/显示名/邮箱/状态/角色/最近登录/创建时间；cursor 分页；
 * - 提升：仅 `user → account_admin`（`role.assign.platform`，后端强制，AC⑥），
 *   确认弹窗展示提升影响（即时生效免重登，04 §10.6）与分级校验说明（06 §13.9）；
 * - 平台级重置（`user.password.reset.platform`）：仅对严格低级别目标显示按钮
 *   （PSA 永不显示，AC⑥）；确认弹窗提示退出全部网页登录设备/保留对话与记忆/
 *   不撤销 API Key（06 §13.9）；成功一次性展示新密码；
 * - 成员数据页为 Subject 视图（同 84.2），可进入检索/Session/Resource/Skill
 *   只读查看（89.3，AC⑦）；Skill 路由全部只读；
 * - 按钮按 Permission 隐藏（06 §13.4）；前端不是安全边界（AC①）。
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { useMe } from "@/features/auth/useAuth";
import { canPerform, canResetTargetPassword } from "@/lib/permissions";
import OneTimeSecret from "@/components/ui/OneTimeSecret";
import {
  ADMIN_USER_STATUS_LABELS,
  adminRoleLabel,
  formatIsoDateTime,
  type AdminPasswordResetResult,
  type AdminUserRecord,
} from "@/features/iam/admin-users";
import {
  isPlatformResetTarget,
  listPlatformAccountUsers,
  listPlatformAccounts,
  platformErrorMessage,
  promotePlatformAccountUser,
  resetPlatformAccountUserPassword,
  type PlatformAccount,
} from "@/features/iam/platform";

interface ResetResultState {
  user: AdminUserRecord;
  result: AdminPasswordResetResult;
}

export default function PlatformAccountUsersPage() {
  const params = useParams({ strict: false }) as Record<string, string>;
  const accountId = params["accountId"] ?? "";
  const me = useMe();
  const actorLabel = me?.user.display_name ?? me?.user.email ?? me?.user.id ?? "当前管理员";

  const [account, setAccount] = useState<PlatformAccount | null>(null);
  const [users, setUsers] = useState<AdminUserRecord[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(
    null,
  );
  const [loadingMore, setLoadingMore] = useState(false);

  const [confirmPromote, setConfirmPromote] = useState<AdminUserRecord | null>(null);
  const [promoting, setPromoting] = useState(false);

  const [confirmReset, setConfirmReset] = useState<AdminUserRecord | null>(null);
  const [resetting, setResetting] = useState(false);
  const [resetResult, setResetResult] = useState<ResetResultState | null>(null);

  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const accountLabel = account?.name ?? account?.code ?? accountId ?? "—";

  const load = useCallback(() => {
    setLoadError(null);
    setUsers(null);
    setNextCursor(null);
    listPlatformAccounts()
      .then((page) => {
        setAccount(page.items.find((a) => a.id === accountId) ?? null);
      })
      .catch(() => setAccount(null));
    listPlatformAccountUsers(accountId)
      .then((page) => {
        setUsers(page.items);
        setNextCursor(page.next_cursor);
      })
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "无法加载用户列表" : "无法加载用户列表",
          requestId: isPlatformError(error) ? error.requestId : null,
        });
      });
  }, [accountId]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleLoadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setActionError(null);
    try {
      const page = await listPlatformAccountUsers(accountId, nextCursor);
      setUsers((prev) => [...(prev ?? []), ...page.items]);
      setNextCursor(page.next_cursor);
    } catch (error) {
      setActionError(platformErrorMessage(error, "加载更多失败，请稍后重试"));
    } finally {
      setLoadingMore(false);
    }
  }

  const canPromote = canPerform(me, "role.assign.platform");
  const canReset = canPerform(me, "user.password.reset.platform");

  // ── 提升（仅 user → account_admin，后端强制；AC⑥）──

  function handleConfirmPromote() {
    if (!confirmPromote || promoting) return;
    setPromoting(true);
    setActionError(null);
    promotePlatformAccountUser(accountId, confirmPromote.id)
      .then((updated) => {
        setNotice(`已将 ${updated.display_name ?? updated.username} 提升为 Account Admin。`);
        setConfirmPromote(null);
        load();
      })
      .catch((error) => {
        setActionError(platformErrorMessage(error, "提升失败，请稍后重试"));
      })
      .finally(() => setPromoting(false));
  }

  // ── 平台级分级重置（禁目标 PSA；成功撤销目标全部登录 Session）──

  function handleConfirmReset() {
    if (!confirmReset || resetting) return;
    setResetting(true);
    setActionError(null);
    resetPlatformAccountUserPassword(accountId, confirmReset.id)
      .then((result) => {
        setResetResult({ user: confirmReset, result });
        setConfirmReset(null);
      })
      .catch((error) => {
        setActionError(platformErrorMessage(error, "重置失败，请稍后重试"));
      })
      .finally(() => setResetting(false));
  }

  if (resetResult) {
    return (
      <OneTimeSecret
        title="密码已重置"
        testIdPrefix="platform-user-reset"
        secret={resetResult.result.new_password}
        subjectLine={`用户：${resetResult.user.display_name ?? resetResult.user.username}（${resetResult.user.email}）｜ 角色：${adminRoleLabel(resetResult.user.role)} ｜ 目标 Account：${accountLabel}｜ 已撤销 ${resetResult.result.sessions_revoked} 个登录会话`}
        onClose={() => setResetResult(null)}
      />
    );
  }

  return (
    <div className="admin-page" data-testid="platform-account-users-page">
      <div className="admin-page-header">
        <div>
          <h2>Account 用户</h2>
          <p className="admin-account-context" data-testid="platform-users-account-context">
            以 <strong>{actorLabel}</strong> 身份管理浏览目标 Account：
            <strong>{accountLabel}</strong>
            （选择目标 Account 是管理浏览，不改变登录者身份，无 Account 切换入口，AC②）
          </p>
        </div>
      </div>
      <div className="profile-actions">
        <Link to="/platform/accounts" className="placeholder-back" data-testid="platform-users-back-accounts">
          ← 返回 Accounts
        </Link>
      </div>

      {loadError ? (
        <div className="admin-load-error" role="alert" data-testid="platform-users-load-error">
          <p className="login-error">{loadError.message}</p>
          {loadError.requestId ? (
            <p className="error-page-request-id">
              请求 ID：<code>{loadError.requestId}</code>
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="button" onClick={load} data-testid="platform-users-retry">
              重试
            </button>
          </div>
        </div>
      ) : null}

      {users == null && !loadError ? (
        <p className="profile-hint" data-testid="platform-users-loading">
          加载中…
        </p>
      ) : null}

      {users != null && users.length === 0 ? (
        <div className="empty-state" data-testid="platform-users-empty">
          该 Account 暂无用户。
        </div>
      ) : null}

      {users != null && users.length > 0 ? (
        <section className="card" data-testid="platform-users-list">
          <p className="profile-hint" data-testid="platform-users-promote-note">
            仅支持将 <code>user</code> 提升为 <code>account_admin</code>（05 §12.6，后端强制，AC⑥）。
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>code</th>
                <th>显示名</th>
                <th>邮箱</th>
                <th>状态</th>
                <th>角色</th>
                <th>最近登录</th>
                <th>创建时间</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id} data-testid={`platform-user-row-${user.id}`}>
                  <td>
                    <code>{user.username}</code>
                  </td>
                  <td>{user.display_name ?? "—"}</td>
                  <td>{user.email}</td>
                  <td>
                    <span className={`status-badge ${user.status}`}>
                      {ADMIN_USER_STATUS_LABELS[user.status] ?? user.status}
                    </span>
                  </td>
                  <td>
                    <span className="role-badge">{adminRoleLabel(user.role)}</span>
                  </td>
                  <td>{formatIsoDateTime(user.last_login_at)}</td>
                  <td>{formatIsoDateTime(user.created_at)}</td>
                  <td>
                    <div className="row-actions">
                      {canPerform(me, "memory.read.platform") ||
                      canPerform(me, "session.read.platform") ||
                      canPerform(me, "resource.user_private.read.platform") ||
                      canPerform(me, "skill.user_private.read.platform") ? (
                        <Link
                          to={`/platform/accounts/${accountId}/users/${user.id}/data`}
                          data-testid={`platform-user-data-${user.id}`}
                        >
                          查看数据
                        </Link>
                      ) : null}
                      {canPerform(me, "credential.read.platform") ? (
                        <Link
                          to={`/platform/accounts/${accountId}/users/${user.id}/api-keys`}
                          data-testid={`platform-user-keys-${user.id}`}
                        >
                          API Keys
                        </Link>
                      ) : null}
                      {canPromote && user.role === "user" && user.status === "active" ? (
                        <button
                          type="button"
                          onClick={() => {
                            setActionError(null);
                            setConfirmPromote(user);
                          }}
                          data-testid={`platform-user-promote-${user.id}`}
                        >
                          提升为 Account Admin
                        </button>
                      ) : null}
                      {canReset &&
                      isPlatformResetTarget(user.role) &&
                      canResetTargetPassword(me?.roles, user.role ?? undefined) ? (
                        <button
                          type="button"
                          onClick={() => {
                            setActionError(null);
                            setConfirmReset(user);
                          }}
                          data-testid={`platform-user-reset-${user.id}`}
                        >
                          重置密码
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {nextCursor ? (
            <div className="admin-pagination">
              <button
                type="button"
                onClick={() => void handleLoadMore()}
                disabled={loadingMore}
                data-testid="platform-users-load-more"
              >
                {loadingMore ? "加载中…" : "加载更多"}
              </button>
            </div>
          ) : null}
        </section>
      ) : null}

      {actionError ? (
        <p className="login-error" role="alert" data-testid="platform-users-action-error">
          {actionError}
        </p>
      ) : null}
      {notice ? (
        <p className="profile-success" role="status" data-testid="platform-users-notice">
          {notice}
        </p>
      ) : null}

      {/* ── 提升确认弹窗（89.3：仅 user→account_admin；分级校验说明，06 §13.9）── */}
      {confirmPromote ? (
        <div className="confirm-dialog" role="dialog" aria-label="提升 Account Admin 确认">
          <h3>提升为 Account Admin</h3>
          <p>
            确认将 <strong>{confirmPromote.display_name ?? confirmPromote.username}</strong>
            （{confirmPromote.email}）提升为 {accountLabel} 的 Account Admin？
          </p>
          <ul className="admin-dialog-list" data-testid="platform-promote-impact">
            <li>仅支持 <code>user → account_admin</code> 方向（05 §12.6，AC⑥）。</li>
            <li>角色即时生效，无需目标用户重新登录（04 §10.6）。</li>
            <li>平台不会创建或提升新的 Platform Super Admin（03 §9.2，范围 Out）。</li>
          </ul>
          <p className="profile-hint">确认后立即生效，无需再次输入密码。</p>
          <div className="profile-actions">
            <button type="button" onClick={() => setConfirmPromote(null)} disabled={promoting}>
              取消
            </button>
            <button
              type="button"
              className="danger-button"
              onClick={handleConfirmPromote}
              disabled={promoting}
              data-testid="platform-user-promote-confirm"
            >
              {promoting ? "提升中…" : "确认提升"}
            </button>
          </div>
        </div>
      ) : null}

      {/* ── 平台级重置确认弹窗（06 §13.9：退出设备提示；PSA 永不显示，AC⑥）── */}
      {confirmReset ? (
        <div className="confirm-dialog" role="dialog" aria-label="重置密码确认">
          <h3>重置密码</h3>
          <p>
            确认重置 <strong>{confirmReset.display_name ?? confirmReset.username}</strong>
            （{confirmReset.email}）的密码？
          </p>
          <ul className="admin-dialog-list">
            <li>将使该用户所有网页登录设备退出；</li>
            <li>不会删除 OpenViking 对话和记忆；</li>
            <li>不会撤销该用户的 API Key。</li>
          </ul>
          <div className="profile-actions">
            <button type="button" onClick={() => setConfirmReset(null)} disabled={resetting}>
              取消
            </button>
            <button
              type="button"
              className="danger-button"
              onClick={handleConfirmReset}
              disabled={resetting}
              data-testid="platform-user-reset-confirm"
            >
              {resetting ? "重置中…" : "确认重置"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
