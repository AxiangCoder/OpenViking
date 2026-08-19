/**
 * 业务页占位组件：路由树全量展开用（06 §13.2 路由冻结）。
 * 占位页不承载任何业务逻辑，仅声明所属 Epic 与页面职责。
 */

import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";

export interface PlaceholderPageProps {
  title: string;
  plannedIn: string;
  description?: string;
  children?: ReactNode;
}

export default function PlaceholderPage({
  title,
  plannedIn,
  description,
  children,
}: PlaceholderPageProps) {
  return (
    <section className="placeholder-page" data-testid="placeholder-page">
      <h1 className="placeholder-title">{title}</h1>
      <p className="placeholder-meta">
        路由与布局已冻结（06 §13.2），正式页面由 <strong>{plannedIn}</strong> 交付。
      </p>
      {description ? <p className="placeholder-desc">{description}</p> : null}
      {children}
      <Link to="/app" className="placeholder-back">
        返回首页
      </Link>
    </section>
  );
}
