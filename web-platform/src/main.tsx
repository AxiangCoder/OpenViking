import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { createAppRouter } from "./router";
import { bootstrapAuth, onSessionExpired } from "./features/auth/auth-state";
import { sanitizeRedirect, redirectParamFor } from "./lib/redirect";
import "./styles.css";

const router = createAppRouter();
const queryClient = new QueryClient();

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

// 会话过期 → 回登录页（AC③，保留安全状态与回跳目标）
onSessionExpired(() => {
  const current = sanitizeRedirect(redirectParamFor(globalThis.location.href));
  void router.navigate({
    to: "/login",
    search: { redirect: current ?? undefined, reason: "session_expired" },
  });
});

// 启动必调 /auth/me（AC①）
void bootstrapAuth();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>
);
