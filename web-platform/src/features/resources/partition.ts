/**
 * Resource 分区偏好（09 §38.1，P3-E4）。
 *
 * `/app/resources` 裸路由 → 最近使用的合法分区；首次默认 `/app/resources/private`。
 * 分区是「最近打开页面」类非敏感偏好，存 localStorage 白名单 key（06 §13.5）。
 */

import { getPreference, setPreference } from "@/lib/storage";

const LAST_PARTITION_KEY = "platform.resources-last-partition";

export type ResourcePartition = "private" | "shared";

export function readLastPartition(): ResourcePartition {
  const stored = getPreference<ResourcePartition>(LAST_PARTITION_KEY);
  return stored === "shared" ? "shared" : "private";
}

export function recordPartition(partition: ResourcePartition): void {
  try {
    setPreference(LAST_PARTITION_KEY, partition);
  } catch {
    // 非敏感偏好写失败不影响页面功能
  }
}
