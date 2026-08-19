/**
 * MCP OAuth 跨设备授权页（13 §83.3，06 §13.8，P3-E2 AC⑤⑥）。
 *
 * - 未登录先跳 /login 且仅回跳同源授权路由（Guard + sanitizeRedirect，AC⑥）；
 * - 输入短期 display code → 先展示与 consent 相同的待授权信息 → 允许/拒绝（83.3）；
 * - display code 仅存组件内存态，绝不进 URL/埋点/日志正文（AC⑥）；
 * - 提交走登录 Session + CSRF（`integration.oauth.authorize.self`），页面不要求
 *   任何 Key/密码输入（AC⑤）。
 */

import { useState, type FormEvent } from "react";
import { useMe } from "@/features/auth/useAuth";
import { isPlatformError } from "@/lib/platform-client";
import {
  submitOAuthDecision,
  type OAuthPendingInfo,
} from "@/features/oauth/oauth";

type Phase =
  | { kind: "idle" }
  | { kind: "previewing" }
  | { kind: "ready" }
  | { kind: "submitting" }
  | { kind: "done"; approved: boolean }
  | { kind: "error"; message: string };

export default function OAuthVerifyPage() {
  const me = useMe();
  const [code, setCode] = useState("");
  const [info, setInfo] = useState<OAuthPendingInfo | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  const accountLabel = me?.account?.name ?? me?.account?.code ?? (me?.account?.id ? me.account.id.slice(0, 8) : "—");

  async function handlePreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = code.trim();
    if (!normalized || phase.kind === "previewing") return;
    setPhase({ kind: "previewing" });
    try {
      // 先取待授权信息（不带 decision 的联调契约，见 features/oauth/oauth.ts 注释）
      const pending = await submitOAuthDecision({ code: normalized });
      setInfo({
        client_id: pending.client_id ?? "",
        client_name: pending.client_name ?? null,
        redirect_uri_host: pending.redirect_uri_host ?? null,
        scopes: pending.scopes ?? [],
        data_access: pending.data_access ?? null,
        impact: pending.impact ?? null,
      });
      setPhase({ kind: "ready" });
    } catch (error) {
      setPhase({
        kind: "error",
        message:
          isPlatformError(error) && error.message
            ? error.message
            : "验证码无效或已过期，请重新输入",
      });
    }
  }

  async function handleDecision(decision: "approve" | "reject") {
    if (phase.kind !== "ready") return;
    const normalized = code.trim();
    setPhase({ kind: "submitting" });
    try {
      const result = await submitOAuthDecision({ code: normalized, decision });
      if (decision === "approve" && result.redirect_url) {
        globalThis.location.replace(result.redirect_url);
        return;
      }
      setPhase({ kind: "done", approved: decision === "approve" });
    } catch (error) {
      setPhase({
        kind: "error",
        message:
          isPlatformError(error) && error.message
            ? error.message
            : "授权提交失败，请稍后重试",
      });
    }
  }

  const clientName = info?.client_name || info?.client_id || "MCP 客户端";
  const showError = phase.kind === "error";

  return (
    <div className="oauth-verify-page" data-testid="oauth-verify-page">
      <h2 className="login-title">跨设备授权</h2>
      <p className="oauth-meta">输入客户端设备上显示的短期验证码，查看待授权信息后允许或拒绝。</p>

      {showError ? (
        <p className="login-error" role="alert" data-testid="oauth-verify-error">
          {phase.message}
        </p>
      ) : null}

      {phase.kind === "idle" || phase.kind === "previewing" ? (
        <form className="profile-form" onSubmit={handlePreview} data-testid="oauth-verify-form">
          <label className="login-field">
            <span>验证码</span>
            <input
              type="text"
              name="code"
              autoComplete="off"
              maxLength={12}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              disabled={phase.kind === "previewing"}
              data-testid="oauth-verify-code"
            />
          </label>
          <div className="profile-actions">
            <button
              type="submit"
              className="primary-button"
              disabled={phase.kind === "previewing" || code.trim().length === 0}
              data-testid="oauth-verify-lookup"
            >
              {phase.kind === "previewing" ? "查询中…" : "查看授权信息"}
            </button>
          </div>
        </form>
      ) : null}

      {info && (phase.kind === "ready" || phase.kind === "submitting") ? (
        <>
          <div className="oauth-client">
            <p className="oauth-meta" data-testid="oauth-verify-client">
              客户端：<strong>{clientName}</strong>
            </p>
            <p className="oauth-meta" data-testid="oauth-verify-client-id">
              Client ID：<code>{info.client_id}</code>
            </p>
            <p className="oauth-meta" data-testid="oauth-verify-redirect-host">
              回调域名（服务端登记）：<code>{info.redirect_uri_host ?? "—"}</code>
            </p>
            <p className="oauth-meta" data-testid="oauth-verify-scopes">
              Scope：<code>{(info.scopes ?? []).join(" ") || "—"}</code>
            </p>
            <p className="oauth-meta">授权身份（当前登录）：<strong>{accountLabel}</strong></p>
            {info.data_access ? <p className="oauth-meta">可访问数据范围：{info.data_access}</p> : null}
            {info.impact ? <p className="oauth-meta">主要操作影响：{info.impact}</p> : null}
          </div>
          <p className="oauth-warning" data-testid="oauth-verify-warning">
            该客户端将以你的身份运行，并受你当前角色权限限制。
          </p>
          <div className="profile-actions">
            <button
              type="button"
              onClick={() => handleDecision("reject")}
              disabled={phase.kind === "submitting"}
              data-testid="oauth-verify-reject"
            >
              拒绝
            </button>
            <button
              type="button"
              className="primary-button"
              onClick={() => handleDecision("approve")}
              disabled={phase.kind === "submitting"}
              data-testid="oauth-verify-approve"
            >
              {phase.kind === "submitting" ? "提交中…" : "允许"}
            </button>
          </div>
        </>
      ) : null}

      {phase.kind === "done" ? (
        <div role="status" data-testid="oauth-verify-done">
          <p>{phase.approved ? "授权成功，可关闭本页面并返回客户端。" : "已拒绝授权，可关闭本页面。"}</p>
        </div>
      ) : null}
    </div>
  );
}
