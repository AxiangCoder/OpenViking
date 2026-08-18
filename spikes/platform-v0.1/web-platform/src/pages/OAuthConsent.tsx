export default function OAuthConsent() {
  return (
    <div style={{ fontFamily: "sans-serif", padding: 24, maxWidth: 480 }}>
      <h1>MCP OAuth 授权（同设备）</h1>
      <p>占位页。正式实现：展示 Client 名称/回调域名/Scope/数据范围，允许或拒绝（设计 06 §13.8）。</p>
      <button disabled>允许</button> <button disabled>拒绝</button>
    </div>
  );
}
