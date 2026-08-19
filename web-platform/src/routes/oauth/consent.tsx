/**
 * MCP OAuth 同设备授权页（13 §83.2，06 §13.8，P3-E2 AC⑤⑥）。
 *
 * - 未登录先跳 /login 且仅回跳同源授权路由（Guard + sanitizeRedirect，AC⑥）；
 * - 读取 `pending` 后立即从地址栏剥离（AC⑥：pending 不驻留 URL/历史）；
 * - 展示服务端登记的 Client 名称、Client ID 与回调 host、Scope、当前 Account、
 *   数据范围与操作影响；明确提示「该客户端将以你的身份运行，并受你当前角色
 *   权限限制」（06 §13.8，防仿冒）；
 * - 提交走登录 Session + CSRF（`integration.oauth.authorize.self`），页面不要求
 *   任何 Key/密码输入（AC⑤）。
 */

import { useEffect, useState } from "react";
import { useSearch } from "@tanstack/react-router";
import { useMe } from "@/features/auth/useAuth";
import { isPlatformError } from "@/lib/platform-client";
import {
  fetchOAuthPending,
  stripPendingFromUrl,
  submitOAuthDecision,
  type OAuthPendingInfo,
} from "@/features/oauth/oauth";

export interface ConsentSearch {
  pending?: string;
}

type Phase =
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "submitting" }
  | { kind: "done"; approved: boolean }
  | { kind: "expired" }
  | { kind: "error"; message: string };

export default function OAuthConsentPage() {
  const search = useSearch({ from: "/oauth/consent" }) as ConsentSearch;
  const me = useMe();
  const pending = typeof search.pending === "string" ? search.pending : undefined;

  const [info, setInfo] = useState<OAuthPendingInfo | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });

  // AC⑥：读取 pending 后立即从地址栏/历史剥离（刷新/前进后退不可再取）
  useEffect(() => {
    stripPendingFromUrl();
  }, []);

  useEffect(() => {
    if (!pending) {
      setPhase({ kind: "error", message: "授权请求不完整或已失效，请重新发起授权。" });
      return;
    }
    let cancelled = false;
    setPhase({ kind: "loading" });
    fetchOAuthPending(pending)
      .then((data) => {
        if (cancelled) return;
        setInfo(data);
        setPhase({ kind: "ready" });
      })
      .catch((error) => {
        if (cancelled) return;
        if (
          isPlatformError(error) &&
          (error.status === 404 || error.status === 410 || error.code === "NOT_FOUND")
        ) {
          setPhase({ kind: "expired" });
          return;
        }
        setPhase({
          kind: "error",
          message:
            isPlatformError(error) && error.message
              ? error.message
              : "无法加载授权信息，请稍后重试",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [pending]);

  function handleDecision(decision: "approve" | "reject") {
    if (!pending || phase.kind === "submitting") return;
    setPhase({ kind: "submitting" });
    submitOAuthDecision({ pending_id: pending, decision })
      .then((result) => {
        if (decision === "approve" && result.redirect_url) {
          // 服务端登记的客户端回调（授权码由服务端拼入回调，06 §13.8 协议闭环）
          globalThis.location.replace(result.redirect_url);
          return;
        }
        setPhase({ kind: "done", approved: decision === "approve" });
      })
      .catch((error) => {
        setPhase({
          kind: "error",
          message:
            isPlatformError(error) && error.message
              ? error.message
              : "授权提交失败，请稍后重试",
        });
      });
  }

  const clientName = info?.client_name || info?.client_id || "MCP 客户端";
  const scopes = info?.scopes ?? [];
  const accountLabel = me?.account?.name ?? me?.account?.code ?? (me?.account?.id ? me.account.id.slice(0, 8) : "—");

  return (
    <div className="oauth-consent-page" data-testid="oauth-consent-page">
      <h2 className="login-title">MCP 客户端授权</h2>

      {phase.kind === "loading" ? <p className="oauth-meta">加载授权信息…</p> : null}

      {phase.kind === "expired" ? (
        <p className="login-error" data-testid="oauth-expired">
          授权请求已过期或已被处理，请返回客户端重新发起授权。
        </p>
      ) : null}

      {phase.kind === "error" ? (
        <p className="login-error" role="alert" data-testid="oauth-error">
          {phase.message}
        </p>
      ) : null}

      {phase.kind === "done" ? (
        <div role="status" data-testid="oauth-done">
          <p>{phase.approved ? "授权成功，可关闭本页面并返回客户端。" : "已拒绝授权，可关闭本页面。"}</p>
        </div>
      ) : null}

      {info && (phase.kind === "ready" || phase.kind === "submitting") ? (
        <>
          <div className="oauth-client">
            <p className="oauth-meta" data-testid="oauth-client-name">
              客户端：<strong>{clientName}</strong>
            </p>
            <p className="oauth-meta" data-testid="oauth-client-id">
              Client ID：<code>{info.client_id}</code>
            </p>
            <p className="oauth-meta" data-testid="oauth-redirect-host">
              回调域名（服务端登记）：<code>{info.redirect_uri_host ?? "—"}</code>
            </p>
            <p className="oauth-meta" data-testid="oauth-scopes">
              Scope：<code>{scopes.length > 0 ? scopes.join(" ") : "—"}</code>
            </p>
            <p className="oauth-meta" data-testid="oauth-account">
              授权身份（当前登录）：<strong>{accountLabel}</strong>
            </p>
            {info.data_access ? (
              <p className="oauth-meta">可访问数据范围：{info.data_access}</p>
            ) : null}
            {info.impact ? <p className="oauth-meta">主要操作影响：{info.impact}</p> : null}
          </div>
          <p className="oauth-warning" data-testid="oauth-warning">
            该客户端将以你的身份运行，并受你当前角色权限限制。
          </p>
          <div className="profile-actions">
            <button
              type="button"
              onClick={() => handleDecision("reject")}
              disabled={phase.kind === "submitting"}
              data-testid="oauth-reject"
            >
              拒绝
            </button>
            <button
              type="button"
              className="primary-button"
              onClick={() => handleDecision("approve")}
              disabled={phase.kind === "submitting"}
              data-testid="oauth-approve"
            >
              {phase.kind === "submitting" ? "提交中…" : "允许"}
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}
