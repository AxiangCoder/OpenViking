/**
 * /platform/accounts 平台 Account 管理页（13 §89.2，05 §12.6，14 号计划 §98.9，P4-E4）。
 *
 * - 列表：名称/code/状态/成员数/创建时间；状态筛选为前端对已加载行过滤
 *   （05 §12.6 列表接口无 status 参数，同 89.2）；cursor 分页；
 *   `suspended` 状态只展示不操作（v0.1 无暂停/恢复产品端点，AC③）；
 * - 创建 Account：表单含 Account 名称/code 与首位 Account Admin 邮箱/code/显示名
 *   （后端契约要求首位 Admin code，05 §12.6）；成功后一次性展示首位 Admin 初始
 *   密码（OneTimeSecret，AC④）；页面无创建/重置 PSA 入口（范围 Out，03 §9.2）；
 * - 删除：先取 deletion-preview 展示影响范围（成员数/共享内容/30 天恢复截止），
 *   只确认/取消（89.2、06 §14.5）；`suspended` 不提供删除操作；
 * - Provisioning 重试：仅 `failed` 状态显示「重试开通」（AC⑤），幂等调用
 *   （P2-E1：失败目标回退 provisioning，active 返回 PROVISIONING_NOT_RETRYABLE）；
 * - 选择目标 Account 是管理浏览（不改变 Actor），页面明确 Account 上下文（AC②）；
 * - 按钮按 Permission 隐藏（06 §13.4）；Guard 与按钮隐藏不是安全边界（AC①）。
 */

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { useMe } from "@/features/auth/useAuth";
import { canPerform } from "@/lib/permissions";
import OneTimeSecret from "@/components/ui/OneTimeSecret";
import {
  formatIsoDateTime,
  type AdminUserDeletionPreview,
} from "@/features/iam/admin-users";
import {
  createPlatformAccount,
  deletePlatformAccount,
  fetchPlatformAccountDeletionPreview,
  listPlatformAccounts,
  platformAccountStatusLabel,
  platformErrorMessage,
  PLATFORM_ACCOUNT_STATUSES,
  retryPlatformProvisioning,
  type PlatformAccount,
  type PlatformAccountCreateResult,
  type PlatformAccountStatus,
} from "@/features/iam/platform";

type StatusFilter = "all" | PlatformAccountStatus;

interface DeleteTargetState {
  account: PlatformAccount;
  preview: AdminUserDeletionPreview | null;
  previewError: string | null;
}

