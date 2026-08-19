/**
 * 发布为共享确认弹窗（09 §44，P3-E4 AC⑦）。
 *
 * - 仅 Account Admin 自己的私有 Resource 显示发布入口（09 §44.1）；
 * - 发布前展示：来源 Resource、目标 Account、文件/节点数量、大小、
 *   是否携带标签、不复制 Watch（09 §44.1 弹窗要素）；
 * - 发布生成新共享 ID、原对象保留、独立审计；Watch/私有关系不复制
 *   （09 §44.2，AC⑦）。
 */

import { useMemo } from "react";
import { useMe } from "@/features/auth/useAuth";
import {
  formatBytes,
  publishResource,
  resourceErrorMessage,
  type ResourceDetail,
} from "@/features/resources";
import { useState } from "react";

export interface PublishDialogProps {
  resource: ResourceDetail;
  onClose: () => void;
  onPublished: (newSharedResourceId: string) => void;
}

export default function PublishDialog({ resource, onClose, onPublished }: PublishDialogProps) {
  const me = useMe();
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accountLabel =
    me?.account?.name ?? me?.account?.code ?? me?.account?.id ?? "当前 Account";

  const targetId = useMemo(() => {
    const cryptoObj = globalThis.crypto as Crypto | undefined;
    if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
      return `resource-publish-${cryptoObj.randomUUID()}`;
    }
    return `resource-publish-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
  }, []);

  async function confirmPublish() {
    if (publishing) return;
    setPublishing(true);
    setError(null);
    try {
      const result = await publishResource(resource.id, targetId);
      onPublished(result.resource_id);
    } catch (publishError) {
      setError(resourceErrorMessage(publishError, "发布失败，请稍后重试。"));
      setPublishing(false);
    }
  }

  return (
    <div className="confirm-dialog-backdrop" role="dialog" aria-modal="true" aria-label="发布为共享">
      <div className="confirm-dialog">
        <h3>发布为共享 Resource</h3>
        {error ? (
          <p className="login-error" data-testid="publish-error">
            {error}
          </p>
        ) : null}
        <dl className="deletion-preview-list" data-testid="publish-preview">
          <div>
            <dt>来源 Resource</dt>
            <dd>{resource.name || "（未命名）"}（我的私有区）</dd>
          </div>
          <div>
            <dt>目标</dt>
            <dd>{accountLabel} 共享区（生成新的共享 Resource）</dd>
          </div>
          <div>
            <dt>内容</dt>
            <dd>
              {resource.content.node_count} 个文件，约 {formatBytes(resource.content.size_bytes)}
            </dd>
          </div>
          <div>
            <dt>标签</dt>
            <dd>{resource.tags.length > 0 ? `${resource.tags.length} 个（默认复制）` : "无"}</dd>
          </div>
          <div>
            <dt>自动同步</dt>
            <dd>不复制私有 Watch 配置（09 §44.2）；如需持续同步，请在新共享详情中重新启用</dd>
          </div>
        </dl>
        <p className="profile-hint">
          发布生成独立的新 Resource ID，原私有对象保留，之后两个对象独立更新与删除；不复制私有
          Activity、审计历史与可能含凭证的来源信息（09 §44.2）。
        </p>
        <div className="confirm-dialog-actions">
          <button
            type="button"
            className="primary-button"
            onClick={confirmPublish}
            disabled={publishing}
          >
            {publishing ? "发布中…" : "确认发布"}
          </button>
          <button type="button" onClick={onClose} disabled={publishing}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
