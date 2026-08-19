/**
 * Skill 前端校验（10 §54.1/§55.1/§56.1，P3-E5）。
 *
 * - `validateSkillName` 与后端 `validate_skill_name` 规则一致：去空白后
 *   ≤64 字符、仅 ASCII 字母/数字/下划线/连字符（10 §54.1，AC②）；
 * - 标签校验与 05 §12.5「所有 Resource/Skill 创建、上传和编辑 API」同一规则：
 *   最多 20 项、每项最多 40 字符、严格 `key=value`、key/value 均非空、
 *   整体小写并去重；前端只做体验校验，后端仍重复校验。
 */

export const SKILL_NAME_PATTERN = "^[A-Za-z0-9_-]{1,64}$";
export const SKILL_NAME_MAX_LENGTH = 64;
export const SKILL_TAG_MAX_ITEMS = 20;
export const SKILL_TAG_MAX_LENGTH = 40;

/** 与后端 `validate_skill_name` 一致：strip 后按模式校验。 */
export function validateSkillName(name: string): { ok: boolean; error?: string } {
  const normalized = name.trim();
  if (!normalized) {
    return { ok: false, error: "名称不能为空" };
  }
  if (normalized.length > SKILL_NAME_MAX_LENGTH) {
    return { ok: false, error: `名称最多 ${SKILL_NAME_MAX_LENGTH} 个字符` };
  }
  if (!new RegExp(`^${SKILL_NAME_PATTERN}$`).test(normalized)) {
    return { ok: false, error: "名称仅允许 ASCII 字母、数字、下划线和连字符" };
  }
  return { ok: true };
}

export interface TagValidationResult {
  ok: boolean;
  error?: string;
}

/** 规范化一条标签：严格 key=value、整体小写、去空白。 */
export function normalizeTag(raw: string): string | null {
  const trimmed = raw.trim().toLowerCase();
  const eq = trimmed.indexOf("=");
  if (eq <= 0 || eq === trimmed.length - 1) return null;
  return trimmed;
}

/** 规范化并校验标签数组（05 §12.5 统一规则；返回去重后的小写 key=value 列表）。 */
export function normalizeTags(raw: readonly string[]): TagValidationResult & { tags: string[] } {
  if (raw.length > SKILL_TAG_MAX_ITEMS) {
    return { ok: false, error: `标签最多 ${SKILL_TAG_MAX_ITEMS} 个`, tags: [] };
  }
  const tags: string[] = [];
  for (const item of raw) {
    const normalized = normalizeTag(item);
    if (normalized == null) {
      return { ok: false, error: "标签必须是 key=value 且 key/value 均非空", tags: [] };
    }
    if (normalized.length > SKILL_TAG_MAX_LENGTH) {
      return { ok: false, error: `单个标签最多 ${SKILL_TAG_MAX_LENGTH} 字符`, tags: [] };
    }
    if (!tags.includes(normalized)) tags.push(normalized);
  }
  return { ok: true, tags };
}

/** 逗号/换行分隔的标签输入（key=value 条目）→ 待校验数组。 */
export function splitTagsInput(value: string): string[] {
  return value.split(/[,，\n]/).map((s) => s.trim()).filter((s) => s.length > 0);
}

/** 逗号/换行分隔的 allowed_tools 输入 → 数组。 */
export function splitToolsInput(value: string): string[] {
  return value.split(/[,，\n]/).map((s) => s.trim()).filter((s) => s.length > 0);
}

/** 上传文件形态提示（10 §54.2；服务端仍执行 ZIP 安全校验，前端只做体验）。 */
export function describeUploadFile(filename: string): "skill_md" | "zip" | "other" {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".md")) {
    return "skill_md";
  }
  if (lower.endsWith(".zip")) {
    return "zip";
  }
  return "other";
}
