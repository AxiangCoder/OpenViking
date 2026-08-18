/**
 * MCP OAuth 跨设备授权页占位（06 §13.8，AC①：仅回跳同源授权路由）。
 * 正式流程由 P3-E2 交付（输入短期 display code，展示待授权信息，允许或拒绝）。
 */

import PlaceholderPage from "@/components/PlaceholderPage";

export default function OAuthVerifyPage() {
  return (
    <PlaceholderPage
      title="MCP OAuth 授权（跨设备）"
      plannedIn="P3-E2"
      description="输入短期 display code 查看待授权信息；display code / authorization code 不进 URL/埋点/日志正文。"
    />
  );
}
