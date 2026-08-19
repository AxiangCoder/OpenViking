/**
 * /admin/settings 页（06 §13.2，14 号计划 §98.6，P4-E1 AC⑦）。
 *
 * - v0.1 仅展示本 Account 基本信息占位，不承载正式功能（06 §13.2）；
 * - Account 固定来自登录 Session（/auth/me），无 Account 切换入口（AC①）。
 */

import { useMe } from "@/features/auth/useAuth";

export default function AdminSettingsPage() {
  const me = useMe();
  const account = me?.account ?? null;
  return (
    <section className="card admin-settings" data-testid="admin-settings-page">
      <h2>Account 设置</h2>
      <p className="profile-hint">
        v0.1 仅展示本 Account 基本信息占位，不承载正式功能（06 §13.2）。
      </p>
      <dl className="admin-settings-list" data-testid="admin-settings-account">
        <div className="profile-row">
          <dt>Account ID</dt>
          <dd>{account?.id ?? "—"}</dd>
        </div>
        {account?.code ? (
          <div className="profile-row">
            <dt>Code</dt>
            <dd>{account.code}</dd>
          </div>
        ) : null}
        {account?.name ? (
          <div className="profile-row">
            <dt>名称</dt>
            <dd>{account.name}</dd>
          </div>
        ) : null}
      </dl>
      <p className="profile-hint" data-testid="admin-settings-fixed-account">
        操作固定作用于当前登录的 Account（05 §12.6），无切换入口。
      </p>
    </section>
  );
}
