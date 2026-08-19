/**
 * /admin/users/{userId}/api-keys 成员 API Key 元数据查看与撤销
 * （13 §84.2、05 §12.6，14 号计划 §98.7，P4-E2 AC③⑥）。
 *
 * - 只展示元数据与掩码：名称/末四位掩码/状态/到期/最近使用/创建/撤销时间，
 *   无明文展示（AC③）；不提供代创建入口（05 §12.6：管理员不能代用户创建 Key）；
 * - 撤销（`credential.revoke.account`）：确认弹窗 + 幂等（KEY_NOT_FOUND 视为
 *   已撤销成功，复用个人 Key 撤销语义）；
 * - 越权动作（创建/取明文）UI 无入口（AC⑥）。
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { useMe } from "@/features/auth/useAuth";
import { canPerform } from "@/lib/permissions";
import { maskKey, isExpired } from "@/features/profile/api-keys";
import SubjectDataBanner from "@/components/subject/SubjectDataBanner";
import { formatIsoDateTime, listAdminUsers, type AdminUserRecord } from "@/features/iam/admin-users";
import {
  listMemberApiKeys,
  memberDataErrorMessage,
  revokeMemberApiKey,
  type MemberApiKeyRecord,
} from "@/features/iam/member-data";

const KEY_STATUS_LABELS: Record<string, string> = {
  active: "有效",
  revoked: "已撤销",
  expired: "已到期",
};

function keyStatusLabel(status: string): string {
  return KEY_STATUS_LABELS[status] ?? status;
}

export default function AdminUserApiKeysPage() {
  const params = useParams({ strict: false }) as Record<string, string>;
  const userId = params["userId"] ?? "";
  const me = useMe();
  const accountLabel = me?.account?.name ?? me?.account?.code ?? me?.account?.id ?? "—";
  const actorLabel = me?.user.display_name ?? me?.user.email ?? me?.user.id ?? "当前管理员";
  const canRevoke = canPerform(me, "credential.revoke.account");

  const [subject, setSubject] = useState<AdminUserRecord | null | undefined>(undefined);
  const [keys, setKeys] = useState<MemberApiKeyRecord[] | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<MemberApiKeyRecord | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoadError(null);
    setKeys(null);
    listAdminUsers()
      .then((page) => {
        setSubject(page.items.find((u) => u.id === userId) ?? null);
      })
      .catch(() => setSubject(null));
    listMemberApiKeys(userId)
      .then((page) => setKeys(page.items))
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "无法加载 API Key 元数据" : "无法加载 API Key 元数据",
          requestId: isPlatformError(error) ? error.requestId : null,
        });
      });
  }, [userId]);

  useEffect(() => {
    load();
  }, [load]);

  function handleConfirmRevoke() {
    if (!confirmRevoke || revoking) return;
    setRevoking(true);
    setActionError(null);
    revokeMemberApiKey(userId, confirmRevoke.id)
      .then(() => {
        setNotice(`已撤销 API Key「${confirmRevoke.name || confirmRevoke.key_last_four}」。`);
        setConfirmRevoke(null);
        load();
      })
      .catch((error) => {
        setActionError(memberDataErrorMessage(error, "撤销失败，请稍后重试"));
      })
      .finally(() => setRevoking(false));
  }

  const subjectLabel = subject
    ? `${subject.display_name ?? subject.username}（${subject.email}）`
    : `用户 ${userId}`;

  return (
    <div className="admin-page" data-testid="admin-user-api-keys-page">
      <SubjectDataBanner actorLabel={actorLabel} subjectLabel={subjectLabel} accountLabel={accountLabel} />
      <div className="profile-actions">
        <Link to="/admin/users" className="placeholder-back" data-testid="api-keys-back-users">
          ← 返回用户管理
        </Link>
      </div>

      {loadError ? (
        <div className="admin-load-error" role="alert" data-testid="api-keys-load-error">
          <p className="login-error">{loadError.message}</p>
          {loadError.requestId ? (
            <p className="error-page-request-id">
              请求 ID：<code>{loadError.requestId}</code>
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="button" onClick={load} data-testid="api-keys-retry">
              重试
            </button>
          </div>
        </div>
      ) : null}

      {keys == null && !loadError ? <p className="profile-hint">加载中…</p> : null}

      {keys != null && keys.length === 0 ? (
        <div className="empty-state" data-testid="api-keys-empty">
          该成员暂无 API Key。
        </div>
      ) : null}

      {keys != null && keys.length > 0 ? (
        <section className="card" data-testid="api-keys-list">
          <p className="profile-hint" data-testid="api-keys-readonly-note">
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
                <tr key={key.id} data-testid={`api-key-row-${key.id}`}>
                  <td data-testid={`api-key-name-${key.id}`}>{key.name}</td>
                  <td>
                    <code data-testid={`api-key-mask-${key.id}`}>{maskKey(key)}</code>
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
                          data-testid={`api-key-revoke-${key.id}`}
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
        <p className="login-error" role="alert" data-testid="api-keys-action-error">
          {actionError}
        </p>
      ) : null}
      {notice ? (
        <p className="profile-success" role="status" data-testid="api-keys-notice">
          {notice}
        </p>
      ) : null}

      {confirmRevoke ? (
        <div className="confirm-dialog" role="dialog" aria-label="撤销 API Key 确认" data-testid="api-key-revoke-dialog">
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
              data-testid="api-key-revoke-confirm"
            >
              {revoking ? "撤销中…" : "确认撤销"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
