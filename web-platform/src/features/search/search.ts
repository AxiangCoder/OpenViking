/**
 * 统一检索数据层（11 §69/§74.1，05 §12.5，P3-E3 AC②③④）。
 *
 * - 两个模式：快速检索 `search/find`（不加载 Session）与
 *   结合会话检索 `search/search`（必须选择自己未删除的 Session）；
 * - 请求体只包含白名单字段（11 §69.2/§74：`query/context_type/tags/since/until`，
 *   结合会话模式追加 `session_id`）；前端类型化构造，无法携带
 *   `target_uri/filter/score_threshold/level/include_provenance/limit/node_limit/time_field`
 *   等调试字段（AC③）；
 * - 默认范围由服务端固定为「我的私有 + 当前 Account 共享」（AC②），
 *   客户端无法扩大根目录；
 * - 结果 DTO 由服务端脱敏：无 URI/Score/层级/Query Plan/Provenance/Relations
 *   （AC④）；Resource/Skill 附带产品 `ref_id` 用于详情跳转契约（lib/links.ts）。
 */

import { request } from "@/lib/platform-client";

export type SearchContextType = "memory" | "resource" | "skill";

export type SearchVisibility = "user_private" | "account_shared";

/** Search 请求白名单字段（05 §12.5：稳定产品字段；其余一律拒绝）。 */
export interface SearchParams {
  query: string;
  context_type?: SearchContextType | null;
  tags?: string[];
  since?: string | null;
  until?: string | null;
}

/** 服务端脱敏后的检索结果（05 §12.5 产品 DTO）。 */
export interface SearchHit {
  context_type: SearchContextType;
  display_name: string;
  abstract: string | null;
  match_reason: string | null;
  visibility: SearchVisibility;
  memory_type?: string | null;
  /** Resource/Skill 产品 ID（05 §12.5：内部 URI 映射为产品 ref，跳详情用）。 */
  ref_id?: string | null;
}

/** 请求体构造（白名单闸口；导出供白名单测试断言，业务代码只经 searchFind/searchWithSession 调用）。 */
export function buildSearchBody(params: SearchParams): Record<string, unknown> {
  // 仅白名单字段；`undefined` 不进入请求体（AC③：前端无法构造调试字段）
  const body: Record<string, unknown> = { query: params.query };
  if (params.context_type) body["context_type"] = params.context_type;
  if (params.tags && params.tags.length > 0) body["tags"] = params.tags;
  if (params.since) body["since"] = params.since;
  if (params.until) body["until"] = params.until;
  return body;
}

/** 快速检索（11 §69.1：默认模式，不使用 Session 上下文，AC②）。 */
export async function searchFind(params: SearchParams): Promise<SearchHit[]> {
  const result = await request<{ items: SearchHit[] }>("/api/platform/v1/search/find", {
    method: "POST",
    body: buildSearchBody(params),
  });
  return result.items;
}

/** 结合会话检索（11 §69.1：必须选择当前 User 自己且未删除的 Session）。 */
export async function searchWithSession(
  params: SearchParams,
  sessionId: string,
): Promise<SearchHit[]> {
  const result = await request<{ items: SearchHit[] }>("/api/platform/v1/search/search", {
    method: "POST",
    body: { ...buildSearchBody(params), session_id: sessionId },
  });
  return result.items;
}

/** 标签输入规范（11 §69.3：严格 key=value，去首尾空白后整体小写）。 */
export function normalizeTagInput(raw: string): string {
  return raw.trim().toLowerCase();
}

export function isValidTag(tag: string): boolean {
  const normalized = normalizeTagInput(tag);
  // 恰好一个 `=`，key/value 均非空（11 §69.3）
  const parts = normalized.split("=");
  if (parts.length !== 2) return false;
  const [key, value] = parts;
  return key.length > 0 && value.length > 0;
}

export function parseTagsInput(raw: string): string[] {
  const parts = raw.split(/[\s,，]+/).filter(Boolean);
  const seen = new Set<string>();
  const tags: string[] = [];
  for (const part of parts) {
    const normalized = normalizeTagInput(part);
    if (!isValidTag(normalized) || seen.has(normalized)) continue;
    seen.add(normalized);
    tags.push(normalized);
  }
  return tags;
}

/** 时间范围映射（11 §69.2：固定 `time_field=updated_at`，无创建/更新时间切换）。 */
export function toSinceUntil(
  since?: string | null,
  until?: string | null,
): { since: string | null; until: string | null } {
  return { since: since || null, until: until || null };
}
