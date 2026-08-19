/**
 * MCP OAuth 连接页（13 §83.1，P3-E2 AC⑤⑦）。
 *
 * - 列表：Client 名称、授权时间、最近使用时间、Scope、状态；
 * - 单独撤销：确认弹窗提示「将断开该客户端并使该客户端所有相关连接失效」，
 *   不影响其他 API Key 与其他客户端授权（83.1，AC⑦）。
 *
 * 后端 oauth-grants 端点由 P2-E6b 交付；未就绪时页面按契约展示加载/错误状态，
 * 联调收口见 AC⑤⑥。
 */

import { useCallback, useEffect, useState } from "react";
import { isPlatformError } from "@/lib/platform-client";
import {
  listOauthGrants,
  revokeOauthGrant,
  type OauthGrant,
} from "@/features/profile/connections";

export default function ProfileConnectionsPage() {
  const [grants, setGrants] = useState<OauthGrant[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmGrantId, setConfirmGrantId] = useState<string | null>(null);
  const [revoking, setRevoking] = useState(false);

  const load = useCallback(() => {
    setLoadError(null);
    listOauthGrants()
      .then((data) => setGrants(data))
      .catch((error) => {
        setLoadError(
          isPlatformError(error)
            ? error.message || "无法加载已授权客户端"
            : "无法加载已授权客户端",
        );
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function handleConfirmRevoke() {
    if (!confirmGrantId || revoking) return;
    setRevoking(true);
    setActionError(null);
    revokeOauthGrant(confirmGrantId)
      .then(() => {
        setNotice("已断开该客户端，其相关连接均已失效。");
        setConfirmGrantId(null);
        load();
      })
      .catch((error) => {
        setActionError(
          isPlatformError(error) ? error.message || "撤销失败，请稍后重试" : "撤销失败，请稍后重试",
        );
      })
      .finally(() => setRevoking(false));
  }

  return (
    <div className="profile-page" data-testid="connections-page">
      <section className="card profile-section" aria-label="已授权客户端">
        <h2>已授权客户端</h2>
        {loadError ? (
          <p className="login-error" role="alert">
            {loadError}
          </p>
        ) : null}
        {grants == null && !loadError ? <p>加载中…</p> : null}
        {grants != null && grants.length === 0 ? (
          <div className="empty-state" data-testid="connections-empty">
            尚无已授权的 MCP 客户端。
          </div>
        ) : null}
        {grants && grants.length > 0 ? (
          <table className="data-table" data-testid="connections-list">
            <thead>
              <tr>
                <th>客户端</th>
                <th>Scope</th>
                <th>状态</th>
                <th>授权时间</th>
                <th>最近使用</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {grants.map((grant) => (
                <tr key={grant.id} data-testid={`grant-row-${grant.id}`}>
                  <td>
                    <div>{grant.client_name ?? grant.client_id}</div>
                    <div className="profile-hint">Client ID: {grant.client_id}</div>
                  </td>
                  <td>
                    <code>{grant.scope}</code>
                  </td>
                  <td>{grant.status}</td>
                  <td>{grant.created_at ? new Date(grant.created_at).toLocaleString() : "—"}</td>
                  <td>{grant.last_used_at ? new Date(grant.last_used_at).toLocaleString() : "—"}</td>
                  <td>
                    <button
                      type="button"
                      className="danger-button"
                      onClick={() => setConfirmGrantId(grant.id)}
                      disabled={grant.status === "revoked"}
                      data-testid={`grant-revoke-${grant.id}`}
                    >
                      撤销
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        {confirmGrantId ? (
          <div className="confirm-dialog" role="dialog" aria-label="撤销客户端授权确认">
            <p>将断开该客户端，并使用户对该客户端的所有相关连接失效；不影响你的其他 API Key 与其他客户端授权。</p>
            <div className="profile-actions">
              <button type="button" onClick={() => setConfirmGrantId(null)} disabled={revoking}>
                取消
              </button>
              <button
                type="button"
                className="danger-button"
                onClick={handleConfirmRevoke}
                disabled={revoking}
                data-testid="grant-revoke-confirm"
              >
                {revoking ? "撤销中…" : "确认断开"}
              </button>
            </div>
          </div>
        ) : null}
        {actionError ? (
          <p className="login-error" role="alert" data-testid="connections-error">
            {actionError}
          </p>
        ) : null}
        {notice ? (
          <p className="profile-hint" role="status">
            {notice}
          </p>
        ) : null}
      </section>
    </div>
  );
}
