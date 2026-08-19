/**
 * 个人 API Key 数据层（13 §82，05 §12.4，P3-E2 AC③④⑦）。
 *
 * - 列表只返回元数据与末四位掩码，无明文（82.3，AC③）；
 * - 创建返回完整明文 `ovk_u.<public>.<secret>` 仅此一次（82.4，AC③）；
 * - 撤销幂等：重复撤销/不存在 → 404 `KEY_NOT_FOUND`，按 82.5「幂等成功提示」处理；
 * - 前端任何位置不得把明文写入 localStorage/sessionStorage/URL（06 §13.5，AC④）。
 */

import { request, PlatformError } from "@/lib/platform-client";

export interface ApiKeyRecord {
  id: string;
  name: string;
  key_last_four: string;
  status: string;
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
}

export interface CreatedApiKey extends ApiKeyRecord {
  /** 完整明文，仅创建响应存在一次（05 §12.4）。 */
  api_key: string;
}

export interface CreateApiKeyInput {
  name: string;
  expires_at: string | null;
}

export async function listApiKeys(): Promise<ApiKeyRecord[]> {
  return request<ApiKeyRecord[]>("/api/platform/v1/me/api-keys");
}

export async function createApiKey(input: CreateApiKeyInput): Promise<CreatedApiKey> {
  return request<CreatedApiKey>("/api/platform/v1/me/api-keys", {
    method: "POST",
    body: { name: input.name, ...(input.expires_at ? { expires_at: input.expires_at } : {}) },
  });
}

/** 撤销自己的 Key；已撤销/不存在按幂等成功处理（82.5，05 §12.4 撤销幂等）。 */
export async function revokeApiKey(keyId: string): Promise<void> {
  try {
    await request<{ status: string }>(`/api/platform/v1/me/api-keys/${keyId}`, {
      method: "DELETE",
    });
  } catch (error) {
    if (error instanceof PlatformError && error.code === "KEY_NOT_FOUND") {
      return;
    }
    throw error;
  }
}

export function maskKey(record: ApiKeyRecord): string {
  return `••••••••${record.key_last_four}`;
}

export function isExpired(record: ApiKeyRecord): boolean {
  return record.expires_at != null && new Date(record.expires_at).getTime() <= Date.now();
}
