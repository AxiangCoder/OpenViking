/**
 * Resource 表单校验（09 §40.3/§40.4/§40.6，P3-E4）。
 *
 * - 标签严格 `key=value`：key/value 均非空、整体转小写去重、最多 20 个、
 *   每个最多 40 字符（与服务端 normalize_tags 一致，04 §10.10）；
 * - 远程来源只接受公开 HTTPS；禁止 userinfo/localhost/私网/file://（09 §40.6，
 *   AC⑧：URL 不携带用户凭证）；是否允许 HTTP 由部署策略决定，前端不自行放宽；
 * - 名称 1–128、说明 ≤1000、处理要求 ≤2000 字符（09 §40.3）。
 */

export const RESOURCE_NAME_MAX = 128;
export const RESOURCE_DESCRIPTION_MAX = 1000;
export const RESOURCE_INSTRUCTION_MAX = 2000;
export const RESOURCE_TAGS_MAX = 20;
export const RESOURCE_TAG_MAX_LENGTH = 40;

const TAG_PATTERN = /^[^=\s]+=[^=\s]+$/;

export interface TagNormalizationResult {
  tags: string[];
  error: string | null;
}

/**
 * 严格 `key=value` 校验并规范化（09 §40.3：与后端 normalize_tags 同规则，
 * AC④：标签严格 key=value）。空输入返回空数组；返回 error 表示整体不可提交。
 */
export function normalizeTags(input: readonly string[]): TagNormalizationResult {
  const seen = new Set<string>();
  const tags: string[] = [];
  for (const raw of input) {
    const item = raw.trim().toLowerCase();
    if (item.length === 0) continue;
    if (seen.has(item)) continue;
    if (item.length > RESOURCE_TAG_MAX_LENGTH) {
      return { tags: [], error: `标签长度超过 ${RESOURCE_TAG_MAX_LENGTH} 字符：${item}` };
    }
    if (!TAG_PATTERN.test(item)) {
      return {
        tags: [],
        error: `标签必须严格使用 key=value 格式（key/value 均非空、不含空格）：${item}`,
      };
    }
    if (tags.length >= RESOURCE_TAGS_MAX) {
      return { tags: [], error: `标签最多 ${RESOURCE_TAGS_MAX} 个` };
    }
    seen.add(item);
    tags.push(item);
  }
  return { tags, error: null };
}

export interface ResourceFormBase {
  name: string;
  description: string;
  tags: string[];
  instruction: string;
}

/** 基础字段校验（名称可为空：服务端从文件名/页面标题/仓库名生成，09 §40.3）。 */
export function validateResourceFormBase(
  form: ResourceFormBase,
): string | null {
  if (form.name.length > RESOURCE_NAME_MAX) {
    return `名称最多 ${RESOURCE_NAME_MAX} 字符`;
  }
  if (form.description.length > RESOURCE_DESCRIPTION_MAX) {
    return `说明最多 ${RESOURCE_DESCRIPTION_MAX} 字符`;
  }
  if (form.instruction.length > RESOURCE_INSTRUCTION_MAX) {
    return `处理要求最多 ${RESOURCE_INSTRUCTION_MAX} 字符`;
  }
  const tagResult = normalizeTags(form.tags);
  if (tagResult.error) return tagResult.error;
  return null;
}

/** 禁止的远程来源（09 §40.6：localhost/私网/云元数据/文件协议等由服务端强校验）。 */
const PRIVATE_HOST_PATTERNS = [
  /^localhost$/i,
  /^127\./,
  /^10\./,
  /^192\.168\./,
  /^169\.254\./,
  /^172\.(1[6-9]|2\d|3[01])\./,
  /^0\./,
  /^::1$/,
  /^fe80:/i,
  /^fc/i,
  /^fd/i,
  /^169\.254\.169\.254/,
];

function hostOf(url: URL): string {
  return url.hostname.replace(/^\[|\]$/g, "");
}

/** 公开 HTTPS 网页/Git URL 校验（09 §40.6，AC⑧：Query 允许一次性导入但不可 Watch）。 */
export function validateRemoteUrl(raw: string): { url: URL; error: null } | { url: null; error: string } {
  let parsed: URL;
  try {
    parsed = new URL(raw.trim());
  } catch {
    return { url: null, error: "请输入完整 URL（https://…）" };
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    return { url: null, error: "仅支持公开 HTTPS 网页或 Git 仓库（file://、ftp:// 等协议被禁止）" };
  }
  if (parsed.username || parsed.password) {
    return { url: null, error: "URL 不允许携带用户名/密码（userinfo）" };
  }
  const host = hostOf(parsed);
  if (PRIVATE_HOST_PATTERNS.some((pattern) => pattern.test(host))) {
    return { url: null, error: "不允许导入 localhost / 内网 / 云元数据地址（SSRF 防护）" };
  }
  return { url: parsed, error: null };
}

/** Git 来源：v0.1 仅公开 HTTPS Repository URL，SSH/git@ 被拒（09 §40.6）。 */
export function validateGitUrl(raw: string): { url: URL; error: null } | { url: null; error: string } {
  const trimmed = raw.trim();
  if (/^(ssh:\/\/|git@|git\+ssh)/i.test(trimmed)) {
    return { url: null, error: "Git v0.1 仅支持公开 HTTPS 仓库地址（不支持 SSH/git@）" };
  }
  const checked = validateRemoteUrl(trimmed);
  if (checked.url === null) {
    return { url: null, error: checked.error };
  }
  return { url: checked.url, error: null };
}

/** 是否稳定来源（可 Watch：无 userinfo/Query/Fragment，09 §40.6）。 */
export function isStableRemoteUrl(raw: string): boolean {
  const checked = validateRemoteUrl(raw);
  if (checked.url === null) return false;
  const url = checked.url;
  return url.username === "" && url.password === "" && url.search === "" && url.hash === "";
}
