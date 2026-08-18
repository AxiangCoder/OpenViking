import { createRootRoute, Outlet } from "@tanstack/react-router";
import ErrorPage from "@/components/ErrorPage";
import NotFoundPage from "./not-found";

export const rootRoute = createRootRoute({
  component: () => <Outlet />,
  errorComponent: ErrorPage,
  notFoundComponent: NotFoundPage,
});
