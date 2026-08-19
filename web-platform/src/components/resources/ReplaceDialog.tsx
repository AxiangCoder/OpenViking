/**
 * 上传文件替换弹窗（09 §43.1，P3-E4 AC⑤）。
 * 新文件 → 同一 Resource `resource_replace` Operation；处理期间旧版本保持可读。
 */

import { useRef, useState } from "react";
import {
  replaceResource,
  resourceErrorMessage,
  uploadResourceFile,
  type ResourceCapabilities,
  type ResourceScopeKind,
} from "@/features/resources";

export interface ReplaceDialogProps {
  scope: ResourceScopeKind;
  resourceId: string;
  capabilities: ResourceCapabilities | null;
  onClose: () => void;
  onReplaced: () => void;
}

export default function ReplaceDialog({
  scope,
  resourceId,
  capabilities,
  onClose,
  onReplaced,
}: ReplaceDialogProps) {
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const maxBytes = capabilities?.upload.max_file_size_bytes;

  async function submit() {
    if (!file || submitting) return;
    if (maxBytes && file.size > maxBytes) {
      setError(`文件超过单文件 ${(maxBytes / (1024 * 1024)).toFixed(0)} MiB 限制（Capabilities 返回）。`);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const uploaded = await uploadResourceFile(scope, file);
      await replaceResource(scope, resourceId, uploaded.upload_id);
      onReplaced();
    } catch (replaceError) {
      setError(resourceErrorMessage(replaceError, "替换失败，请稍后重试。"));
      setSubmitting(false);
    }
  }

  return (
    <div className="confirm-dialog-backdrop" role="dialog" aria-modal="true" aria-label="替换文件">
      <div className="confirm-dialog">
        <h3>替换来源文件</h3>
        <p className="profile-hint">
          上传新文件并对同一 Resource 产生替换任务；处理期间旧成功版本保持可读，完成后原子切换
          （09 §43.1/§41.2，AC⑤）。处理失败时 Resource 保持可用。
        </p>
        {error ? (
          <p className="login-error" data-testid="replace-error">
            {error}
          </p>
        ) : null}
        <div className="import-upload-area">
          <input
            ref={inputRef}
            type="file"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setError(null);
            }}
            aria-label="选择替换文件"
          />
          {file ? <p className="profile-hint">已选择：{file.name}</p> : null}
        </div>
        <div className="confirm-dialog-actions">
          <button
            type="button"
            className="primary-button"
            onClick={submit}
            disabled={!file || submitting}
          >
            {submitting ? "提交中…" : "上传并替换"}
          </button>
          <button type="button" onClick={onClose} disabled={submitting}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
