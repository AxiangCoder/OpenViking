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
  // AC⑥：pending 仅作为同设备授权入参存在；读取后由页面立即从地址栏剥离
  validateSearch: (search: Record<string, unknown>) => ({
    pending: typeof search.pending === "string" ? search.pending : undefined,
  }),
});

export const oauthVerifyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/oauth/verify",
  component: OAuthVerifyPage,
  beforeLoad: requireAuthZone("oauth"),
  errorComponent: ErrorPage,
});

export const oauthRoutes = [oauthConsentRoute, oauthVerifyRoute];
