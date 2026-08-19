/**
 * Skill 软删除确认弹窗（10 §59，P3-E5 AC⑥）。
 *
 * - 展示影响：名称立即释放、进入统一 30 天回收站、可恢复；
 * - 确认后调用方收到 `SkillDeletionResult`（含 restore_until），用于页内恢复提示。
 */

import type { SkillRecord } from "@/features/skills";

export interface SkillDeleteDialogProps {
  skill: SkillRecord;
  busy: boolean;
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

export function SkillDeleteDialog({ skill, busy, error, onConfirm, onCancel }: SkillDeleteDialogProps) {
  return (
    <div className="confirm-dialog" role="dialog" aria-label="删除 Skill 确认" data-testid="skill-delete-dialog">
      <h3>删除 Skill「{skill.name}」？</h3>
      <ul className="admin-dialog-list">
        <li>进入统一回收站，30 天内可恢复（恢复截止后不可再恢复）。</li>
        <li>名称将立即释放：同 Account 内可立即创建同名 Skill（10 §55.3，AC⑥）。</li>
        <li>恢复时若名称已被占用，将保持删除状态且不覆盖同名 Skill。</li>
      </ul>
      {error ? (
        <p className="login-error" role="alert" data-testid="skill-delete-error">
          {error}
        </p>
      ) : null}
      <div className="profile-actions">
        <button type="button" onClick={onCancel} disabled={busy} data-testid="skill-delete-cancel">
          取消
        </button>
        <button type="button" className="danger-button" onClick={onConfirm} disabled={busy} data-testid="skill-delete-confirm">
          {busy ? "删除中…" : "确认删除"}
        </button>
      </div>
    </div>
  );
}
