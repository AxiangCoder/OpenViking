/**
 * 元数据编辑弹窗（09 §42.4，P3-E4 AC④）。
 *
 * - 可编辑字段只有 display_name / description / tags（09 §42.4）；
 * - 乐观锁：携带当前 version，冲突返回 RESOURCE_VERSION_CONFLICT →
 *   重新加载最新数据并让用户决定是否再次提交（AC④）；
 * - 标签严格 key=value（AC④，与服务端 normalize_tags 同规则）；
 * - 名称修改不移动 URI、不重建索引、不改变分享链接（09 §42.4）。
 */

import { useMemo, useState } from "react";
import { isPlatformError } from "@/lib/platform-client";
import {
  normalizeTags,
  patchResource,
  resourceErrorMessage,
  type ResourceScopeKind,
  type ResourceSummary,
} from "@/features/resources";

export interface EditMetaDialogProps {
  scope: ResourceScopeKind;
  resource: ResourceSummary;
  onClose: () => void;
  /** 版本冲突时重新加载并返回最新数据（AC④）。 */
  onReload: () => Promise<ResourceSummary | null>;
  onSaved: (updated: ResourceSummary) => void;
}

export default function EditMetaDialog({
  scope,
  resource,
  onClose,
  onReload,
  onSaved,
}: EditMetaDialogProps) {
  const [name, setName] = useState(resource.name);
  const [description, setDescription] = useState(resource.description ?? "");
  const [tagsText, setTagsText] = useState((resource.tags ?? []).join(", "));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const normalizedTags = useMemo(() => normalizeTags(tagsText.split(",")), [tagsText]);

  async function submit() {
    if (saving) return;
    if (name.trim().length === 0) {
      setError("名称不能为空。");
      return;
    }
    if (name.length > 128) {
      setError("名称最多 128 字符。");
      return;
    }
    if (description.length > 1000) {
      setError("说明最多 1000 字符。");
      return;
    }
    if (normalizedTags.error) {
      setError(normalizedTags.error);
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const updated = await patchResource(scope, resource.id, {
        displayName: name.trim(),
        description: description.trim(),
        tags: normalizedTags.tags,
        version: resource.version,
      });
      onSaved(updated);
      onClose();
    } catch (error) {
      if (
        isPlatformError(error) &&
        (error.code === "RESOURCE_VERSION_CONFLICT" || error.status === 409)
      ) {
        // AC④：冲突 → 重新加载，让用户决定是否再次提交
        setError(resourceErrorMessage(error, "版本冲突。"));
        const latest = await onReload();
        if (latest) {
          setError(
            "该 Resource 已在其他窗口被更新（版本冲突）。页面已重新加载最新信息，请确认后再次提交。",
          );
        }
      } else {
        setError(resourceErrorMessage(error, "保存失败，请稍后重试。"));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="confirm-dialog-backdrop" role="dialog" aria-modal="true" aria-label="编辑 Resource 信息">
      <div className="confirm-dialog">
        <h3>编辑 Resource 信息</h3>
        {error ? (
          <p className="login-error" data-testid="edit-error">
            {error}
          </p>
        ) : null}
        <div className="admin-form">
          <label>
            名称（1–128 字符；修改不影响链接与索引，09 §42.4）
            <input
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={128}
            />
          </label>
          <label>
            说明（最多 1000 字符）
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              maxLength={1000}
              rows={3}
            />
          </label>
          <label>
            标签（最多 20 个，严格 key=value，逗号分隔，自动转小写去重）
            <input
              type="text"
              value={tagsText}
              onChange={(event) => setTagsText(event.target.value)}
              placeholder="type=requirement, project=openviking"
            />
          </label>
          {normalizedTags.error ? <span className="login-error">{normalizedTags.error}</span> : null}
        </div>
        <div className="confirm-dialog-actions">
          <button type="button" className="primary-button" onClick={submit} disabled={saving}>
            {saving ? "保存中…" : "保存"}
          </button>
          <button type="button" onClick={onClose} disabled={saving}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
