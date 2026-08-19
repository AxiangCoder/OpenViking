/**
 * /admin/users 用户管理页（13 §85，14 号计划 §98.6，P4-E1 AC①-⑥⑧）。
 *
 * - Account 固定来自登录 Session（/auth/me）：页头展示固定 Account 上下文，
 *   无 Account 切换入口；路径中的用户只作为 Subject（05 §12.6，AC①）；
 * - 列表：code/显示名/邮箱/状态/角色/最近登录/创建时间；状态筛选为前端对
 *   已加载行过滤（05 §12.6 列表 API 无 status 参数，85.1）；cursor 分页；
 * - 创建：邮箱（全局唯一）+ code（Account 内唯一、创建后不可改）+ 显示名；
 *   表单无角色选择，固定 `user`（AC③）；成功后一次性初始密码弹窗（AC②），
 *   并提示「创建者可能长期知晓密码」的交接限制（06 §13.9，AC⑧）；
 * - 禁用/启用：禁用确认弹窗提示会话与 API Key 立即失效、对话与记忆不受影响
 *   （85.3，AC⑤）；启用为 PATCH status=active（05 §12.6）；
 * - 分级重置：仅对严格低级别目标显示按钮（actor_role_rank > target_role_rank，
 *   03 §8.3、85.4，AC④），确认弹窗提示设备退出/对话记忆保留/Key 不撤销，
 *   成功后一次性展示新密码（AC④⑧）；
 * - 删除：先取 deletion-preview 展示影响范围（名称/Account/预计影响/30 天
 *   回收期），只确认/取消，不重输密码（85.5、06 §14.5，AC⑥）；
 * - 按钮按 Permission 隐藏（06 §13.4）；前端 Guard 与按钮隐藏不是安全边界，
 *   后端每次请求重复鉴权（AC①）；
 * - 加载失败保留页面框架，展示 Request ID 与重试，不展示底层异常（13 §84.3）。
 */

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { isPlatformError } from "@/lib/platform-client";
import { useMe } from "@/features/auth/useAuth";
import { canPerform, canResetTargetPassword } from "@/lib/permissions";
import OneTimeSecret from "@/components/ui/OneTimeSecret";
import {
  ADMIN_USER_STATUS_LABELS,
  ADMIN_USER_STATUSES,
  adminRoleLabel,
  adminUserErrorMessage,
  createAdminUser,
  deleteAdminUser,
  disableAdminUser,
  enableAdminUser,
  fetchAdminUserDeletionPreview,
  formatIsoDateTime,
  listAdminUsers,
  newCreateUserIdempotencyKey,
  resetAdminUserPassword,
  type AdminPasswordResetResult,
  type AdminUserDeletionPreview,
  type AdminUserRecord,
  type AdminUserStatus,
  type CreatedAdminUser,
} from "@/features/iam/admin-users";

type StatusFilter = "all" | AdminUserStatus;

interface DeleteTargetState {
  user: AdminUserRecord;
  preview: AdminUserDeletionPreview | null;
  previewError: string | null;
}

interface ResetResultState {
  user: AdminUserRecord;
  result: AdminPasswordResetResult;
}

