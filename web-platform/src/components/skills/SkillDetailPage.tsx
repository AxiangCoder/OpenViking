/**
 * Skill 详情页（10 §53.3/§55.2/§56/§59，P3-E5）。
 *
 * - 视图：概览/使用说明/文件清单/工具范围/使用提示 + 私密配置（仅当前 User
 *   管理自己的配置，AC⑤⑧）；
 * - 编辑：在线 Skill 可编辑 description/tags/allowed_tools/content，name 只读
 *   不可变（AC③）；ZIP Skill 仅整体重传（AC④）；
 * - 删除：确认弹窗 → 软删 → 页内显示删除结果与恢复入口（30 天，AC⑥）；
 * - 无「在新 Session 中使用」或执行器入口（AC⑤）；
 * - 页面 URL 与网络请求不携带 Viking URI/控制文件（AC①）。
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import {
  getSkill,
  restoreSkill,
  skillErrorMessage,
  softDeleteSkill,
  replaceSkillFromUpload,
  updateSkillOnline,
  type SkillDeletionResult,
  type SkillDetail,
  type SkillOnlineInput,
  type SkillScope,
} from "@/features/skills";
import { SkillDetailSections } from "./SkillDetailSections";
import { SkillForm } from "./SkillForm";
import { SkillUploadSection } from "./SkillUploadSection";
import { SkillConfigPanel } from "./SkillConfigPanel";
import { SkillDeleteDialog } from "./SkillDeleteDialog";

export interface SkillDetailPageProps {
  scope: SkillScope;
  skillId: string;
  ownershipLabel: string;
  /** 管理能力（私有=本人；共享=仅 Account Admin）。 */
  canManage: boolean;
  backHref: string;
}

type ViewMode = "view" | "edit" | "replace";

