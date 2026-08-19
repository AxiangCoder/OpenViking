/**
 * MCP OAuth 授权数据层（13 §83.2–83.3，05 §12.4，P3-E2 AC⑤⑥）。
 *
 * - `GET /integrations/mcp/oauth/pending/{pending_id}`（无鉴权）：返回服务端登记的
 *   Client 名称、Client ID、回调域名、Scope 等公开安全元数据（防仿冒，06 §13.8）；
 * - `POST /integrations/mcp/oauth/authorize`（登录 Session + CSRF，
 *   `integration.oauth.authorize.self`）：使用 `pending_id`（同设备）或跨设备
 *   display code 批准/拒绝；不接受 API Key/OAuth Token（05 §12.4）；
 * - 跨设备「先展示再决策」：authorize 请求体不带 decision 时返回待授权信息
 *   （联调契约，见 AC⑤⑥ 联合收口；P2-E6b 交付后对齐）；
 * - 敏感值纪律（AC⑥）：pending_id 读取后立即从地址栏剥离；display code 只存
 *   组件内存态、绝不进 URL；authorization code 由服务端回调客户端，前端不处理。
 */

import { request } from "@/lib/platform-client";

export interface OAuthPendingInfo {
  client_id: string;
  client_name: string | null;
  /** 服务端登记的 Client 注册回调 host（06 §13.8：不允许客户端提供的名称替代）。 */
  redirect_uri_host: string | null;
  scopes: string[];
  expires_in?: number | null;
  account?: { code?: string | null; name?: string | null } | null;
  /** 可访问数据范围 / 主要操作影响（06 §13.8 展示项，后端可选下发）。 */
  data_access?: string | null;
  impact?: string | null;
}

export type OAuthDecision = "approve" | "reject";

export interface OAuthAuthorizeInput {
  /** 同设备流程：pending_id（06 §13.8）。 */
  pending_id?: string;
  /** 跨设备流程：用户输入的短期 display code（05 §12.4）。 */
  code?: string;
  /** 缺省 = 仅查询待授权信息（跨设备先展示再决策的联调契约）。 */
  decision?: OAuthDecision;
}

export interface OAuthAuthorizeResult {
  /** 服务端登记的客户端回调（含授权码时由客户端浏览器跳转，06 §13.8 协议闭环）。 */
  redirect_url?: string | null;
  client_name?: string | null;
  /** 跨设备先展示再决策：不带 decision 查询时回填的待授权信息（联调契约）。 */
  client_id?: string | null;
  redirect_uri_host?: string | null;
  scopes?: string[] | null;
  data_access?: string | null;
  impact?: string | null;
}

export async function fetchOAuthPending(pendingId: string): Promise<OAuthPendingInfo> {
  return request<OAuthPendingInfo>(
    `/api/platform/v1/integrations/mcp/oauth/pending/${encodeURIComponent(pendingId)}`,
  );
}

export async function submitOAuthDecision(
  input: OAuthAuthorizeInput,
): Promise<OAuthAuthorizeResult> {
  return request<OAuthAuthorizeResult>("/api/platform/v1/integrations/mcp/oauth/authorize", {
    method: "POST",
    body: input,
  });
}

/** 从当前地址栏剥离 pending 参数（AC⑥：读取后不进 URL/历史驻留）。 */
export function stripPendingFromUrl(): void {
  try {
    const url = new URL(globalThis.location.href);
    if (!url.searchParams.has("pending")) return;
    url.searchParams.delete("pending");
    const next = `${url.pathname}${url.search}${url.hash}`;
    globalThis.history.replaceState(globalThis.history.state, "", next);
  } catch {
    // jsdom/异常环境下跳过，不影响页面主流程
  }
}
