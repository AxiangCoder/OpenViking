/**
 * 个人 API Key 页（13 §82，P3-E2 AC③④⑦）。
 *
 * - 列表区：名称/末四位掩码/状态/到期/最近使用/创建时间，逐项撤销（82.2）；
 * - 创建区：名称必填 + 可选到期时间；成功后进入一次性明文展示视图（82.4）；
 * - 一次性明文：只在当前组件内存态短暂持有，刷新/离开/返回列表后不可再取，
 *   绝不写入 localStorage/sessionStorage/URL/剪贴板历史管理逻辑（06 §13.5，AC④）；
 * - 撤销单 Key 不影响其他 Key；已撤销 Key 再次撤销幂等成功提示（82.5，AC⑦）。
 */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { isPlatformError } from "@/lib/platform-client";
import {
  createApiKey,
  isExpired,
  listApiKeys,
  maskKey,
  revokeApiKey,
  type ApiKeyRecord,
  type CreatedApiKey,
} from "@/features/profile/api-keys";

export default function ProfileApiKeysPage() {
  const [keys, setKeys] = useState<ApiKeyRecord[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // 一次性明文视图（仅内存态，AC③④）
  const [revealed, setRevealed] = useState<CreatedApiKey | null>(null);

  const [name, setName] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [creating, setCreating] = useState(false);

  const [confirmRevokeId, setConfirmRevokeId] = useState<string | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoadError(null);
    listApiKeys()
      .then((data) => setKeys(data))
      .catch((error) => {
        setLoadError(
          isPlatformError(error) ? error.message || "无法加载 API Key 列表" : "无法加载 API Key 列表",
        );
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (creating) return;
    setActionError(null);
    setCreating(true);
    createApiKey({ name, expires_at: expiresAt ? new Date(expiresAt).toISOString() : null })
      .then((created) => {
        setRevealed(created); // 明文仅进入内存态（AC④），一次展示（AC③）
        setName("");
        setExpiresAt("");
        load();
      })
      .catch((error) => {
        setActionError(
          isPlatformError(error) ? error.message || "创建失败，请稍后重试" : "创建失败，请稍后重试",
        );
      })
      .finally(() => setCreating(false));
  }

  function handleConfirmRevoke() {
    if (!confirmRevokeId || revoking) return;
    setRevoking(true);
    setActionError(null);
    revokeApiKey(confirmRevokeId)
      .then(() => {
        setNotice("该 API Key 已撤销。");
        setConfirmRevokeId(null);
        load();
      })
      .catch((error) => {
        setActionError(isPlatformError(error) ? error.message || "撤销失败，请稍后重试" : "撤销失败，请稍后重试");
      })
      .finally(() => setRevoking(false));
  }

  async function handleCopy() {
    if (!revealed) return;
    try {
      await navigator.clipboard.writeText(revealed.api_key);
      setNotice("已复制到剪贴板（仅本次会话可见）。");
    } catch {
      setNotice("复制失败，请手动选择复制。");
    }
  }

  // 一次性明文视图（AC③：离开/刷新不可再取——视图依赖内存态 revealed）
  if (revealed) {
    return (
      <section className="card" data-testid="api-key-created">
        <h2>API Key 已创建</h2>
        <p className="profile-hint" data-testid="api-key-once-notice">
          明文只显示这一次，离开后无法再次查看（请立即复制并妥善保存）。
        </p>
        <div className="one-time-key" data-testid="api-key-plaintext">
          {revealed.api_key}
        </div>
        <div className="profile-actions">
          <button type="button" className="primary-button" onClick={() => void handleCopy()} data-testid="api-key-copy">
            复制
          </button>
          <button type="button" onClick={() => setRevealed(null)} data-testid="api-key-done">
            我已保存，返回列表
          </button>
        </div>
        {notice ? <p className="profile-hint">{notice}</p> : null}
      </section>
    );
  }

  return (
    <div className="profile-page" data-testid="api-keys-page">
      <section className="card profile-section" aria-label="我的 API Key">
        <h2>我的 API Key</h2>
        {loadError ? (
          <p className="login-error" role="alert">
            {loadError}
          </p>
        ) : null}
        {keys == null && !loadError ? <p>加载中…</p> : null}
        {keys != null && keys.length === 0 ? (
          <div className="empty-state" data-testid="api-keys-empty">
            可为 Codex、插件或 MCP 客户端创建个人访问凭证。
          </div>
        ) : null}
        {keys && keys.length > 0 ? (
          <table className="data-table" data-testid="api-keys-list">
            <thead>
              <tr>
                <th>名称</th>
                <th>Key</th>
                <th>状态</th>
                <th>到期</th>
                <th>最近使用</th>
                <th>创建时间</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => (
                <tr key={key.id} data-testid={`api-key-row-${key.id}`}>
                  <td>{key.name}</td>
                  <td>
                    <code data-testid="api-key-mask">{maskKey(key)}</code>
                  </td>
                  <td>
                    {isExpired(key)
                      ? "已到期"
                      : key.status === "revoked"
                        ? "已撤销"
                        : "有效"}
                  </td>
                  <td>{key.expires_at ? new Date(key.expires_at).toLocaleString() : "—"}</td>
                  <td>{key.last_used_at ? new Date(key.last_used_at).toLocaleString() : "—"}</td>
                  <td>{new Date(key.created_at).toLocaleString()}</td>
                  <td>
                    <button
                      type="button"
                      className="danger-button"
                      onClick={() => setConfirmRevokeId(key.id)}
                      disabled={key.status === "revoked"}
                      data-testid={`api-key-revoke-${key.id}`}
                    >
                      撤销
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        {confirmRevokeId ? (
          <div className="confirm-dialog" role="dialog" aria-label="撤销 API Key 确认">
            <p>撤销后该 Key 立即失效且不可恢复；不影响其他 API Key 与 OAuth 授权。</p>
            <div className="profile-actions">
              <button type="button" onClick={() => setConfirmRevokeId(null)} disabled={revoking}>
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
      </section>

      <section className="card profile-section" aria-label="创建 API Key">
        <h2>创建 API Key</h2>
        <form className="profile-form" onSubmit={handleCreate} data-testid="api-key-create-form">
          <label className="login-field">
            <span>名称（必填）</span>
            <input
              type="text"
              name="name"
              required
              maxLength={128}
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={creating}
              data-testid="api-key-name"
            />
          </label>
          <label className="login-field">
            <span>到期时间（可选）</span>
            <input
              type="datetime-local"
              name="expires_at"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
              disabled={creating}
              data-testid="api-key-expires"
            />
          </label>
          {actionError ? (
            <p className="login-error" role="alert" data-testid="api-key-error">
              {actionError}
            </p>
          ) : null}
          {notice ? (
            <p className="profile-hint" role="status">
              {notice}
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="submit" className="primary-button" disabled={creating} data-testid="api-key-create">
              {creating ? "创建中…" : "创建 Key"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
