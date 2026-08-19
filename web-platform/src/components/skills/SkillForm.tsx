/**
 * Skill 在线创建/编辑表单（10 §54.1/§56.1，P3-E5）。
 *
 * - 表单不要求手写 YAML Frontmatter，前端序列化为合法 SKILL.md 由产品服务处理；
 * - 创建模式 name 必填且按 `validate_skill_name` 规则校验（AC②）；
 * - 编辑模式不提供 name 字段（创建后不可修改，AC③）；
 * - 标签严格 `key=value`（05 §12.5 统一规则）；不提交 `target_uri`（AC②）。
 */

import { useMemo, useState, type FormEvent } from "react";
import {
  normalizeTags,
  splitTagsInput,
  splitToolsInput,
  validateSkillName,
  type SkillOnlineInput,
} from "@/features/skills";

export interface SkillFormProps {
  mode: "create" | "edit";
  /** 编辑模式初始值（含只读 name 展示）。 */
  initial?: { name?: string; description?: string; tags?: string[]; allowed_tools?: string[]; content?: string };
  submitLabel: string;
  busy: boolean;
  error?: string | null;
  onSubmit: (input: SkillOnlineInput) => Promise<void>;
  onCancel?: () => void;
}

export function SkillForm({ mode, initial, submitLabel, busy, error, onSubmit, onCancel }: SkillFormProps) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [tagsInput, setTagsInput] = useState((initial?.tags ?? []).join(", "));
  const [toolsInput, setToolsInput] = useState((initial?.allowed_tools ?? []).join(", "));
  const [content, setContent] = useState(initial?.content ?? "");
  const [formError, setFormError] = useState<string | null>(null);

  const nameHint = useMemo(() => {
    if (mode === "create") {
      const result = validateSkillName(name);
      return result.ok ? null : result.error ?? null;
    }
    return null;
  }, [mode, name]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    if (mode === "create") {
      const nameResult = validateSkillName(name);
      if (!nameResult.ok) {
        setFormError(nameResult.error ?? "名称不合法");
        return;
      }
    }
    if (!description.trim()) {
      setFormError("描述不能为空");
      return;
    }
    if (!content.trim()) {
      setFormError("正文内容不能为空");
      return;
    }
    const tagResult = normalizeTags(splitTagsInput(tagsInput));
    if (!tagResult.ok) {
      setFormError(tagResult.error ?? "标签不合法");
      return;
    }
    const input: SkillOnlineInput = {
      ...(mode === "create" ? { name: name.trim() } : {}),
      description: description.trim(),
      tags: tagResult.tags,
      allowed_tools: splitToolsInput(toolsInput),
      content,
    };
    void onSubmit(input);
  }

  return (
    <form className="profile-form skill-form" onSubmit={handleSubmit} data-testid="skill-form">
      {mode === "edit" && initial?.name ? (
        <label className="login-field">
          <span>名称（创建后不可修改，AC③）</span>
          <input type="text" value={initial.name} readOnly disabled data-testid="skill-name-readonly" />
        </label>
      ) : null}
      {mode === "create" ? (
        <label className="login-field">
          <span>名称（必填：仅 ASCII 字母/数字/下划线/连字符，≤64 字符）</span>
          <input
            type="text"
            name="name"
            required
            maxLength={64}
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={busy}
            data-testid="skill-name"
            aria-invalid={nameHint != null && name.length > 0}
          />
          {nameHint && name.length > 0 ? <span className="skill-field-hint">{nameHint}</span> : null}
        </label>
      ) : null}
      <label className="login-field">
        <span>描述（必填）</span>
        <input
          type="text"
          name="description"
          required
          maxLength={1024}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          disabled={busy}
          data-testid="skill-description"
        />
      </label>
      <label className="login-field">
        <span>标签（可选，key=value，逗号分隔，最多 20 个）</span>
        <input
          type="text"
          name="tags"
          value={tagsInput}
          onChange={(e) => setTagsInput(e.target.value)}
          disabled={busy}
          placeholder="例如 project=openviking, type=guide"
          data-testid="skill-tags"
        />
      </label>
      <label className="login-field">
        <span>工具范围 allowed_tools（可选，逗号分隔）</span>
        <input
          type="text"
          name="allowed_tools"
          value={toolsInput}
          onChange={(e) => setToolsInput(e.target.value)}
          disabled={busy}
          placeholder="例如 read_file, write_file"
          data-testid="skill-tools"
        />
      </label>
      <label className="login-field">
        <span>正文 Markdown（必填；由产品服务序列化为合法 SKILL.md，无需手写 YAML）</span>
        <textarea
          name="content"
          required
          rows={10}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          disabled={busy}
          data-testid="skill-content"
        />
      </label>
      {error || formError ? (
        <p className="login-error" role="alert" data-testid="skill-form-error">
          {formError ?? error}
        </p>
      ) : null}
      <div className="profile-actions">
        <button type="submit" className="primary-button" disabled={busy} data-testid="skill-submit">
          {busy ? "提交中…" : submitLabel}
        </button>
        {onCancel ? (
          <button type="button" onClick={onCancel} disabled={busy} data-testid="skill-cancel">
            取消
          </button>
        ) : null}
      </div>
    </form>
  );
}
