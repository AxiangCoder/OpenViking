/**
 * Resource 内容树与预览（09 §42.3，P3-E4 AC③）。
 *
 * - 根固定为当前 Resource：客户端只能以 resource_id 打开，不能提交其他
 *   路径/URI；目录按需展开（node_id 不透明，09 §42.3）；
 * - 控制文件（.abstract.md 等）由服务端隐藏（09 §42.3，AC③）；
 * - 文本预览以纯文本渲染（React 默认转义，HTML/SVG 不执行来源脚本，
 *   AC③ 预览沙箱化）；
 * - 下载使用服务端安全文件名/Content-Disposition（AC③）；可执行文件
 *   与高风险类型不内联（服务端保证）。
 */

import { useEffect, useState, type FormEvent } from "react";
import {
  formatBytes,
  listResourceNodes,
  readResourceNode,
  resourceErrorMessage,
  resourceNodeDownloadUrl,
  searchResourceNodes,
  type ResourceNode,
  type ResourceNodeDetail,
  type ResourceScopeKind,
} from "@/features/resources";

export interface NodeTreeProps {
  scope: ResourceScopeKind;
  resourceId: string;
  resourceName: string;
}

interface ExpandedDir {
  nodeId: string | null;
  children: ResourceNode[];
  loading: boolean;
  error: string | null;
}

