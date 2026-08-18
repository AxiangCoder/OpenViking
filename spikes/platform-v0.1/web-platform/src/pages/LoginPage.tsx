import { useState } from "react";

type AuthMe = {
  account: { id: string | null };
  user: { id: string; ov_user_id: string | null };
  roles: string[];
  permissions: string[];
  authentication_method: string;
  can_switch_account: boolean;
};

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [me, setMe] = useState<AuthMe | null>(null);
  const [csrf, setCsrf] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const r = await fetch("/api/platform/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!r.ok) {
      const body = await r.json();
      setError(body?.detail?.code ?? "LOGIN_FAILED");
      return;
    }
    const body = await r.json();
    setCsrf(body.result.csrf_token);
    const meR = await fetch("/api/platform/v1/auth/me");
    const meBody = await meR.json();
    setMe(meBody.result);
  }

  return (
    <div style={{ maxWidth: 360, margin: "80px auto", fontFamily: "sans-serif" }}>
      <h1>OpenViking Platform</h1>
      <form onSubmit={onSubmit}>
        <div style={{ marginBottom: 8 }}>
          <input
            type="email"
            placeholder="邮箱"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            style={{ width: "100%", padding: 8 }}
          />
        </div>
        <div style={{ marginBottom: 8 }}>
          <input
            type="password"
            placeholder="密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            style={{ width: "100%", padding: 8 }}
          />
        </div>
        <button type="submit" style={{ width: "100%", padding: 8 }}>
          登录
        </button>
      </form>
      {error && <p style={{ color: "red" }}>登录失败：{error}</p>}
      {me && (
        <pre style={{ marginTop: 16, background: "#f5f5f5", padding: 12, fontSize: 12 }}>
          {JSON.stringify({ user: me.user, roles: me.roles, authentication_method: me.authentication_method, can_switch_account: me.can_switch_account }, null, 2)}
          {"\ncsrf_token (内存): " + (csrf ?? "").slice(0, 12) + "..."}
        </pre>
      )}
    </div>
  );
}
