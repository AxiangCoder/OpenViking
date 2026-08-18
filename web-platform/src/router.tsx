import { createRouter } from "@tanstack/react-router";
import { rootRoute } from "./routes/__root";
import { loginRoute } from "./routes/login/route";
import { oauthRoutes } from "./routes/oauth/routes";
import { appRouteTree } from "./routes/app/routes";
import { adminRouteTree } from "./routes/admin/routes";
import { platformRouteTree } from "./routes/platform/routes";

export const routeTree = rootRoute.addChildren([
  loginRoute,
  appRouteTree,
  adminRouteTree,
  platformRouteTree,
  ...oauthRoutes,
]);

export function createAppRouter(
  options: Parameters<typeof createRouter>[0] = {},
): ReturnType<typeof createRouter> {
  return createRouter({ routeTree, ...options });
}
