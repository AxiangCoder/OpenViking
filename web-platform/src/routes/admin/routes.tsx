import { createRoute, Outlet } from "@tanstack/react-router";
import { rootRoute } from "@/routes/__root";
import { requireAuthZone } from "@/features/auth/guards";
import ErrorPage from "@/components/ErrorPage";
import AppShell from "@/components/layouts/AppShell";
import { ADMIN_NAV } from "@/components/layouts/nav";
import AdminIndexPage from "./index";
import AdminUsersPage from "./users";
import AdminUserDataPage from "./users/$userId/data";
import AdminUserResourcePage from "./users/$userId/resources/$resourceId";
import AdminUserApiKeysPage from "./users/$userId/api-keys";
import AdminUserSkillPage from "./users/$userId/skills/$skillId";
import AdminSharedResourcesPage from "./shared-resources";
import AdminSharedResourcePage from "./shared-resources/$resourceId";
import AdminSharedSkillsPage from "./shared-skills";
import AdminSharedSkillsNewPage from "./shared-skills/new";
import AdminSharedSkillPage from "./shared-skills/$skillId";
import AdminRolesPage from "./roles";
import AdminAuditPage from "./audit";
import AdminActivityPage from "./activity";
import AdminMonitoringPage from "./monitoring";
import AdminRecycleBinPage from "./recycle-bin";
import AdminSettingsPage from "./settings";

const adminLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin",
  component: () => <AppShell zone="admin" items={ADMIN_NAV} sectionTitle="/admin" />,
  beforeLoad: requireAuthZone("admin"),
  errorComponent: ErrorPage,
});

const adminIndexRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/",
  component: AdminIndexPage,
});

const adminUsersLayoutRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/users",
  component: () => <Outlet />,
});

const adminUsersIndexRoute = createRoute({
  getParentRoute: () => adminUsersLayoutRoute,
  path: "/",
  component: AdminUsersPage,
});

const adminUserDataRoute = createRoute({
  getParentRoute: () => adminUsersLayoutRoute,
  path: "/$userId/data",
  component: AdminUserDataPage,
});

const adminUserResourceRoute = createRoute({
  getParentRoute: () => adminUsersLayoutRoute,
  path: "/$userId/resources/$resourceId",
  component: AdminUserResourcePage,
});

const adminUserApiKeysRoute = createRoute({
  getParentRoute: () => adminUsersLayoutRoute,
  path: "/$userId/api-keys",
  component: AdminUserApiKeysPage,
});

const adminUserSkillRoute = createRoute({
  getParentRoute: () => adminUsersLayoutRoute,
  path: "/$userId/skills/$skillId",
  component: AdminUserSkillPage,
});

const adminSharedResourcesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/shared-resources",
  component: () => <Outlet />,
});

const adminSharedResourcesIndexRoute = createRoute({
  getParentRoute: () => adminSharedResourcesRoute,
  path: "/",
  component: AdminSharedResourcesPage,
});

const adminSharedResourceDetailRoute = createRoute({
  getParentRoute: () => adminSharedResourcesRoute,
  path: "/$resourceId",
  component: AdminSharedResourcePage,
});

const adminSharedSkillsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/shared-skills",
  component: AdminSharedSkillsPage,
});

const adminSharedSkillsNewRoute = createRoute({
  getParentRoute: () => adminSharedSkillsRoute,
  path: "/new",
  component: AdminSharedSkillsNewPage,
});

const adminSharedSkillDetailRoute = createRoute({
  getParentRoute: () => adminSharedSkillsRoute,
  path: "/$skillId",
  component: AdminSharedSkillPage,
});

const adminRolesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/roles",
  component: AdminRolesPage,
});

const adminAuditRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/audit",
  component: AdminAuditPage,
});

const adminActivityRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/activity",
  component: AdminActivityPage,
});

const adminMonitoringRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/monitoring",
  component: AdminMonitoringPage,
});

const adminRecycleBinRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/recycle-bin",
  component: AdminRecycleBinPage,
});

const adminSettingsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/settings",
  component: AdminSettingsPage,
});

export const adminRouteTree = adminLayoutRoute.addChildren([
  adminIndexRoute,
  adminUsersLayoutRoute.addChildren([
    adminUsersIndexRoute,
    adminUserDataRoute,
    adminUserResourceRoute,
    adminUserApiKeysRoute,
    adminUserSkillRoute,
  ]),
  adminSharedResourcesRoute.addChildren([
    adminSharedResourcesIndexRoute,
    adminSharedResourceDetailRoute,
  ]),
  adminSharedSkillsRoute.addChildren([adminSharedSkillsNewRoute, adminSharedSkillDetailRoute]),
  adminRolesRoute,
  adminAuditRoute,
  adminActivityRoute,
  adminMonitoringRoute,
  adminRecycleBinRoute,
  adminSettingsRoute,
]);