export default function NodeTree({ scope, resourceId, resourceName }: NodeTreeProps) {
  const [root, setRoot] = useState<ExpandedDir>({ nodeId: null, children: [], loading: true, error: null });
  const [expanded, setExpanded] = useState<Record<string, ExpandedDir>>({});
  const [preview, setPreview] = useState<ResourceNodeDetail | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<
    { node_id: string; name: string; snippet: string }[] | null
  >(null);
  const [searching, setSearching] = useState(false);

  function loadDir(
    state: ExpandedDir,
    apply: (next: ExpandedDir) => void,
  ) {
    apply({ ...state, loading: true, error: null });
    listResourceNodes(scope, resourceId, state.nodeId ?? undefined)
      .then((page) => apply({ ...state, children: page.items, loading: false, error: null }))
      .catch((error) =>
        apply({ ...state, loading: false, error: resourceErrorMessage(error, "无法加载目录。") }),
      );
  }

  const loadRoot = () => loadDir(root, setRoot);

  // AC③：根固定为当前 Resource（挂载即加载根目录，不提交任何路径/URI）
  useEffect(() => {
    loadRoot();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, resourceId]);

  function toggleDir(node: ResourceNode) {
    setPreview(null);
    const existing = expanded[node.node_id];
    if (existing) {
      const next = { ...expanded };
      delete next[node.node_id];
      setExpanded(next);
      return;
    }
    const entry: ExpandedDir = { nodeId: node.node_id, children: [], loading: true, error: null };
    setExpanded((prev) => ({ ...prev, [node.node_id]: entry }));
    loadDir(entry, (next) =>
      setExpanded((prev) => ({ ...prev, [node.node_id]: next })),
    );
  }

  function openPreview(node: ResourceNode) {
    if (node.type !== "file") return;
    setPreview(null);
    setPreviewLoading(true);
    setPreviewError(null);
    readResourceNode(scope, resourceId, node.node_id)
      .then(setPreview)
      .catch((error) => setPreviewError(resourceErrorMessage(error, "无法读取预览。")))
      .finally(() => setPreviewLoading(false));
  }

  function handleSearch(event: FormEvent) {
    event.preventDefault();
    const query = searchQuery.trim();
    if (!query) return;
    setSearching(true);
    setSearchResults(null);
    searchResourceNodes(scope, resourceId, query)
      .then((result) => setSearchResults(result.items))
      .catch((error) => setPreviewError(resourceErrorMessage(error, "资源内检索失败。")))
      .finally(() => setSearching(false));
  }

  return (
    <div data-testid="node-tree">
      <form className="admin-filter-bar" onSubmit={handleSearch}>
        <input
          type="search"
          placeholder="在当前 Resource 内检索"
          value={searchQuery}
          onChange={(event) => setSearchQuery(event.target.value)}
          aria-label="资源内检索"
        />
        <button type="submit" className="primary-button" disabled={searching}>
          {searching ? "检索中…" : "检索"}
        </button>
      </form>

      {searchResults ? (
        <div className="card">
          <h4>检索结果（{searchResults.length}）</h4>
          {searchResults.length === 0 ? <p className="empty-state">无匹配内容</p> : null}
          <ul className="admin-dialog-list">
            {searchResults.map((hit) => (
              <li key={hit.node_id}>{hit.name}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="card">
        <h4>
          {resourceName} · 内容
          <button type="button" className="filter-clear" onClick={loadRoot}>
            刷新
          </button>
        </h4>
        {root.loading ? <p className="profile-hint">加载中…</p> : null}
        {root.error ? (
          <p className="login-error">
            {root.error} <button type="button" onClick={loadRoot}>重试</button>
          </p>
        ) : null}
        {!root.loading && !root.error && root.children.length === 0 ? (
          <p className="empty-state">该 Resource 暂无可预览的文件节点。</p>
        ) : null}
        <ul className="node-tree">
          {root.children.map((node) => (
            <li key={node.node_id}>
              <NodeRow
                node={node}
                onToggle={() => toggleDir(node)}
                onPreview={() => openPreview(node)}
                downloadUrl={resourceNodeDownloadUrl(scope, resourceId, node.node_id)}
              />
              {expanded[node.node_id] ? (
                <ul className="node-tree node-children">
                  {expanded[node.node_id].loading ? <li className="profile-hint">加载中…</li> : null}
                  {expanded[node.node_id].error ? (
                    <li className="login-error">{expanded[node.node_id].error}</li>
                  ) : null}
                  {expanded[node.node_id].children.map((child) => (
                    <li key={child.node_id}>
                      <NodeRow
                        node={child}
                        onToggle={() => toggleDir(child)}
                        onPreview={() => openPreview(child)}
                        downloadUrl={resourceNodeDownloadUrl(scope, resourceId, child.node_id)}
                      />
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      </div>

      {previewLoading ? <p className="profile-hint">正在加载预览…</p> : null}
      {previewError ? (
        <p className="login-error" data-testid="preview-error">
          {previewError}
        </p>
      ) : null}
      {preview ? (
        <div className="card node-preview" data-testid="node-preview">
          <h4>
            {preview.path}
            <span className="resource-source-display">
              {formatBytes(preview.size_bytes)}
              {preview.mime_type ? ` · ${preview.mime_type}` : ""}
            </span>
            <a
              href={resourceNodeDownloadUrl(scope, resourceId, preview.node_id)}
              className="filter-clear"
            >
              下载
            </a>
          </h4>
          {preview.preview != null ? (
            <pre className="node-preview-text">{preview.preview}</pre>
          ) : (
            <p className="profile-hint">
              该类型不支持文本预览，请下载查看（可执行文件与高风险类型不内联，AC③）。
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}

function NodeRow({
  node,
  onToggle,
  onPreview,
  downloadUrl,
}: {
  node: ResourceNode;
  onToggle: () => void;
  onPreview: () => void;
  downloadUrl: string;
}) {
  if (node.type === "dir") {
    return (
      <button type="button" className="node-row" onClick={onToggle}>
        <span className="node-icon">📁</span> {node.name}
      </button>
    );
  }
  return (
    <span className="node-row">
      <button type="button" className="node-file" onClick={onPreview}>
        <span className="node-icon">📄</span> {node.name}
      </button>
      <span className="resource-source-display">{formatBytes(node.size_bytes)}</span>
      <a href={downloadUrl} className="node-download" title="下载（安全文件名）">
        下载
      </a>
    </span>
  );
}
