/**
 * MCP OAuth 连接数据层（13 §83.1，05 §12.4，P3-E2 AC⑤⑦）。
 *
 * - `GET /me/oauth-grants`：已授权客户端（Client 名称、授权时间、最近使用时间、
 *   Scope=`mcp`、状态），`integration.oauth.read.self`；
 * - `DELETE /me/oauth-grants/{grant_id}`：撤销 Grant 及其 Token family，
 *   不影响其他 Key/客户端（13 §83.1，AC⑦）。
 *
 * 后端协议端点由 P2-E6b 并行交付；本文件按 05 §12.4 契约实现，联调收口见 AC⑤⑥。
 */

import { request } from "@/lib/platform-client";

export interface OauthGrant {
  id: string;
  client_id: string;
  client_name: string | null;
  scope: string;
  status: string;
  created_at: string | null;
  last_used_at: string | null;
}

export async function listOauthGrants(): Promise<OauthGrant[]> {
  return request<OauthGrant[]>("/api/platform/v1/me/oauth-grants");
}

export async function revokeOauthGrant(grantId: string): Promise<void> {
  await request<{ status: string }>(`/api/platform/v1/me/oauth-grants/${grantId}`, {
    method: "DELETE",
  });
}
