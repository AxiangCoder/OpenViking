import { createRoute } from "@tanstack/react-router";
import { rootRoute } from "@/routes/__root";
import { requireAuthZone } from "@/features/auth/guards";
import ErrorPage from "@/components/ErrorPage";
import OAuthConsentPage from "./consent";
import OAuthVerifyPage from "./verify";

export const oauthConsentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/oauth/consent",
  component: OAuthConsentPage,
  beforeLoad: requireAuthZone("oauth"),
  errorComponent: ErrorPage,
});

export const oauthVerifyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/oauth/verify",
  component: OAuthVerifyPage,
  beforeLoad: requireAuthZone("oauth"),
  errorComponent: ErrorPage,
});

export const oauthRoutes = [oauthConsentRoute, oauthVerifyRoute];
