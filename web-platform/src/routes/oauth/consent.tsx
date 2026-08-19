/**
 * MCP OAuth 授权页占位（06 §13.8，AC①：未登录先跳 /login，仅回跳同源授权路由）。
 * 正式流程由 P3-E2 交付（展示 Client/回调域名/Scope/数据范围，允许或拒绝）。
 */

import PlaceholderPage from "@/components/PlaceholderPage";

export default function OAuthConsentPage() {
  return (
    <PlaceholderPage
      title="MCP OAuth 授权（同设备）"
      plannedIn="P3-E2"
      description="展示服务端登记的 Client ID 与回调 host、Scope 与数据范围，允许或拒绝。不显示/要求任何 Key 或密码；pending 参数不进 URL/埋点/日志正文。"
    />
  );
}
