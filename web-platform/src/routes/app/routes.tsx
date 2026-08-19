import { createRoute, Outlet } from "@tanstack/react-router";
import { rootRoute } from "@/routes/__root";
import { requireAuthZone } from "@/features/auth/guards";
import ErrorPage from "@/components/ErrorPage";
import AppShell from "@/components/layouts/AppShell";
import { APP_NAV } from "@/components/layouts/nav";
import HomePage from "./home";
import SearchPage from "./search";
import ResourcesIndexPage from "./resources";
import ResourcesPrivatePage from "./resources/private";
import ResourcePrivateDetailPage from "./resources/private/$resourceId";
import ResourcesSharedPage from "./resources/shared";
import ResourceSharedDetailPage from "./resources/shared/$resourceId";
import SkillsIndexPage from "./skills";
import SkillsPrivatePage from "./skills/private";
import SkillsPrivateNewPage from "./skills/private/new";
import SkillPrivateDetailPage from "./skills/private/$skillId";
import SkillsSharedPage from "./skills/shared";
import SkillSharedDetailPage from "./skills/shared/$skillId";
import SessionsPage from "./sessions";
import ActivityPage from "./activity";
import RecycleBinPage from "./recycle-bin";
import ProfilePage from "./profile";
import ProfileApiKeysPage from "./profile/api-keys";
import ProfileConnectionsPage from "./profile/connections";

const appLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/app",
  component: () => <AppShell zone="app" items={APP_NAV} sectionTitle="/app" />,
  beforeLoad: requireAuthZone("app"),
  errorComponent: ErrorPage,
});

const appHomeRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/",
  component: HomePage,
});

const appSearchRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/search",
  component: SearchPage,
});

const appResourcesRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/resources",
  component: () => <Outlet />,
});

const appResourcesIndexRoute = createRoute({
  getParentRoute: () => appResourcesRoute,
  path: "/",
  component: ResourcesIndexPage,
});

const appResourcesPrivateRoute = createRoute({
  getParentRoute: () => appResourcesRoute,
  path: "/private",
  component: ResourcesPrivatePage,
});

const appResourcePrivateDetailRoute = createRoute({
  getParentRoute: () => appResourcesPrivateRoute,
  path: "/$resourceId",
  component: ResourcePrivateDetailPage,
});

const appResourcesSharedRoute = createRoute({
  getParentRoute: () => appResourcesRoute,
  path: "/shared",
  component: ResourcesSharedPage,
});

const appResourceSharedDetailRoute = createRoute({
  getParentRoute: () => appResourcesSharedRoute,
  path: "/$resourceId",
  component: ResourceSharedDetailPage,
});

const appSkillsRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/skills",
  component: () => <Outlet />,
});

const appSkillsIndexRoute = createRoute({
  getParentRoute: () => appSkillsRoute,
  path: "/",
  component: SkillsIndexPage,
});

const appSkillsPrivateRoute = createRoute({
  getParentRoute: () => appSkillsRoute,
  path: "/private",
  component: () => <Outlet />,
});

const appSkillsPrivateIndexRoute = createRoute({
  getParentRoute: () => appSkillsPrivateRoute,
  path: "/",
  component: SkillsPrivatePage,
});

const appSkillsPrivateNewRoute = createRoute({
  getParentRoute: () => appSkillsPrivateRoute,
  path: "/new",
  component: SkillsPrivateNewPage,
});

const appSkillPrivateDetailRoute = createRoute({
  getParentRoute: () => appSkillsPrivateRoute,
  path: "/$skillId",
  component: SkillPrivateDetailPage,
});

const appSkillsSharedRoute = createRoute({
  getParentRoute: () => appSkillsRoute,
  path: "/shared",
  component: () => <Outlet />,
});

const appSkillsSharedIndexRoute = createRoute({
  getParentRoute: () => appSkillsSharedRoute,
  path: "/",
  component: SkillsSharedPage,
});

const appSkillSharedDetailRoute = createRoute({
  getParentRoute: () => appSkillsSharedRoute,
  path: "/$skillId",
  component: SkillSharedDetailPage,
});

const appSessionsRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/sessions",
  component: SessionsPage,
});

const appActivityRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/activity",
  component: ActivityPage,
});

const appRecycleBinRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/recycle-bin",
  component: RecycleBinPage,
});

const appProfileRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/profile",
  component: () => <Outlet />,
});

const appProfileIndexRoute = createRoute({
  getParentRoute: () => appProfileRoute,
  path: "/",
  component: ProfilePage,
});

const appProfileApiKeysRoute = createRoute({
  getParentRoute: () => appProfileRoute,
  path: "/api-keys",
  component: ProfileApiKeysPage,
});

const appProfileConnectionsRoute = createRoute({
  getParentRoute: () => appProfileRoute,
  path: "/connections",
  component: ProfileConnectionsPage,
});

export const appRouteTree = appLayoutRoute.addChildren([
  appHomeRoute,
  appSearchRoute,
  appResourcesRoute.addChildren([
    appResourcesIndexRoute,
    appResourcesPrivateRoute.addChildren([appResourcePrivateDetailRoute]),
    appResourcesSharedRoute.addChildren([appResourceSharedDetailRoute]),
  ]),
  appSkillsRoute.addChildren([
    appSkillsIndexRoute,
    appSkillsPrivateRoute.addChildren([
      appSkillsPrivateIndexRoute,
      appSkillsPrivateNewRoute,
      appSkillPrivateDetailRoute,
    ]),
    appSkillsSharedRoute.addChildren([appSkillsSharedIndexRoute, appSkillSharedDetailRoute]),
  ]),
  appSessionsRoute,
  appActivityRoute,
  appRecycleBinRoute,
  appProfileRoute.addChildren([
    appProfileIndexRoute,
    appProfileApiKeysRoute,
    appProfileConnectionsRoute,
  ]),
]);
