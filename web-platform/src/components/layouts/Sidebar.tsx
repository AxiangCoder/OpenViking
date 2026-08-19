/**
 * 三布局侧边栏（/app、/admin、/platform，06 §13.2）。
 * 条目依 Permission 过滤（AC②）；不出现 /studio 与 Account 切换入口（06 §13.6，07 §21 条目 14）。
 */

import { Link } from "@tanstack/react-router";
import { useHasPermission } from "@/features/auth/useAuth";
import type { NavItem } from "./nav";

export interface SidebarProps {
  items: NavItem[];
  sectionTitle: string;
  zone: string;
}

export default function Sidebar({ items, sectionTitle, zone }: SidebarProps) {
  return (
    <aside className="sidebar" data-testid={`sidebar-${zone}`}>
      <div className="sidebar-brand">OpenViking Platform</div>
      <nav className="sidebar-nav" aria-label={sectionTitle}>
        {items.map((item) => (
          <SidebarItem key={item.to} item={item} />
        ))}
      </nav>
    </aside>
  );
}

function SidebarItem({ item }: { item: NavItem }) {
  const allowed = item.permission ? useHasPermission(item.permission) : true;
  if (!allowed) return null;
  return (
    <Link
      to={item.to}
      activeOptions={{ exact: item.exact ?? false }}
      activeProps={{ className: "sidebar-link sidebar-link-active" }}
      className="sidebar-link"
    >
      {item.label}
    </Link>
  );
}
