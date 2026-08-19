/**
 * Skill 上传区（10 §54.2/§61.5，P3-E5）。
 *
 * - 支持单个 SKILL.md 或包含 SKILL.md 的 ZIP；先经 `resource-uploads` 得
 *   `upload_id`，再随创建/替换请求消费（05 §12.5，AC④）；
 * - 创建模式要求填写 name（与包内 SKILL.md 一致）；替换模式 name 不可变，
 *   新包 name 必须与当前名称一致（SKILL_NAME_IMMUTABLE，AC④）；
 * - ZIP 路径穿越/绝对路径/符号链接/数量大小限制由服务端校验，页面不暴露
 *   底层参数（10 §54.2）。
 */

import { useState, type FormEvent } from "react";
import {
  describeUploadFile,
  uploadSkillFile,
  validateSkillName,
  type SkillScope,
} from "@/features/skills";

export interface SkillUploadSectionProps {
  scope: SkillScope;
  mode: "create" | "replace";
  /** 替换模式展示的当前名称（只读）。 */
  currentName?: string;
  submitLabel: string;
  busy: boolean;
  error?: string | null;
  onSubmit: (uploadId: string, name?: string) => Promise<void>;
  onCancel?: () => void;
}

export function SkillUploadSection({
  scope,
  mode,
  currentName,
  submitLabel,
  busy,
  error,
  onSubmit,
  onCancel,
}: SkillUploadSectionProps) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const fileHint = file ? describeUploadFile(file.name) : null;

  function handleFileChange(selected: File | null) {
    setFile(selected);
    setFormError(null);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    if (!file) {
      setFormError("请先选择文件（单个 SKILL.md 或 ZIP）");
      return;
    }
    if (fileHint === "other") {
      setFormError("仅支持 .md（SKILL.md）或 .zip 文件");
      return;
    }
    if (mode === "create") {
      const nameResult = validateSkillName(name);
      if (!nameResult.ok) {
        setFormError(nameResult.error ?? "名称不合法");
        return;
      }
    }
    uploadSkillFile(scope, file)
      .then((upload) => onSubmit(upload.upload_id, mode === "create" ? name.trim() : undefined))
      .catch((uploadError) => {
        setFormError(
          uploadError instanceof Error && "code" in uploadError
            ? `上传失败：${String((uploadError as { code: string }).code)}`
            : "上传失败，请稍后重试",
        );
      });
  }

  return (
    <form className="profile-form skill-form" onSubmit={handleSubmit} data-testid="skill-upload-form">
      {mode === "replace" && currentName ? (
        <label className="login-field">
          <span>当前名称（不可变）</span>
          <input type="text" value={currentName} readOnly disabled data-testid="skill-name-readonly" />
        </label>
      ) : null}
      {mode === "create" ? (
        <label className="login-field">
          <span>名称（必填：必须与包内 SKILL.md 的 name 一致）</span>
          <input
            type="text"
            name="name"
            required
            maxLength={64}
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={busy}
            data-testid="skill-upload-name"
          />
        </label>
      ) : null}
      <label className="login-field">
        <span>
          {mode === "replace"
            ? "整体重传文件（仅支持整体替换，不提供逐文件在线编辑；新包 name 必须与当前名称一致，AC④）"
            : "选择文件（单个 SKILL.md 或包含 SKILL.md 的 ZIP）"}
        </span>
        <input
          type="file"
          name="file"
          accept=".md,.zip"
          onChange={(e) => handleFileChange(e.target.files?.[0] ?? null)}
          disabled={busy}
          data-testid="skill-upload-file"
        />
        {file ? (
          <span className="skill-field-hint" data-testid="skill-upload-file-hint">
            已选择：{file.name}（{fileHint === "zip" ? "ZIP 包" : fileHint === "skill_md" ? "SKILL.md" : "格式不受支持"}）
          </span>
        ) : null}
      </label>
      {error || formError ? (
        <p className="login-error" role="alert" data-testid="skill-upload-error">
          {formError ?? error}
        </p>
      ) : null}
      <div className="profile-actions">
        <button type="submit" className="primary-button" disabled={busy || !file} data-testid="skill-upload-submit">
          {busy ? "上传中…" : submitLabel}
        </button>
        {onCancel ? (
          <button type="button" onClick={onCancel} disabled={busy} data-testid="skill-upload-cancel">
            取消
          </button>
        ) : null}
      </div>
    </form>
  );
}
