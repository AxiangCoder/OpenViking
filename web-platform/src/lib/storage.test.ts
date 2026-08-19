/**
 * lib/storage.ts 单测（06 §13.5，AC④）：本地存储仅非敏感偏好。
 */

import { describe, expect, it } from "vitest";
import {
  clearPreferences,
  getPreference,
  isSensitiveValue,
  setPreference,
  storedPreferenceKeys,
} from "@/lib/storage";

describe("storage（06 §13.5）", () => {
  it("白名单偏好 key 可读写", () => {
    setPreference("platform.theme", "dark");
    setPreference("platform.sidebar-collapsed", true);
    expect(getPreference<"dark" | "light">("platform.theme")).toBe("dark");
    expect(getPreference<boolean>("platform.sidebar-collapsed")).toBe(true);
  });

  it("白名单外 key 一律拒绝（不落存储）", () => {
    expect(() => setPreference("auth.csrf", "x")).toThrow(/白名单外/);
    expect(() => setPreference("user.permissions", ["a"])).toThrow(/白名单外/);
    expect(storedPreferenceKeys()).toHaveLength(0);
  });

  it("敏感值拒绝写入：Token/Key/密码/凭证形态（06 §13.5 禁止清单）", () => {
    expect(isSensitiveValue("ovk_u.abc.secret")).toBe(true);
    expect(isSensitiveValue("eyJhbGciOi.eyJzdWIiOi.abc")).toBe(true);
    expect(isSensitiveValue("my-password-123")).toBe(true);
    expect(isSensitiveValue("dark")).toBe(false);
    expect(isSensitiveValue("zh-CN")).toBe(false);
  });

  it("即使落在白名单 key 上，敏感值也被拒绝", () => {
    expect(() => setPreference("platform.theme", "ovk_u.abc.secret")).toThrow(/敏感值/);
  });

  it("clearPreferences 只清产品 key", () => {
    setPreference("platform.lang", "zh-CN");
    globalThis.localStorage.setItem("other-app-key", "keep");
    clearPreferences();
    expect(storedPreferenceKeys()).toHaveLength(0);
    expect(globalThis.localStorage.getItem("other-app-key")).toBe("keep");
  });

  it("storedPreferenceKeys 只暴露白名单 key（DevTools 自检依据）", () => {
    setPreference("platform.lang", "zh-CN");
    globalThis.localStorage.setItem("security.snapshot", "{}");
    expect(storedPreferenceKeys()).toEqual(["platform.lang"]);
  });
});