export default function AdminUsersPage() {
  const me = useMe();
  const account = me?.account ?? null;
  const accountLabel = account?.name ?? account?.code ?? account?.id ?? "—";

  // ── 列表 ──
  const [users, setUsers] = useState<AdminUserRecord[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(
    null,
  );
  const [loadingMore, setLoadingMore] = useState(false);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  // ── 创建 ──
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createdUser, setCreatedUser] = useState<CreatedAdminUser | null>(null);

  // ── 禁用/启用 ──
  const [confirmDisable, setConfirmDisable] = useState<AdminUserRecord | null>(null);
  const [disabling, setDisabling] = useState(false);

  // ── 分级重置 ──
  const [confirmReset, setConfirmReset] = useState<AdminUserRecord | null>(null);
  const [resetting, setResetting] = useState(false);
  const [resetResult, setResetResult] = useState<ResetResultState | null>(null);

  // ── 删除 ──
  const [deleteTarget, setDeleteTarget] = useState<DeleteTargetState | null>(null);
  const [deleting, setDeleting] = useState(false);

  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoadError(null);
    setUsers(null);
    setNextCursor(null);
    listAdminUsers()
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
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleLoadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setActionError(null);
    try {
      const page = await listAdminUsers(nextCursor);
      setUsers((prev) => [...(prev ?? []), ...page.items]);
      setNextCursor(page.next_cursor);
    } catch (error) {
      setActionError(adminUserErrorMessage(error, "加载更多失败，请稍后重试"));
    } finally {
      setLoadingMore(false);
    }
  }

  const filteredUsers = useMemo(() => {
    if (!users) return [];
    return statusFilter === "all" ? users : users.filter((u) => u.status === statusFilter);
  }, [users, statusFilter]);

  // ── 创建 ──

  function handleCreateSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (creating) return;
    setCreating(true);
    setCreateError(null);
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    const username = String(form.get("username") ?? "").trim();
    const displayName = String(form.get("display_name") ?? "").trim() || null;
    createAdminUser({ email, username, display_name: displayName }, newCreateUserIdempotencyKey())
      .then((created) => {
        // 初始密码仅进入内存态一次性视图（AC②）
        setCreatedUser(created);
        setCreateOpen(false);
        load();
      })
      .catch((error) => {
        setCreateError(adminUserErrorMessage(error, "创建失败，请稍后重试"));
      })
      .finally(() => setCreating(false));
  }

  // ── 禁用/启用 ──

  function handleConfirmDisable() {
    if (!confirmDisable || disabling) return;
    setDisabling(true);
    setActionError(null);
    disableAdminUser(confirmDisable.id)
      .then((result) => {
        setNotice(
          `已禁用 ${confirmDisable.display_name ?? confirmDisable.username}：撤销 ${result.sessions_revoked} 个登录会话、${result.keys_revoked} 个 API Key。`,
        );
        setConfirmDisable(null);
        load();
      })
      .catch((error) => {
        setActionError(adminUserErrorMessage(error, "禁用失败，请稍后重试"));
      })
      .finally(() => setDisabling(false));
  }

  function handleEnable(user: AdminUserRecord) {
    setActionError(null);
    enableAdminUser(user.id)
      .then(() => {
        setNotice(`已启用 ${user.display_name ?? user.username}。`);
        load();
      })
      .catch((error) => {
        setActionError(adminUserErrorMessage(error, "启用失败，请稍后重试"));
      });
  }

  // ── 分级重置 ──

  function handleConfirmReset() {
    if (!confirmReset || resetting) return;
    setResetting(true);
    setActionError(null);
    resetAdminUserPassword(confirmReset.id)
      .then((result) => {
        // 新密码仅进入内存态一次性视图（AC④）
        setResetResult({ user: confirmReset, result });
        setConfirmReset(null);
      })
      .catch((error) => {
        setActionError(adminUserErrorMessage(error, "重置失败，请稍后重试"));
      })
      .finally(() => setResetting(false));
  }

  // ── 删除 ──

  function handleDeleteClick(user: AdminUserRecord) {
    setActionError(null);
    setDeleteTarget({ user, preview: null, previewError: null });
    fetchAdminUserDeletionPreview(user.id)
      .then((preview) => setDeleteTarget({ user, preview, previewError: null }))
      .catch((error) =>
        setDeleteTarget({
          user,
          preview: null,
          previewError: adminUserErrorMessage(error, "无法获取删除影响范围"),
        }),
      );
  }

  function handleConfirmDelete() {
    if (!deleteTarget?.preview || deleting) return;
    const user = deleteTarget.user;
    setDeleting(true);
    setActionError(null);
    deleteAdminUser(user.id)
      .then((result) => {
        setNotice(
          `已删除 ${user.display_name ?? user.username}：进入 30 天回收期，恢复截止 ${formatIsoDateTime(result.restore_until)}。`,
        );
        setDeleteTarget(null);
        load();
      })
      .catch((error) => {
        setActionError(adminUserErrorMessage(error, "删除失败，请稍后重试"));
      })
      .finally(() => setDeleting(false));
  }

  // ── 一次性凭证视图（AC②④：离开/刷新不可再取，仅内存态）──

  if (createdUser) {
    return (
      <OneTimeSecret
        title="用户已创建"
        testIdPrefix="admin-user-created"
        secret={createdUser.initial_password}
        subjectLine={`用户：${createdUser.display_name ?? createdUser.username}（${createdUser.email}）｜ 角色：user ｜ 所属 Account：${accountLabel}`}
        onClose={() => setCreatedUser(null)}
      />
    );
  }

  if (resetResult) {
    return (
      <OneTimeSecret
        title="密码已重置"
        testIdPrefix="admin-user-reset"
        secret={resetResult.result.new_password}
        subjectLine={`用户：${resetResult.user.display_name ?? resetResult.user.username}（${resetResult.user.email}）｜ 角色：${adminRoleLabel(resetResult.user.role)} ｜ 所属 Account：${accountLabel}｜ 已撤销 ${resetResult.result.sessions_revoked} 个登录会话`}
        onClose={() => setResetResult(null)}
      />
    );
  }

  return (
    <div className="admin-page" data-testid="admin-users-page">
      <div className="admin-page-header">
        <div>
          <h2>用户管理</h2>
          <p className="admin-account-context" data-testid="admin-fixed-account">
            操作固定作用于当前登录 Account：
            <strong>{accountLabel}</strong>
            （05 §12.6，无 Account 切换入口）
          </p>
        </div>
        {canPerform(me, "user.create") ? (
          <button
            type="button"
            className="primary-button"
            onClick={() => {
              setCreateError(null);
              setCreateOpen(true);
            }}
            data-testid="admin-user-create-open"
          >
            创建用户
          </button>
        ) : null}
      </div>

      {loadError ? (
        <div className="admin-load-error" role="alert" data-testid="admin-users-load-error">
          <p className="login-error">{loadError.message}</p>
          {loadError.requestId ? (
            <p className="error-page-request-id">
              请求 ID：<code>{loadError.requestId}</code>
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="button" onClick={load} data-testid="admin-users-retry">
              重试
            </button>
          </div>
        </div>
      ) : null}

      {users == null && !loadError ? (
        <p className="profile-hint" data-testid="admin-users-loading">
          加载中…
        </p>
      ) : null}

      {users != null && users.length === 0 ? (
        <div className="empty-state" data-testid="admin-users-empty">
          该 Account 暂无用户。
        </div>
      ) : null}

      {users != null && users.length > 0 ? (
        <section className="card" data-testid="admin-users-list">
          <div className="admin-filter-bar" data-testid="admin-user-filter">
            <label htmlFor="user-status-filter">状态筛选</label>
            <select
              id="user-status-filter"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
              data-testid="admin-user-status-filter"
            >
              <option value="all">全部</option>
              {ADMIN_USER_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {ADMIN_USER_STATUS_LABELS[s]}
                </option>
              ))}
            </select>
            <span className="profile-hint">
              筛选作用于已加载列表（05 §12.6 列表接口无状态参数）
            </span>
          </div>

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
              {filteredUsers.map((user) => (
                <tr key={user.id} data-testid={`admin-user-row-${user.id}`}>
                  <td>
                    <code>{user.username}</code>
                  </td>
                  <td>{user.display_name ?? "—"}</td>
                  <td>{user.email}</td>
                  <td>
                    <span className={`status-badge ${user.status}`} data-testid={`admin-user-status-${user.id}`}>
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
                      {user.status === "active" && canPerform(me, "user.disable") ? (
                        <button
                          type="button"
                          onClick={() => setConfirmDisable(user)}
                          data-testid={`admin-user-disable-${user.id}`}
                        >
                          禁用
                        </button>
                      ) : null}
                      {user.status === "disabled" && canPerform(me, "user.update") ? (
                        <button
                          type="button"
                          onClick={() => handleEnable(user)}
                          data-testid={`admin-user-enable-${user.id}`}
                        >
                          启用
                        </button>
                      ) : null}
                      {canPerform(me, "user.password.reset.account") &&
                      canResetTargetPassword(me?.roles, user.role ?? undefined) ? (
                        <button
                          type="button"
                          onClick={() => setConfirmReset(user)}
                          data-testid={`admin-user-reset-${user.id}`}
                        >
                          重置密码
                        </button>
                      ) : null}
                      {(user.status === "active" || user.status === "disabled") &&
                      canPerform(me, "user.delete") ? (
                        <button
                          type="button"
                          className="danger-button"
                          onClick={() => handleDeleteClick(user)}
                          data-testid={`admin-user-delete-${user.id}`}
                        >
                          删除
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
                data-testid="admin-users-load-more"
              >
                {loadingMore ? "加载中…" : "加载更多"}
              </button>
            </div>
          ) : null}
        </section>
      ) : null}

      {actionError ? (
        <p className="login-error" role="alert" data-testid="admin-users-action-error">
          {actionError}
        </p>
      ) : null}
      {notice ? (
        <p className="profile-success" role="status" data-testid="admin-users-notice">
          {notice}
        </p>
      ) : null}

      {/* ── 创建用户弹窗（85.2：无角色选择，固定 user，AC③）── */}
      {createOpen ? (
        <div className="confirm-dialog" role="dialog" aria-label="创建用户">
          <h3>创建用户</h3>
          <p className="profile-hint">
            角色固定为 <code>user</code>（创建时不可选择 account_admin；Account Admin 的创建与
            提升只能由 Platform Super Admin 执行，03 §8.3）。
          </p>
          <form className="admin-form" onSubmit={handleCreateSubmit} data-testid="admin-user-create-form">
            <label className="login-field">
              <span>邮箱（必填，全局唯一）</span>
              <input
                type="email"
                name="email"
                required
                maxLength={320}
                disabled={creating}
                data-testid="admin-user-create-email"
              />
            </label>
            <label className="login-field">
              <span>code（必填，Account 内唯一，创建后不可修改）</span>
              <input
                type="text"
                name="username"
                required
                maxLength={128}
                disabled={creating}
                data-testid="admin-user-create-username"
              />
            </label>
            <label className="login-field">
              <span>显示名（可选）</span>
              <input
                type="text"
                name="display_name"
                maxLength={128}
                disabled={creating}
                data-testid="admin-user-create-display-name"
              />
            </label>
            {createError ? (
              <p className="login-error" role="alert" data-testid="admin-user-create-error">
                {createError}
              </p>
            ) : null}
            <div className="profile-actions">
              <button type="submit" className="primary-button" disabled={creating} data-testid="admin-user-create-submit">
                {creating ? "创建中…" : "创建"}
              </button>
              <button
                type="button"
                onClick={() => setCreateOpen(false)}
                disabled={creating}
                data-testid="admin-user-create-cancel"
              >
                取消
              </button>
            </div>
          </form>
        </div>
      ) : null}

      {/* ── 禁用确认弹窗（85.3，AC⑤）── */}
      {confirmDisable ? (
        <div className="confirm-dialog" role="dialog" aria-label="禁用用户确认">
          <h3>禁用用户</h3>
          <p>
            确认禁用 <strong>{confirmDisable.display_name ?? confirmDisable.username}</strong>
            （{confirmDisable.email}）？
          </p>
          <ul className="admin-dialog-list">
            <li>该用户所有登录会话立即失效；</li>
            <li>该用户全部 API Key 立即拒绝；</li>
            <li>OpenViking 对话与记忆不受影响。</li>
          </ul>
          <div className="profile-actions">
            <button type="button" onClick={() => setConfirmDisable(null)} disabled={disabling}>
              取消
            </button>
            <button
              type="button"
              className="danger-button"
              onClick={handleConfirmDisable}
              disabled={disabling}
              data-testid="admin-user-disable-confirm"
            >
              {disabling ? "禁用中…" : "确认禁用"}
            </button>
          </div>
        </div>
      ) : null}

      {/* ── 重置密码确认弹窗（85.4，AC④）── */}
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
              data-testid="admin-user-reset-confirm"
            >
              {resetting ? "重置中…" : "确认重置"}
            </button>
          </div>
        </div>
      ) : null}

      {/* ── 删除确认弹窗（85.5：展示影响范围，只确认/取消，AC⑥）── */}
      {deleteTarget ? (
        <div className="confirm-dialog" role="dialog" aria-label="删除用户确认">
          <h3>删除用户</h3>
          {deleteTarget.preview ? (
            <>
              <ul className="admin-dialog-list" data-testid="admin-user-delete-impact">
                <li>目标：{deleteTarget.preview.target_name}（{deleteTarget.user.email}）</li>
                <li>所属 Account：{accountLabel}</li>
                <li>
                  预计影响：{deleteTarget.preview.impacted.login_sessions ?? 0} 个登录会话、
                  {deleteTarget.preview.impacted.api_keys ?? 0} 个 API Key
                </li>
                <li>
                  恢复：删除后进入 30 天回收期
                  {deleteTarget.preview.recoverable ? "，可恢复" : "，不可恢复"}，期满由系统物理清理；
                  期间该用户禁止登录，相关会话与 API Key 不可再用
                </li>
              </ul>
              <p className="profile-hint">确认后立即删除，无需再次输入密码或 Account 名称。</p>
            </>
          ) : deleteTarget.previewError ? (
            <p className="login-error" role="alert" data-testid="admin-user-delete-error">
              {deleteTarget.previewError}
            </p>
          ) : (
            <p className="profile-hint">正在获取删除影响范围…</p>
          )}
          <div className="profile-actions">
            <button type="button" onClick={() => setDeleteTarget(null)} disabled={deleting}>
              取消
            </button>
            <button
              type="button"
              className="danger-button"
              onClick={handleConfirmDelete}
              disabled={deleting || !deleteTarget.preview}
              data-testid="admin-user-delete-confirm"
            >
              {deleting ? "删除中…" : "确认删除"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