export default function PlatformAccountsPage() {
  const me = useMe();
  const actorLabel = me?.user.display_name ?? me?.user.email ?? me?.user.id ?? "当前管理员";

  // ── 列表 ──
  const [accounts, setAccounts] = useState<PlatformAccount[] | null>(null);
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
  const [createdResult, setCreatedResult] = useState<PlatformAccountCreateResult | null>(null);

  // ── 删除 ──
  const [deleteTarget, setDeleteTarget] = useState<DeleteTargetState | null>(null);
  const [deleting, setDeleting] = useState(false);

  // ── Provisioning 重试 ──
  const [retryingAccountId, setRetryingAccountId] = useState<string | null>(null);

  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const canManage = canPerform(me, "account.manage.platform");
  const canDelete = canPerform(me, "account.delete");

  const load = useCallback(() => {
    setLoadError(null);
    setAccounts(null);
    setNextCursor(null);
    listPlatformAccounts()
      .then((page) => {
        setAccounts(page.items);
        setNextCursor(page.next_cursor);
      })
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "无法加载 Account 列表" : "无法加载 Account 列表",
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
      const page = await listPlatformAccounts(nextCursor);
      setAccounts((prev) => [...(prev ?? []), ...page.items]);
      setNextCursor(page.next_cursor);
    } catch (error) {
      setActionError(platformErrorMessage(error, "加载更多失败，请稍后重试"));
    } finally {
      setLoadingMore(false);
    }
  }

  const filteredAccounts = useMemo(() => {
    if (!accounts) return [];
    return statusFilter === "all"
      ? accounts
      : accounts.filter((a) => a.status === statusFilter);
  }, [accounts, statusFilter]);

  // ── 创建（89.2：Account 名称/code + 首位 Admin 邮箱/显示名；无角色选择，AC④）──

  function handleCreateSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (creating) return;
    setCreating(true);
    setCreateError(null);
    const form = new FormData(event.currentTarget);
    const accountName = String(form.get("account_name") ?? "").trim();
    const accountCode = String(form.get("account_code") ?? "").trim();
    const adminEmail = String(form.get("admin_email") ?? "").trim();
    const adminUsername = String(form.get("admin_username") ?? "").trim();
    const adminDisplayName = String(form.get("admin_display_name") ?? "").trim() || null;
    createPlatformAccount({
      account_name: accountName,
      account_code: accountCode,
      admin_email: adminEmail,
      admin_username: adminUsername,
      admin_display_name: adminDisplayName,
    })
      .then((result) => {
        // 初始密码仅进入内存态一次性视图（AC④，06 §13.9）
        setCreatedResult(result);
        setCreateOpen(false);
        load();
      })
      .catch((error) => {
        setCreateError(platformErrorMessage(error, "创建失败，请稍后重试"));
      })
      .finally(() => setCreating(false));
  }

  // ── Provisioning 重试（仅 failed 显示；幂等，AC⑤）──

  function handleRetryProvisioning(account: PlatformAccount) {
    if (retryingAccountId) return;
    setRetryingAccountId(account.id);
    setActionError(null);
    retryPlatformProvisioning(account.id)
      .then((result) => {
        setNotice(
          `已重新提交 ${account.name ?? account.code} 的开通流程（状态 ${result.status}，重试 ${result.retried_events} 个事件）。`,
        );
        load();
      })
      .catch((error) => {
        setActionError(platformErrorMessage(error, "重试开通失败，请稍后重试"));
      })
      .finally(() => setRetryingAccountId(null));
  }

  // ── 删除（89.2：影响范围预览 + 30 天回收期，只确认/取消）──

  function handleDeleteClick(account: PlatformAccount) {
    setActionError(null);
    setDeleteTarget({ account, preview: null, previewError: null });
    fetchPlatformAccountDeletionPreview(account.id)
      .then((preview) => setDeleteTarget({ account, preview, previewError: null }))
      .catch((error) =>
        setDeleteTarget({
          account,
          preview: null,
          previewError: platformErrorMessage(error, "无法获取删除影响范围"),
        }),
      );
  }

  function handleConfirmDelete() {
    if (!deleteTarget?.preview || deleting) return;
    const account = deleteTarget.account;
    setDeleting(true);
    setActionError(null);
    deletePlatformAccount(account.id)
      .then((result) => {
        setNotice(
          `已删除 ${account.name ?? account.code}：进入 30 天回收期，恢复截止 ${formatIsoDateTime(result.restore_until)}。`,
        );
        setDeleteTarget(null);
        load();
      })
      .catch((error) => {
        setActionError(platformErrorMessage(error, "删除失败，请稍后重试"));
      })
      .finally(() => setDeleting(false));
  }

  // ── 一次性凭证视图（AC④：离开/刷新不可再取，仅内存态）──

  if (createdResult) {
    return (
      <OneTimeSecret
        title="Account 已创建"
        testIdPrefix="platform-account-created"
        secret={createdResult.first_admin.initial_password}
        subjectLine={`Account：${createdResult.account.name ?? createdResult.account.code}（${createdResult.account.code}）｜ 首位 Admin：${createdResult.first_admin.username}（${createdResult.first_admin.email}）｜ 角色：${createdResult.first_admin.role}`}
        onClose={() => setCreatedResult(null)}
      >
        <p className="profile-hint" data-testid="platform-account-created-role-note">
          Account Admin 的创建与提升只能由 Platform Super Admin 执行；本页不提供创建或重置
          另一个 Platform Super Admin 的入口（03 §9.2，范围 Out）。
        </p>
      </OneTimeSecret>
    );
  }

  return (
    <div className="admin-page" data-testid="platform-accounts-page">
      <div className="admin-page-header">
        <div>
          <h2>Accounts</h2>
          <p className="admin-account-context" data-testid="platform-accounts-actor">
            以 <strong>{actorLabel}</strong>（Platform Super Admin）身份管理平台；
            选择目标 Account 是管理浏览，不改变登录者身份（06 §13.2，AC②）
          </p>
        </div>
        {canManage ? (
          <button
            type="button"
            className="primary-button"
            onClick={() => {
              setCreateError(null);
              setCreateOpen(true);
            }}
            data-testid="platform-account-create-open"
          >
            创建 Account
          </button>
        ) : null}
      </div>

      {loadError ? (
        <div className="admin-load-error" role="alert" data-testid="platform-accounts-load-error">
          <p className="login-error">{loadError.message}</p>
          {loadError.requestId ? (
            <p className="error-page-request-id">
              请求 ID：<code>{loadError.requestId}</code>
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="button" onClick={load} data-testid="platform-accounts-retry">
              重试
            </button>
          </div>
        </div>
      ) : null}

      {accounts == null && !loadError ? (
        <p className="profile-hint" data-testid="platform-accounts-loading">
          加载中…
        </p>
      ) : null}

      {accounts != null && accounts.length === 0 ? (
        <div className="empty-state" data-testid="platform-accounts-empty">
          平台暂无 Account。
        </div>
      ) : null}

      {accounts != null && accounts.length > 0 ? (
        <section className="card" data-testid="platform-accounts-list">
          <div className="admin-filter-bar" data-testid="platform-account-filter">
            <label htmlFor="account-status-filter">状态筛选</label>
            <select
              id="account-status-filter"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
              data-testid="platform-account-status-filter"
            >
              <option value="all">全部</option>
              {PLATFORM_ACCOUNT_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {platformAccountStatusLabel(s)}
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
                <th>名称</th>
                <th>code</th>
                <th>状态</th>
                <th>成员数</th>
                <th>创建时间</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {filteredAccounts.map((account) => (
                <tr key={account.id} data-testid={`platform-account-row-${account.id}`}>
                  <td>
                    <Link
                      to={`/platform/accounts/${account.id}/users`}
                      data-testid={`platform-account-name-${account.id}`}
                    >
                      {account.name ?? "（未命名）"}
                    </Link>
                  </td>
                  <td>
                    <code>{account.code}</code>
                  </td>
                  <td>
                    <span className={`status-badge ${account.status}`} data-testid={`platform-account-status-${account.id}`}>
                      {platformAccountStatusLabel(account.status)}
                    </span>
                  </td>
                  <td data-testid={`platform-account-members-${account.id}`}>
                    {account.member_count != null ? account.member_count : "—"}
                  </td>
                  <td>{formatIsoDateTime(account.created_at)}</td>
                  <td>
                    <div className="row-actions">
                      {canPerform(me, "user.read.platform") ? (
                        <Link
                          to={`/platform/accounts/${account.id}/users`}
                          data-testid={`platform-account-users-${account.id}`}
                        >
                          用户
                        </Link>
                      ) : null}
                      {canPerform(me, "resource.account_shared.read.platform") ? (
                        <Link
                          to={`/platform/accounts/${account.id}/resources`}
                          data-testid={`platform-account-resources-${account.id}`}
                        >
                          共享 Resource
                        </Link>
                      ) : null}
                      {canPerform(me, "skill.account_shared.read.platform") ? (
                        <Link
                          to={`/platform/accounts/${account.id}/skills`}
                          data-testid={`platform-account-skills-${account.id}`}
                        >
                          共享 Skill
                        </Link>
                      ) : null}
                      {account.status === "failed" && canManage ? (
                        <button
                          type="button"
                          onClick={() => handleRetryProvisioning(account)}
                          disabled={retryingAccountId != null}
                          data-testid={`platform-account-retry-${account.id}`}
                        >
                          {retryingAccountId === account.id ? "重试中…" : "重试开通"}
                        </button>
                      ) : null}
                      {account.status === "active" && canDelete ? (
                        <button
                          type="button"
                          className="danger-button"
                          onClick={() => handleDeleteClick(account)}
                          data-testid={`platform-account-delete-${account.id}`}
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
                data-testid="platform-accounts-load-more"
              >
                {loadingMore ? "加载中…" : "加载更多"}
              </button>
            </div>
          ) : null}
        </section>
      ) : null}

      {actionError ? (
        <p className="login-error" role="alert" data-testid="platform-accounts-action-error">
          {actionError}
        </p>
      ) : null}
      {notice ? (
        <p className="profile-success" role="status" data-testid="platform-accounts-notice">
          {notice}
        </p>
      ) : null}

      {/* ── 创建 Account 弹窗（89.2：首位 Admin 邮箱/显示名；无创建 PSA 入口，AC④）── */}
      {createOpen ? (
        <div className="confirm-dialog" role="dialog" aria-label="创建 Account">
          <h3>创建 Account</h3>
          <p className="profile-hint" data-testid="platform-account-create-role-note">
            首位 Account Admin 由本表单创建；页面不提供创建或重置另一个 Platform Super Admin
            的入口（03 §9.2，范围 Out）。
          </p>
          <form className="admin-form" onSubmit={handleCreateSubmit} data-testid="platform-account-create-form">
            <label className="login-field">
              <span>Account 名称（必填）</span>
              <input
                type="text"
                name="account_name"
                required
                maxLength={128}
                disabled={creating}
                data-testid="platform-account-create-name"
              />
            </label>
            <label className="login-field">
              <span>Account code（必填，平台全局唯一，创建后不可修改）</span>
              <input
                type="text"
                name="account_code"
                required
                maxLength={64}
                disabled={creating}
                data-testid="platform-account-create-code"
              />
            </label>
            <label className="login-field">
              <span>首位 Admin 邮箱（必填，全局唯一）</span>
              <input
                type="email"
                name="admin_email"
                required
                maxLength={320}
                disabled={creating}
                data-testid="platform-account-create-admin-email"
              />
            </label>
            <label className="login-field">
              <span>首位 Admin code（必填，Account 内唯一，创建后不可修改）</span>
              <input
                type="text"
                name="admin_username"
                required
                maxLength={128}
                disabled={creating}
                data-testid="platform-account-create-admin-username"
              />
            </label>
            <label className="login-field">
              <span>首位 Admin 显示名（可选）</span>
              <input
                type="text"
                name="admin_display_name"
                maxLength={128}
                disabled={creating}
                data-testid="platform-account-create-admin-display-name"
              />
            </label>
            {createError ? (
              <p className="login-error" role="alert" data-testid="platform-account-create-error">
                {createError}
              </p>
            ) : null}
            <div className="profile-actions">
              <button
                type="submit"
                className="primary-button"
                disabled={creating}
                data-testid="platform-account-create-submit"
              >
                {creating ? "创建中…" : "创建"}
              </button>
              <button
                type="button"
                onClick={() => setCreateOpen(false)}
                disabled={creating}
                data-testid="platform-account-create-cancel"
              >
                取消
              </button>
            </div>
          </form>
        </div>
      ) : null}

      {/* ── 删除确认弹窗（89.2：影响范围 + 30 天恢复截止，只确认/取消）── */}
      {deleteTarget ? (
        <div className="confirm-dialog" role="dialog" aria-label="删除 Account 确认">
          <h3>删除 Account</h3>
          {deleteTarget.preview ? (
            <>
              <ul className="admin-dialog-list" data-testid="platform-account-delete-impact">
                <li>目标：{deleteTarget.preview.target_name}（{deleteTarget.account.code}）</li>
                <li>
                  预计影响：{deleteTarget.preview.impacted.users ?? 0} 个成员、
                  {deleteTarget.preview.impacted.resources ?? 0} 个 Resource、
                  {deleteTarget.preview.impacted.skills ?? 0} 个 Skill
                </li>
                <li>
                  恢复：删除后进入 30 天回收期
                  {deleteTarget.preview.recoverable ? "，可恢复" : "，不可恢复"}，期满由系统物理清理；
                  期间该 Account 禁止登录，相关会话与 API Key 不可再用
                </li>
              </ul>
              <p className="profile-hint">确认后立即删除，无需再次输入密码或 Account 名称。</p>
            </>
          ) : deleteTarget.previewError ? (
            <p className="login-error" role="alert" data-testid="platform-account-delete-error">
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
              data-testid="platform-account-delete-confirm"
            >
              {deleting ? "删除中…" : "确认删除"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
