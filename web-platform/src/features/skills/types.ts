/**
 * Skill 产品 DTO 类型（10 §61 契约，05 §12.5，P3-E5）。
 *
 * - 与后端 `skills.skill_dto` / `SkillConfigSnapshot.dto` 逐字段对应；
 * - 任何 DTO 都不携带 Viking URI / 内部控制文件（10 §64.14，AC①）；
 * - 私密配置只返回 `value_masked`（固定掩码），不返回可恢复 Secret（AC⑧）。
 */

export type SkillVisibility = "user_private" | "account_shared";

/** 创建/更新形态：online=在线创建，skill_md=单个 SKILL.md 上传，zip=ZIP 包。 */
export type SkillSourceType = "online" | "skill_md" | "zip";

export interface SkillRecord {
  id: string;
  name: string;
  description: string;
  tags: string[];
  visibility: SkillVisibility;
  source_type: SkillSourceType;
  has_auxiliary_files: boolean;
  status: string;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  /** 管理/只读视图（成员列表）才返回；本人私有列表不返回。 */
  owner_user_id?: string | null;
}

export interface SkillFile {
  name: string;
  size_bytes: number;
}

export interface SkillDetail extends SkillRecord {
  content: string;
  allowed_tools: string[];
  files: SkillFile[];
}

export interface SkillListResult {
  items: SkillRecord[];
  next_cursor: string | null;
}

/** 软删除/恢复结果（05 §12.5/§12.6：restore_until=30 天恢复截止）。 */
export interface SkillDeletionResult {
  resource_type: string;
  resource_id: string;
  deletion_job_id: string;
  deleted_at: string;
  restore_until: string;
}

/** `me|account resource-uploads` 一次性上传产物（05 §12.5，10 §61.5）。 */
export interface SkillUploadResult {
  upload_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  expires_at: string;
}

export interface SkillConfigValue {
  key: string;
  configured: boolean;
  value_masked: string;
}

/** 私密配置脱敏快照（05 §12.5；values 全部为掩码值）。 */
export interface SkillConfigSnapshot {
  skill_id: string;
  configured: boolean;
  active_version: number | null;
  latest_version: number | null;
  versions: number[];
  values: SkillConfigValue[];
}

/** 在线创建/编辑提交（10 §54.1/§56.1；name 创建时必填、编辑时不接受）。 */
export interface SkillOnlineInput {
  name?: string;
  description: string;
  tags: string[];
  allowed_tools: string[];
  content: string;
}