export function SkillDetailPage({
  scope,
  skillId,
  ownershipLabel,
  canManage,
  backHref,
}: SkillDetailPageProps) {
  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mode, setMode] = useState<ViewMode>("view");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleted, setDeleted] = useState<SkillDeletionResult | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoadError(null);
    getSkill(scope, skillId)
      .then(setSkill)
      .catch((error) => {
        if (isPlatformError(error)) {
          setLoadError(error.code);
        } else {
          setLoadError("INTERNAL");
        }
      });
  }, [scope, skillId]);

  useEffect(() => {
    load();
  }, [load]);

  function handleEditSubmit(input: SkillOnlineInput): Promise<void> {
    setBusy(true);
    setActionError(null);
    return updateSkillOnline(scope, skillId, input)
      .then((next) => {
        setSkill(next);
        setMode("view");
      })
      .catch((error) => setActionError(skillErrorMessage(error)))
      .finally(() => setBusy(false));
  }

  function handleReplaceSubmit(uploadId: string): Promise<void> {
    setBusy(true);
    setActionError(null);
    return replaceSkillFromUpload(scope, skillId, uploadId)
      .then((next) => {
        setSkill(next);
        setMode("view");
      })
      .catch((error) => setActionError(skillErrorMessage(error)))
      .finally(() => setBusy(false));
  }

  function handleDeleteConfirm() {
    setBusy(true);
    setActionError(null);
    softDeleteSkill(scope, skillId)
      .then((result) => {
        setConfirmDelete(false);
        setDeleted(result);
        setSkill(null);
      })
      .catch((error) => setActionError(skillErrorMessage(error)))
      .finally(() => setBusy(false));
  }

  function handleRestore() {
    if (restoring) return;
    setRestoring(true);
    setRestoreError(null);
    restoreSkill(scope, skillId)
      .then(() => {
        setDeleted(null);
        load();
      })
      .catch((error) => {
        setRestoreError(
          isPlatformError(error) && error.code === "SKILL_NAME_CONFLICT"
            ? "恢复失败：该名称在当前 Account 不可用，原 Skill 保持在回收站，不会覆盖同名 Skill（10 §55.3）。"
            : skillErrorMessage(error),
        );
      })
      .finally(() => setRestoring(false));
  }

  // 404 语义（跳转契约 lib/links.ts：非法/已删/无权 → 404 展示）
  if (loadError === "NOT_FOUND") {
    return (
      <div className="card" data-testid="skill-not-found">
        <h2>未找到该 Skill</h2>
        <p className="profile-hint">该 Skill 可能已被删除，或您没有访问权限。</p>
        <Link to={backHref} className="placeholder-back">
          返回列表
        </Link>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="card" data-testid="skill-load-error">
        <h2>加载失败</h2>
        <p className="login-error" role="alert">
          无法加载该 Skill（{loadError}），请稍后重试。
        </p>
        <Link to={backHref} className="placeholder-back">
          返回列表
        </Link>
      </div>
    );
  }

  if (deleted) {
    return (
      <div className="card" data-testid="skill-deleted-view">
        <h2>Skill 已删除</h2>
        <ul className="admin-dialog-list">
          <li>名称已立即释放（10 §55.3，AC⑥）。</li>
          <li>
            恢复截止时间：{new Date(deleted.restore_until).toLocaleString()}（30 天回收窗口）。
          </li>
          <li>恢复时若名称已被占用，将保持删除状态。</li>
        </ul>
        {restoreError ? (
          <p className="login-error" role="alert" data-testid="skill-restore-error">
            {restoreError}
          </p>
        ) : null}
        <div className="profile-actions">
          <button
            type="button"
            className="primary-button"
            onClick={handleRestore}
            disabled={restoring}
            data-testid="skill-restore"
          >
            {restoring ? "恢复中…" : "恢复该 Skill"}
          </button>
          <Link to={backHref} className="placeholder-back" data-testid="skill-deleted-back">
            返回列表
          </Link>
        </div>
      </div>
    );
  }

  if (skill == null) {
    return <p>加载中…</p>;
  }

  if (mode === "edit") {
    return (
      <div className="card" data-testid="skill-edit-form">
        <h2>编辑 Skill</h2>
        <SkillForm
          mode="edit"
          initial={{
            name: skill.name,
            description: skill.description,
            tags: skill.tags,
            allowed_tools: skill.allowed_tools,
            content: skill.content,
          }}
          submitLabel="保存修改"
          busy={busy}
          error={actionError}
          onSubmit={handleEditSubmit}
          onCancel={() => {
            setMode("view");
            setActionError(null);
          }}
        />
      </div>
    );
  }

  if (mode === "replace") {
    return (
      <div className="card" data-testid="skill-replace-form">
        <h2>整体替换 Skill</h2>
        <SkillUploadSection
          scope={scope}
          mode="replace"
          currentName={skill.name}
          submitLabel="整体替换"
          busy={busy}
          error={actionError}
          onSubmit={handleReplaceSubmit}
          onCancel={() => {
            setMode("view");
            setActionError(null);
          }}
        />
      </div>
    );
  }

  return (
    <div className="skill-page">
      <div className="admin-page-header">
        <h2 data-testid="skill-page-title">{skill.name}</h2>
        <span className="status-badge">{ownershipLabel}</span>
        {skill.has_auxiliary_files ? <span className="status-badge active">含辅助文件</span> : null}
      </div>
      <SkillDetailSections skill={skill} ownershipLabel={ownershipLabel} />
      <section className="card" aria-label="私密配置" data-testid="skill-config-section">
        <h2>私密配置（仅本人）</h2>
        <SkillConfigPanel skillId={skillId} />
      </section>
      {canManage ? (
        <section className="card" aria-label="管理操作" data-testid="skill-manage-section">
          <h2>管理</h2>
          {actionError ? (
            <p className="login-error" role="alert" data-testid="skill-action-error">
              {actionError}
            </p>
          ) : null}
          <div className="profile-actions">
            {skill.source_type !== "zip" ? (
              <button
                type="button"
                className="primary-button"
                onClick={() => {
                  setActionError(null);
                  setMode("edit");
                }}
                disabled={busy}
                data-testid="skill-edit"
              >
                编辑
              </button>
            ) : null}
            {skill.source_type === "zip" ? (
              <button
                type="button"
                className="primary-button"
                onClick={() => {
                  setActionError(null);
                  setMode("replace");
                }}
                disabled={busy}
                data-testid="skill-replace"
              >
                整体替换
              </button>
            ) : null}
            <button
              type="button"
              className="danger-button"
              onClick={() => {
                setActionError(null);
                setConfirmDelete(true);
              }}
              disabled={busy}
              data-testid="skill-delete"
            >
              删除
            </button>
          </div>
          {confirmDelete ? (
            <SkillDeleteDialog
              skill={skill}
              busy={busy}
              onConfirm={handleDeleteConfirm}
              onCancel={() => setConfirmDelete(false)}
            />
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
