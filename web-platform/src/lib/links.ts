/**
 * 跨页面跳转契约（14 号计划 §98.1 冻结点，P3-E1）。
 *
 * Search 结果 → Resource/Skill 详情链接统一使用「产品 ID」（内部 UUID/ULID，
 * 05 §12.2），URL 中不得出现 Viking URI / Account ID / User ID（AC⑥）。
 *
 * 约定：
 * - `/app/resources/private/$resourceId`   我的 Resource 详情（P3-E4 交付页面）
 * - `/app/resources/shared/$resourceId`   Account 共享 Resource 详情（P3-E4 交付页面）
 * - `/app/skills/private/$skillId`         我的 Skill 详情（P3-E5 交付页面）
 * - `/app/skills/shared/$skillId`          Account 共享 Skill 详情（P3-E5 交付页面）
 * - 参数值必须是产品 ID（isProductId 校验），跳转目标页面对非法 ID 返回 404 语义；
 * - E3 在 E4/E5 未完成时仍可交付链路：Search 结果点击跳详情，目标页由占位页
 *   展示 404/「页面由 P3-E4/E5 交付」语义。
 */

export type Visibility = "private" | "shared";

export const RESOURCE_DETAIL_PATH = {
  private: "/app/resources/private",
  shared: "/app/resources/shared",
} as const;

export const SKILL_DETAIL_PATH = {
  private: "/app/skills/private",
  shared: "/app/skills/shared",
} as const;

const PRODUCT_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** 产品 ID 校验（UUID 形态；ULID 等内部标识符不得出现在 URL）。 */
export function isProductId(id: string): boolean {
  return PRODUCT_ID_PATTERN.test(id);
}

export function resourceDetailPath(visibility: Visibility, resourceId: string): string {
  if (!isProductId(resourceId)) {
    throw new Error(`非产品 ID 的跳转参数被拒绝：${resourceId}（跳转契约：URL 无内部标识符）`);
  }
  return `${RESOURCE_DETAIL_PATH[visibility]}/${resourceId}`;
}

export function skillDetailPath(visibility: Visibility, skillId: string): string {
  if (!isProductId(skillId)) {
    throw new Error(`非产品 ID 的跳转参数被拒绝：${skillId}（跳转契约：URL 无内部标识符）`);
  }
  return `${SKILL_DETAIL_PATH[visibility]}/${skillId}`;
}

/** Search 结果对象的跳转目标映射（P3-E3 使用；先按 Product ID 契约冻结）。 */
export interface SearchTarget {
  kind: "resource" | "skill";
  visibility: Visibility;
  productId: string;
}

export function searchTargetPath(target: SearchTarget): string {
  return target.kind === "resource"
    ? resourceDetailPath(target.visibility, target.productId)
    : skillDetailPath(target.visibility, target.productId);
}
