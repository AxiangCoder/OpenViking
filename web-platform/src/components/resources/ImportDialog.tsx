/**
 * 新增 Resource 弹窗（09 §40，P3-E4）。
 *
 * - 入口固定归属（AC①）：挂载在私有页 → 「保存到：我的 Resource」，
 *   共享管理页 → 「保存到：{Account} 共享 Resource」，无归属下拉切换；
 * - 来源：本地文件（每文件一个 Resource）/ 公开 HTTPS 网页 / 公开 HTTPS Git
 *   （09 §40.2）；批量按文件独立成败（09 §40.5，AC②）；
 * - 上传限制取自 capabilities（AC②：max_files_per_batch / max_file_size_bytes，
 *   前端只做浏览器预检，服务端仍独立强制）；
 * - 标签严格 key=value（AC④）；URL 禁 userinfo/私网（09 §40.6，AC⑧）；
 * - 上传/一次性 URL 导入完成后不可 Watch；稳定来源在详情页配置自动同步
 *   （09 §40.6：含 Query 的 URL 只做一次性导入）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchCapabilities,
  importResources,
  normalizeTags,
  resourceErrorMessage,
  uploadResourceFile,
  validateGitUrl,
  validateRemoteUrl,
  type ImportBatchItem,
  type ResourceCapabilities,
  type ResourceScopeKind,
} from "@/features/resources";

export type ImportSourceKind = "upload" | "web" | "git";

export interface ImportDialogProps {
  scope: ResourceScopeKind;
  targetLabel: string;
  onClose: () => void;
  onImported: (batchId: string) => void;
}

interface FileEntry {
  file: File;
  state: "pending" | "uploading" | "uploaded" | "failed";
  uploadId?: string;
  error?: string;
}

function newIdempotencyKey(): string {
  const cryptoObj = globalThis.crypto as Crypto | undefined;
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
    return `resource-import-${cryptoObj.randomUUID()}`;
  }
  return `resource-import-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
}

export default function ImportDialog({
  scope,
  targetLabel,
  onClose,
  onImported,
}: ImportDialogProps) {
  const [capabilities, setCapabilities] = useState<ResourceCapabilities | null>(null);
  const [sourceKind, setSourceKind] = useState<ImportSourceKind>("upload");
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [webUrl, setWebUrl] = useState("");
  const [gitUrl, setGitUrl] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tagsText, setTagsText] = useState("");
  const [instruction, setInstruction] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [resultItems, setResultItems] = useState<ImportBatchItem[] | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    fetchCapabilities()
      .then(setCapabilities)
      .catch((error) =>
        setFormError(
          resourceErrorMessage(error, "无法获取上传能力配置，请刷新后重试（AC②：限制取自服务端）。"),
        ),
      );
  }, []);

  const normalizedTags = useMemo(() => normalizeTags(tagsText.split(",")), [tagsText]);

  function addFiles(selected: FileList | null) {
    if (!selected || !capabilities) return;
    const maxFiles = capabilities.upload.max_files_per_batch;
    const maxBytes = capabilities.upload.max_file_size_bytes;
    setFormError(null);
    const next: FileEntry[] = [...files];
    for (const file of Array.from(selected)) {
      if (next.length >= maxFiles) {
        setFormError(`已超过每批文件数量上限（${maxFiles} 个），其余文件未添加。`);
        break;
      }
      if (file.size > maxBytes) {
        next.push({
          file,
          state: "failed",
          error: `超过单文件 ${(maxBytes / (1024 * 1024)).toFixed(0)} MiB 限制（Capabilities 返回）。`,
        });
        continue;
      }
      next.push({ file, state: "pending" });
    }
    setFiles(next);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function removeFile(index: number) {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }

  const validate = useCallback((): string | null => {
    if (sourceKind === "upload" && files.length === 0) {
      return "请先选择要上传的文件。";
    }
    if (sourceKind === "web" && !webUrl.trim()) {
      return "请输入公开网页 URL。";
    }
    if (sourceKind === "web") {
      const checked = validateRemoteUrl(webUrl);
      if (checked.error) return checked.error;
    }
    if (sourceKind === "git" && !gitUrl.trim()) {
      return "请输入公开 Git 仓库 URL。";
    }
    if (sourceKind === "git") {
      const checked = validateGitUrl(gitUrl);
      if (checked.error) return checked.error;
    }
    if (name.length > 128) return "名称最多 128 字符。";
    if (description.length > 1000) return "说明最多 1000 字符。";
    if (instruction.length > 2000) return "处理要求最多 2000 字符。";
    if (normalizedTags.error) return normalizedTags.error;
    return null;
  }, [sourceKind, files, webUrl, gitUrl, name, description, instruction, normalizedTags]);

  async function submit() {
    if (submitting) return;
    const invalid = validate();
    if (invalid) {
      setFormError(invalid);
      return;
    }
    setFormError(null);
    setSubmitting(true);
    setResultItems(null);
    try {
      // 1) 逐个上传文件：按文件独立成败（AC②），失败项不阻断其他文件
      const entries = await Promise.all(
        files.map(async (entry): Promise<FileEntry> => {
          if (entry.state === "uploaded") return entry;
          setFiles((prev) =>
            prev.map((item) => (item.file === entry.file ? { ...item, state: "uploading" } : item)),
          );
          try {
            const uploaded = await uploadResourceFile(scope, entry.file);
            return { ...entry, state: "uploaded", uploadId: uploaded.upload_id };
          } catch (error) {
            return {
              ...entry,
              state: "failed",
              error: resourceErrorMessage(error, "上传失败，请重新选择该文件。"),
            };
          }
        }),
      );
      setFiles(entries);

      const remoteItems =
        sourceKind === "web" || sourceKind === "git"
          ? [
              {
                sourceUrl: sourceKind === "web" ? webUrl.trim() : gitUrl.trim(),
                isGit: sourceKind === "git",
              },
            ]
          : [];

      const submitted = [
        ...entries
          .filter((entry) => entry.state === "uploaded" && entry.uploadId)
          .map((entry) => ({ uploadId: entry.uploadId as string })),
        ...remoteItems,
      ];

      const batch = await importResources(
        scope,
        submitted.map((item) => ({
          ...item,
          name: name.trim() || undefined,
          description: description.trim() || undefined,
          tags: normalizedTags.tags,
          instruction: instruction.trim() || undefined,
        })),
        newIdempotencyKey(),
      );
      setResultItems(batch.items);
      onImported(batch.batch_id);
    } catch (error) {
      setFormError(resourceErrorMessage(error, "导入请求失败，请稍后重试。"));
    } finally {
      setSubmitting(false);
    }
  }

  const allAccepted =
    resultItems !== null &&
    resultItems.length > 0 &&
    resultItems.every((item) => item.error === null);

  return (
    <div className="confirm-dialog-backdrop" role="dialog" aria-modal="true" aria-label="新增 Resource">
      <div className="confirm-dialog import-dialog">
        <h3>新增 Resource</h3>
        <p className="profile-hint">
          保存到：<strong>{targetLabel}</strong>（入口固定归属，无归属切换。如需其他范围，请关闭弹窗并前往对应页面，09 §40.1）
        </p>

        {formError ? (
          <p className="login-error" data-testid="import-error">
            {formError}
          </p>
        ) : null}

        <div className="import-source-tabs" role="tablist">
          {(["upload", "web", "git"] as const).map((kind) => (
            <button
              key={kind}
              type="button"
              className={sourceKind === kind ? "tab-active" : undefined}
              onClick={() => setSourceKind(kind)}
            >
              {kind === "upload" ? "本地文件" : kind === "web" ? "公开网页" : "公开 Git 仓库"}
            </button>
          ))}
        </div>

        {sourceKind === "upload" ? (
          <div className="import-upload-area">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              onChange={(event) => addFiles(event.target.files)}
              aria-label="选择文件"
            />
            {capabilities ? (
              <p className="profile-hint">
                每批最多 {capabilities.upload.max_files_per_batch} 个文件、单文件最大{" "}
                {(capabilities.upload.max_file_size_bytes / (1024 * 1024)).toFixed(0)} MiB
                （取自 Capabilities，AC②）；每个文件生成一个 Resource，上传文件不能开启自动同步（09 §40.5）。
              </p>
            ) : null}
            {files.length > 0 ? (
              <ul className="admin-dialog-list">
                {files.map((entry, index) => (
                  <li key={`${entry.file.name}-${index}`}>
                    <span>
                      {entry.file.name}（{(entry.file.size / 1024).toFixed(1)} KB）
                      {entry.state === "uploading" ? " · 上传中…" : null}
                      {entry.state === "uploaded" ? " · 已上传" : null}
                      {entry.state === "failed" ? " · 失败" : null}
                    </span>
                    {entry.state === "failed" && entry.error ? (
                      <span className="login-error">{entry.error}</span>
                    ) : null}
                    <button type="button" onClick={() => removeFile(index)} disabled={submitting}>
                      移除
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        {sourceKind === "web" ? (
          <div className="admin-form">
            <label>
              公开网页 URL（仅 HTTPS；不含 userinfo，禁止内网地址）
              <input
                type="url"
                value={webUrl}
                onChange={(event) => setWebUrl(event.target.value)}
                placeholder="https://example.com/article"
              />
            </label>
            <p className="profile-hint">
              含 Query 的 URL 只做一次性导入，不可开启自动同步（09 §40.6）。
            </p>
          </div>
        ) : null}

        {sourceKind === "git" ? (
          <div className="admin-form">
            <label>
              公开 Git 仓库 URL（仅 HTTPS，不支持 SSH/git@）
              <input
                type="url"
                value={gitUrl}
                onChange={(event) => setGitUrl(event.target.value)}
                placeholder="https://github.com/org/repo"
              />
            </label>
            <p className="profile-hint">默认保留仓库目录结构；产品固定 semantic_and_vectors 处理模式（09 §40.4）。</p>
          </div>
        ) : null}

        <div className="admin-form">
          <label>
            名称（可选，默认从文件名/页面标题/仓库名生成，1–128 字符）
            <input
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={128}
            />
          </label>
          <label>
            说明（可选，最多 1000 字符）
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              maxLength={1000}
              rows={2}
            />
          </label>
          <label>
            标签（可选，最多 20 个，严格 key=value，逗号分隔，自动转小写去重）
            <input
              type="text"
              value={tagsText}
              onChange={(event) => setTagsText(event.target.value)}
              placeholder="type=requirement, project=openviking"
            />
          </label>
          {normalizedTags.error ? (
            <span className="login-error">{normalizedTags.error}</span>
          ) : null}
          <label>
            处理要求（可选，最多 2000 字符）
            <textarea
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
              maxLength={2000}
              rows={2}
            />
          </label>
        </div>

        {resultItems ? (
          <div className="import-result" data-testid="import-result">
            <p>
              {allAccepted
                ? "导入请求已提交，正在处理。"
                : "部分条目未通过校验，其余已提交处理（按文件独立成败）。"}
            </p>
            <ul className="admin-dialog-list">
              {resultItems.map((item, index) => (
                <li key={index}>
                  {item.error ? (
                    <span className="login-error">
                      条目失败：{item.error.message}
                      {item.error.retryable ? "（可重试）" : ""}
                    </span>
                  ) : (
                    <span>已受理，进入处理中（成功/失败按处理状态更新）。</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="confirm-dialog-actions">
          <button type="button" className="primary-button" onClick={submit} disabled={submitting || !capabilities}>
            {submitting ? "提交中…" : "开始导入"}
          </button>
          <button type="button" onClick={onClose} disabled={submitting}>
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}
