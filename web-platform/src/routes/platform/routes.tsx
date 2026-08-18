import { createRoute, Outlet } from "@tanstack/react-router";
import { rootRoute } from "@/routes/__root";
import { requireAuthZone } from "@/features/auth/guards";
import ErrorPage from "@/components/ErrorPage";
import AppShell from "@/components/layouts/AppShell";
import { PLATFORM_NAV } from "@/components/layouts/nav";
import PlatformIndexPage from "./index";
import PlatformAccountsPage from "./accounts";
import PlatformAccountUsersPage from "./accounts/$accountId/users";
import PlatformAccountResourcesPage from "./accounts/$accountId/resources";
import PlatformAccountResourceDetailPage from "./accounts/$accountId/resources/$resourceId";
import PlatformAccountSkillsPage from "./accounts/$accountId/skills";
import PlatformAccountSkillDetailPage from "./accounts/$accountId/skills/$skillId";
import PlatformAccountUserSkillPage from "./accounts/$accountId/users/$userId/skills/$skillId";
import PlatformAccountUserDataPage from "./accounts/$accountId/users/$userId/data";
import PlatformAccountUserResourcePage from "./accounts/$accountId/users/$userId/resources/$resourceId";
import PlatformAccountUserApiKeysPage from "./accounts/$accountId/users/$userId/api-keys";
import PlatformAuditPage from "./audit";
import PlatformActivityPage from "./activity";
import PlatformMonitoringPage from "./monitoring";
import PlatformRecycleBinPage from "./recycle-bin";

const platformLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/platform",
  component: () => (
    <AppShell zone="platform" items={PLATFORM_NAV} sectionTitle="/platform" />
  ),
  beforeLoad: requireAuthZone("platform"),
  errorComponent: ErrorPage,
});

const platformIndexRoute = createRoute({
  getParentRoute: () => platformLayoutRoute,
  path: "/",
  component: PlatformIndexPage,
});

const platformAccountsRoute = createRoute({
  getParentRoute: () => platformLayoutRoute,
  path: "/accounts",
  component: () => <Outlet />,
});

const platformAccountsIndexRoute = createRoute({
  getParentRoute: () => platformAccountsRoute,
  path: "/",
  component: PlatformAccountsPage,
});

const platformAccountUsersRoute = createRoute({
  getParentRoute: () => platformAccountsRoute,
  path: "/$accountId/users",
  component: PlatformAccountUsersPage,
});

const platformAccountResourcesRoute = createRoute({
  getParentRoute: () => platformAccountsRoute,
  path: "/$accountId/resources",
  component: PlatformAccountResourcesPage,
});

const platformAccountResourceDetailRoute = createRoute({
  getParentRoute: () => platformAccountResourcesRoute,
  path: "/$resourceId",
  component: PlatformAccountResourceDetailPage,
});

const platformAccountSkillsRoute = createRoute({
  getParentRoute: () => platformAccountsRoute,
  path: "/$accountId/skills",
  component: PlatformAccountSkillsPage,
});

const platformAccountSkillDetailRoute = createRoute({
  getParentRoute: () => platformAccountSkillsRoute,
  path: "/$skillId",
  component: PlatformAccountSkillDetailPage,
});

const platformAccountUserSkillRoute = createRoute({
  getParentRoute: () => platformAccountUsersRoute,
  path: "/$userId/skills/$skillId",
  component: PlatformAccountUserSkillPage,
});

const platformAccountUserDataRoute = createRoute({
  getParentRoute: () => platformAccountUsersRoute,
  path: "/$userId/data",
  component: PlatformAccountUserDataPage,
});

const platformAccountUserResourceRoute = createRoute({
  getParentRoute: () => platformAccountUsersRoute,
  path: "/$userId/resources/$resourceId",
  component: PlatformAccountUserResourcePage,
});

const platformAccountUserApiKeysRoute = createRoute({
  getParentRoute: () => platformAccountUsersRoute,
  path: "/$userId/api-keys",
  component: PlatformAccountUserApiKeysPage,
});

const platformAuditRoute = createRoute({
  getParentRoute: () => platformLayoutRoute,
  path: "/audit",
  component: PlatformAuditPage,
});

const platformActivityRoute = createRoute({
  getParentRoute: () => platformLayoutRoute,
  path: "/activity",
  component: PlatformActivityPage,
});

const platformMonitoringRoute = createRoute({
  getParentRoute: () => platformLayoutRoute,
  path: "/monitoring",
  component: PlatformMonitoringPage,
});

const platformRecycleBinRoute = createRoute({
  getParentRoute: () => platformLayoutRoute,
  path: "/recycle-bin",
  component: PlatformRecycleBinPage,
});

export const platformRouteTree = platformLayoutRoute.addChildren([
  platformIndexRoute,
  platformAccountsRoute.addChildren([
    platformAccountsIndexRoute,
    platformAccountResourcesRoute.addChildren([platformAccountResourceDetailRoute]),
    platformAccountSkillsRoute.addChildren([platformAccountSkillDetailRoute]),
    platformAccountUsersRoute.addChildren([
      platformAccountUserSkillRoute,
      platformAccountUserDataRoute,
      platformAccountUserResourceRoute,
      platformAccountUserApiKeysRoute,
    ]),
  ]),
  platformAuditRoute,
  platformActivityRoute,
  platformMonitoringRoute,
  platformRecycleBinRoute,
]);
