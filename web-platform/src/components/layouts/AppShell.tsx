/**
 * 三布局公共外壳：侧边栏 + 顶栏（当前用户/角色）+ Outlet。
 * 顶栏仅展示当前登录 Actor 的脱敏信息，无 Account 切换入口（07 §21 条目 14）。
 */

import { Outlet } from "@tanstack/react-router";
import Sidebar from "./Sidebar";
import type { NavItem } from "./nav";
import { useAuth } from "@/features/auth/useAuth";

export interface AppShellProps {
  zone: string;
  items: NavItem[];
  sectionTitle: string;
}

export default function AppShell({ zone, items, sectionTitle }: AppShellProps) {
  const { me } = useAuth();
  return (
    <div className="app-shell" data-testid={`shell-${zone}`}>
      <Sidebar items={items} sectionTitle={sectionTitle} zone={zone} />
      <div className="app-main">
        <header className="topbar">
          <span className="topbar-zone">{sectionTitle}</span>
          <span className="topbar-user">
            {me ? (
              <>
                <span className="topbar-role">{me.roles.join(" / ")}</span>
                <span className="topbar-name">{me.user.id.slice(0, 8)}…</span>
              </>
            ) : (
              <span className="topbar-name">未登录</span>
            )}
          </span>
        </header>
        <main className="app-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
