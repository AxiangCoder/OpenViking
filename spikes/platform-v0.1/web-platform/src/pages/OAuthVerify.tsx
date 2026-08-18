export default function OAuthVerify() {
  return (
    <div style={{ fontFamily: "sans-serif", padding: 24, maxWidth: 480 }}>
      <h1>MCP OAuth 授权（跨设备）</h1>
      <p>占位页。正式实现：输入短期 display code，展示待授权信息，允许或拒绝（设计 06 §13.8）。</p>
      <input placeholder="输入验证码" disabled />
    </div>
  );
}
