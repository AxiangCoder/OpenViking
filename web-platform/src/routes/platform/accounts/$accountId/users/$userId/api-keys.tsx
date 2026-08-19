/**
 * /platform/accounts/{accountId}/users/{userId}/api-keys
 * 平台级成员 API Key 元数据查看与撤销（13 §89.3、05 §12.6，P4-E4 AC②⑥）。
 *
 * - 只展示元数据与掩码：名称/末四位掩码/状态/到期/最近使用/创建/撤销时间，
 *   无明文展示（AC③）；不提供代创建入口（05 §12.6：管理员不能代用户创建 Key）；
 * - 撤销（`credential.revoke.platform`）：确认弹窗 + 幂等（KEY_NOT_FOUND 视为
 *   已撤销成功）；
 * - 越权动作（创建/取明文）UI 无入口（AC⑥）。
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { useMe } from "@/features/auth/useAuth";
import { canPerform } from "@/lib/permissions";
import { maskKey, isExpired } from "@/features/profile/api-keys";
import PlatformSubjectDataBanner from "@/components/subject/PlatformSubjectDataBanner";
import { formatIsoDateTime, type AdminUserRecord } from "@/features/iam/admin-users";
import {
  listPlatformAccountUsers,
  listPlatformAccounts,
  listPlatformMemberApiKeys,
  platformErrorMessage,
  revokePlatformMemberApiKey,
  type PlatformAccount,
} from "@/features/iam/platform";

const KEY_STATUS_LABELS: Record<string, string> = {
  active: "有效",
  revoked: "已撤销",
  expired: "已到期",
};

function keyStatusLabel(status: string): string {
  return KEY_STATUS_LABELS[status] ?? status;
}

export default function PlatformAccountUserApiKeysPage() {
  const params = useParams({ strict: false }) as Record<string, string>;
  const accountId = params["accountId"] ?? "";
  const userId = params["userId"] ?? "";
  const me = useMe();
  const actorLabel = me?.user.display_name ?? me?.user.email ?? me?.user.id ?? "当前管理员";
  const canRevoke = canPerform(me, "credential.revoke.platform");

  const [account, setAccount] = useState<PlatformAccount | null>(null);
  const [subject, setSubject] = useState<AdminUserRecord | null | undefined>(undefined);
  const [keys, setKeys] = useState<{ id: string; name: string; key_last_four: string; status: string; expires_at: string | null; last_used_at: string | null; created_at: string; revoked_at: string | null }[] | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<{
    id: string;
    name: string;
    key_last_four: string;
    status: string;
    expires_at: string | null;
    last_used_at: string | null;
    created_at: string;
    revoked_at: string | null;
  } | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const accountLabel = account?.name ?? account?.code ?? accountId ?? "—";

  const load = useCallback(() => {
    setLoadError(null);
    setKeys(null);
    listPlatformAccounts()
      .then((page) => {
        setAccount(page.items.find((a) => a.id === accountId) ?? null);
      })
      .catch(() => setAccount(null));
    listPlatformAccountUsers(accountId)
      .then((page) => {
        setSubject(page.items.find((u) => u.id === userId) ?? null);
      })
      .catch(() => setSubject(null));
    listPlatformMemberApiKeys(accountId, userId)
      .then((page) => setKeys(page.items))
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "无法加载 API Key 元数据" : "无法加载 API Key 元数据",
          requestId: isPlatformError(error) ? error.requestId : null,
        });
      });
  }, [accountId, userId]);

  useEffect(() => {
    load();
  }, [load]);

  function handleConfirmRevoke() {
    if (!confirmRevoke || revoking) return;
    setRevoking(true);
    setActionError(null);
    revokePlatformMemberApiKey(accountId, userId, confirmRevoke.id)
      .then(() => {
        setNotice(`已撤销 API Key「${confirmRevoke.name || confirmRevoke.key_last_four}」。`);
        setConfirmRevoke(null);
        load();
      })
      .catch((error) => {
        setActionError(platformErrorMessage(error, "撤销失败，请稍后重试"));
      })
      .finally(() => setRevoking(false));
  }

  const subjectLabel = subject
    ? `${subject.display_name ?? subject.username}（${subject.email}）`
    : `用户 ${userId}`;

  return (
    <div className="admin-page" data-testid="platform-user-api-keys-page">
      <PlatformSubjectDataBanner actorLabel={actorLabel} accountLabel={accountLabel} subjectLabel={subjectLabel} />
      <div className="profile-actions">
        <Link to={`/platform/accounts/${accountId}/users`} className="placeholder-back" data-testid="platform-api-keys-back-users">
          ← 返回 Account 用户
        </Link>
      </div>

      {loadError ? (
        <div className="admin-load-error" role="alert" data-testid="platform-api-keys-load-error">
          <p className="login-error">{loadError.message}</p>
          {loadError.requestId ? (
            <p className="error-page-request-id">
              请求 ID：<code>{loadError.requestId}</code>
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="button" onClick={load} data-testid="platform-api-keys-retry">
              重试
            </button>
          </div>
        </div>
      ) : null}

      {keys == null && !loadError ? <p className="profile-hint">加载中…</p> : null}

      {keys != null && keys.length === 0 ? (
        <div className="empty-state" data-testid="platform-api-keys-empty">
          该成员暂无 API Key。
        </div>
      ) : null}

      {keys != null && keys.length > 0 ? (
        <section className="card" data-testid="platform-api-keys-list">
          <p className="profile-hint" data-testid="platform-api-keys-readonly-note">
            仅展示元数据与掩码；管理员不能获取明文，也不能代用户创建 Key（05 §12.6，AC③）。
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>Key</th>
                <th>状态</th>
                <th>到期时间</th>
                <th>最近使用</th>
                <th>创建时间</th>
                <th>撤销时间</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => (
                <tr key={key.id} data-testid={`platform-api-key-row-${key.id}`}>
                  <td data-testid={`platform-api-key-name-${key.id}`}>{key.name}</td>
                  <td>
                    <code data-testid={`platform-api-key-mask-${key.id}`}>{maskKey(key)}</code>
                  </td>
                  <td>
                    <span className={`status-badge ${key.status}`}>
                      {keyStatusLabel(key.status)}
                      {key.status === "active" && isExpired(key) ? "（已到期）" : ""}
                    </span>
                  </td>
                  <td>{formatIsoDateTime(key.expires_at)}</td>
                  <td>{formatIsoDateTime(key.last_used_at)}</td>
                  <td>{formatIsoDateTime(key.created_at)}</td>
                  <td>{formatIsoDateTime(key.revoked_at)}</td>
                  <td>
                    <div className="row-actions">
                      {canRevoke && key.status === "active" && !isExpired(key) ? (
                        <button
                          type="button"
                          className="danger-button"
                          onClick={() => {
                            setActionError(null);
                            setConfirmRevoke(key);
                          }}
                          data-testid={`platform-api-key-revoke-${key.id}`}
                        >
                          撤销
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {actionError ? (
        <p className="login-error" role="alert" data-testid="platform-api-keys-action-error">
          {actionError}
        </p>
      ) : null}
      {notice ? (
        <p className="profile-success" role="status" data-testid="platform-api-keys-notice">
          {notice}
        </p>
      ) : null}

      {confirmRevoke ? (
        <div className="confirm-dialog" role="dialog" aria-label="撤销 API Key 确认" data-testid="platform-api-key-revoke-dialog">
          <h3>撤销 API Key</h3>
          <p>
            确认撤销该成员的 API Key <strong>{confirmRevoke.name}</strong>（
            {maskKey(confirmRevoke)}）？
          </p>
          <ul className="admin-dialog-list">
            <li>撤销后该 Key 立即失效，使用该 Key 的客户端将无法继续访问。</li>
            <li>该操作不可撤销；如需继续使用，需要创建新的 API Key（由成员本人创建）。</li>
            <li>管理员不能代用户创建 Key（05 §12.6，AC③）。</li>
          </ul>
          <div className="profile-actions">
            <button type="button" onClick={() => setConfirmRevoke(null)} disabled={revoking}>
              取消
            </button>
            <button
              type="button"
              className="danger-button"
              onClick={handleConfirmRevoke}
              disabled={revoking}
              data-testid="platform-api-key-revoke-confirm"
            >
              {revoking ? "撤销中…" : "确认撤销"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
