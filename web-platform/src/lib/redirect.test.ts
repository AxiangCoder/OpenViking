/**
 * lib/redirect.ts 单测（AC①）：回跳净化与默认入口。
 */

import { describe, expect, it } from "vitest";
import { defaultEntryPath, redirectParamFor, sanitizeRedirect } from "@/lib/redirect";

describe("redirect（AC① 回跳契约）", () => {
  it("同源内部路径允许", () => {
    expect(sanitizeRedirect("/app/search")).toBe("/app/search");
    expect(sanitizeRedirect("/app/resources/private?tab=all")).toBe("/app/resources/private?tab=all");
  });

  it("外部 URL / 协议相对 / 反斜杠拒绝", () => {
    expect(sanitizeRedirect("https://evil.example.com")).toBeNull();
    expect(sanitizeRedirect("//evil.example.com")).toBeNull();
    expect(sanitizeRedirect("/\\evil.example.com")).toBeNull();
    expect(sanitizeRedirect("javascript:alert(1)")).toBeNull();
  });

  it("/oauth/* 只允许同源授权路由（06 §13.2/§13.8）", () => {
    expect(sanitizeRedirect("/oauth/consent")).toBe("/oauth/consent");
    expect(sanitizeRedirect("/oauth/verify")).toBe("/oauth/verify");
    expect(sanitizeRedirect("/oauth/authorize")).toBeNull();
    expect(sanitizeRedirect("/oauth/consent?pending=abc")).toBeNull();
  });

  it("/login 与 / 拒绝（防回跳循环）", () => {
    expect(sanitizeRedirect("/login")).toBeNull();
    expect(sanitizeRedirect("/")).toBeNull();
    expect(sanitizeRedirect("")).toBeNull();
  });

  it("redirectParamFor 从当前 href 提取同源路径", () => {
    expect(redirectParamFor("https://ov.example.com/app/search?q=1#top")).toBe("/app/search?q=1#top");
  });

  it("默认入口：PSA → /platform/accounts，其余 → /app", () => {
    expect(defaultEntryPath(["platform_super_admin"])).toBe("/platform/accounts");
    expect(defaultEntryPath(["account_admin"])).toBe("/app");
    expect(defaultEntryPath(["user"])).toBe("/app");
    expect(defaultEntryPath(undefined)).toBe("/app");
  });
});
