/**
 * 退出类操作后的登录页跳转（P3-E2）。
 * 全量页面跳转以重置内存态（CSRF Token / Key 明文等只存内存，不随 SPA 状态存留）。
 */

export type LeaveReason = "logged_out" | "password_changed";

export function leaveToLogin(reason: LeaveReason): void {
  globalThis.location.assign(`/login?reason=${reason}`);
}
