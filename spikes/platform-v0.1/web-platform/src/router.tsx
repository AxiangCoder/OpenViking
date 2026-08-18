import { Outlet, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import LoginPage from "./pages/LoginPage";
import AppHome from "./pages/AppHome";
import OAuthConsent from "./pages/OAuthConsent";
import OAuthVerify from "./pages/OAuthVerify";

const rootRoute = createRootRoute({
  component: () => <Outlet />,
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
});

const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/app",
  component: AppHome,
});

const consentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/oauth/consent",
  component: OAuthConsent,
});

const verifyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/oauth/verify",
  component: OAuthVerify,
});

const routeTree = rootRoute.addChildren([loginRoute, appRoute, consentRoute, verifyRoute]);

export { routeTree };
