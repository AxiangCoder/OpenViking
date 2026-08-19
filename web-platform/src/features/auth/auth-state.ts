/**
 * Auth 状态层（06 §13.4，P3-E1）。
 *
 * - 应用启动调用 `/auth/me`（AC①），结果进入内存态 store（useSyncExternalStore 驱动 UI）；
 * - 401/403（auth-challenge 事件）→ 刷新 `/auth/me` 并更新界面（AC③）；
 * - 刷新确认未登录（会话过期）→ 置 sessionExpired 并回调导航到登录页；
 * - CSRF Token 只存活于内存（04 §10.7），绝不写入任何存储（AC④）。
 */

import { configurePlatformClient, request } from "@/lib/platform-client";
import { AUTH_CHALLENGE_EVENT } from "@/lib/platform-client/client";

export interface AuthMeAccount {
  id: string | null;
}

export interface AuthMeResult {
  account: AuthMeAccount | null;
  user: {
    id: string;
    ov_user_id: string | null;
  };
  roles: string[];
  permissions: string[];
  can_switch_account: boolean;
  csrf_token: string | null;
}

export type AuthStatus = "idle" | "loading" | "authenticated" | "unauthenticated";

export interface AuthState {
  status: AuthStatus;
  me: AuthMeResult | null;
  sessionExpired: boolean;
  /** 启动后第一次刷新未完成的挂起期（Guard 等待用）。 */
  lastError?: string;
}

const ME_PATH = "/api/platform/v1/auth/me";

let state: AuthState = { status: "idle", me: null, sessionExpired: false };
const listeners = new Set<() => void>();

let readyResolve: (s: AuthState) => void;
let readyPromise: Promise<AuthState> = new Promise((resolve) => {
  readyResolve = resolve;
});

function resetReady(): void {
  readyPromise = new Promise((resolve) => {
    readyResolve = resolve;
  });
}

let refreshing = false;
const sessionExpiredHandlers = new Set<() => void>();

export function subscribeAuth(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getAuthState(): AuthState {
  return state;
}

function setState(next: AuthState): void {
  state = next;
  for (const listener of listeners) listener();
}

/** 首次启动的 /auth/me 完成信号（Guard 在 status=idle/loading 时 await 它）。 */
export function authReady(): Promise<AuthState> {
  return readyPromise;
}

/** 注册会话过期回调（main.tsx 注册 router 跳转；仅内存态，不涉及安全存储）。 */
export function onSessionExpired(handler: () => void): () => void {
  sessionExpiredHandlers.add(handler);
  return () => sessionExpiredHandlers.delete(handler);
}

export function isUnauthenticatedStatus(status: AuthStatus): boolean {
  return status === "unauthenticated";
}

/**
 * 刷新 /auth/me（AC③：401/403 后调用，更新界面）。
 * 401/403 视为未登录；网络等其他错误保持现状并记录 lastError。
 */
export async function refreshAuth(): Promise<AuthState> {
  if (refreshing) return state;
  refreshing = true;
  resetReady();
  if (state.status === "idle") setState({ ...state, status: "loading" });
  try {
    const me = await request<AuthMeResult>(ME_PATH);
    setState({ status: "authenticated", me, sessionExpired: false });
  } catch (error) {
    if (
      error instanceof Error &&
      "status" in error &&
      ((error as { status: number }).status === 401 || (error as { status: number }).status === 403)
    ) {
      setState({ status: "unauthenticated", me: null, sessionExpired: true });
      for (const handler of sessionExpiredHandlers) handler();
    } else {
      setState({
        ...state,
        lastError: error instanceof Error ? error.message : String(error),
      });
    }
  } finally {
    refreshing = false;
    readyResolve(state);
  }
  return state;
}

/** 应用启动调用（main.tsx）。 */
export function bootstrapAuth(): Promise<AuthState> {
  return refreshAuth();
}

function handleAuthChallenge(): void {
  if (refreshing) return;
  if (state.status === "unauthenticated") return;
  void refreshAuth();
}

if (typeof globalThis.addEventListener === "function") {
  globalThis.addEventListener(AUTH_CHALLENGE_EVENT, handleAuthChallenge);
}

// ── CSRF Token 内存持有（03 §8.2；登录/改密响应一次性下发）──

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export function getCsrfToken(): string | null {
  return csrfToken;
}

configurePlatformClient({ csrfTokenProvider: getCsrfToken });

// ── 测试辅助（仅测试环境使用）──

export function setAuthStateForTest(next: AuthState): void {
  resetReady();
  setState(next);
  readyResolve(next);
}
