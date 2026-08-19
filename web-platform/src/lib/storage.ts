/**
 * 前端本地存储规则（06 §13.5，AC④ 落地点）。
 *
 * 允许保存：主题、语言、表格列、最近打开页面等非敏感偏好。
 * 禁止保存：Root/User API Key、登录 Session Token、密码、权限快照、
 * 从用户消息截取的 Session 标题或其他业务正文。
 *
 * 实现上只暴露白名单 key 的读写，拒绝白名单外 key 与敏感值，
 * 从机制上保证「DevTools 本地存储仅非敏感偏好」。
 */

export const PREFERENCE_KEYS = [
  "platform.theme",
  "platform.lang",
  "platform.sidebar-collapsed",
  "platform.last-open-path",
] as const;

export type PreferenceKey = (typeof PREFERENCE_KEYS)[number];

/** 与 06 §13.5 禁止清单对应：任何命中模式的值一律拒绝写入。 */
const SENSITIVE_VALUE_PATTERNS: RegExp[] = [
  /^(sk-|ovk_[a-z]+\.)/i,
  /^__Host-ov_session/,
  /eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}/,
  /(password|passwd|secret|token|credential|csrf|api[_-]?key)/i,
];

function isAllowedKey(key: string): key is PreferenceKey {
  return (PREFERENCE_KEYS as readonly string[]).includes(key);
}

export function isSensitiveValue(value: unknown): boolean {
  if (typeof value !== "string") return false;
  return SENSITIVE_VALUE_PATTERNS.some((pattern) => pattern.test(value));
}

export function setPreference(key: string, value: unknown): void {
  if (!isAllowedKey(key)) {
    throw new Error(`本地存储白名单外 key 被拒绝：${key}（06 §13.5 仅非敏感偏好）`);
  }
  if (isSensitiveValue(value)) {
    throw new Error(`本地存储敏感值被拒绝：${key}（06 §13.5 禁存 Key/Token/密码/业务正文）`);
  }
  globalThis.localStorage.setItem(key, JSON.stringify({ v: value }));
}

export function getPreference<T>(key: PreferenceKey): T | undefined {
  const raw = globalThis.localStorage.getItem(key);
  if (raw == null) return undefined;
  try {
    const parsed = JSON.parse(raw) as { v?: T };
    return parsed?.v;
  } catch {
    return undefined;
  }
}

/** 清除全部产品偏好（保留其他应用数据）。 */
export function clearPreferences(): void {
  for (const key of PREFERENCE_KEYS) {
    globalThis.localStorage.removeItem(key);
  }
}

/** 审计辅助：返回当前本地存储中的产品 key 清单（仅用于自检/测试）。 */
export function storedPreferenceKeys(): string[] {
  const keys: string[] = [];
  for (let i = 0; i < globalThis.localStorage.length; i += 1) {
    const key = globalThis.localStorage.key(i);
    if (key && isAllowedKey(key)) keys.push(key);
  }
  return keys.sort();
}
