/**
 * Search 结果列表 + 只读抽屉（11 §69.4/§69.5，P3-E3 AC④）。
 *
 * - 列表按引擎返回顺序展示类型、产品显示名称、我的/Account 共享归属和摘要；
 * - Resource/Skill 点击进入产品详情页（跳转参数契约由 P3-E1 冻结：
 *   lib/links.ts `searchTargetPath`，URL 仅携带产品 ID）；
 * - Memory 点击只打开只读抽屉：Memory 类型、摘要、匹配原因；
 *   不调用 content/read，无编辑/删除/恢复/下载（AC④）；
 * - 不展示 URI/Score/层级/Query Plan/Provenance/Relations/标签/更新时间（AC④）；
 * - 当前筛选条件显示在结果区上方（11 §69.4）。
 */

import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { searchTargetPath } from "@/lib/links";
import type { SearchHit } from "@/features/search/search";

const CONTEXT_LABELS: Record<SearchHit["context_type"], string> = {
  memory: "Memory",
  resource: "Resource",
  skill: "Skill",
};

export interface SearchResultListProps {
  hits: SearchHit[];
  /** 当前筛选条件展示（11 §69.4：结果区上方）。 */
  filtersText: string | null;
  loading?: boolean;
}

export default function SearchResultList({ hits, filtersText, loading }: SearchResultListProps) {
  const navigate = useNavigate();
  const [memoryHit, setMemoryHit] = useState<SearchHit | null>(null);

  function openHit(hit: SearchHit) {
    if (hit.context_type === "memory") {
      setMemoryHit(hit);
      return;
    }
    // AC④：Resource/Skill 跳产品详情（跳转目标由 P3-E4/E5 交付；参数契约 P3-E1 冻结）
    if (!hit.ref_id) return;
    const path = searchTargetPath({
      kind: hit.context_type === "resource" ? "resource" : "skill",
      // 服务端可见性 → 跳转契约分区（user_private → private / account_shared → shared）
      visibility: hit.visibility === "user_private" ? "private" : "shared",
      productId: hit.ref_id,
    });
    void navigate({ to: path });
  }

  return (
    <section className="card search-results" data-testid="search-results">
      <div className="search-results-head">
        <h3>结果（{hits.length}）</h3>
        {filtersText ? (
          <p className="profile-hint" data-testid="search-filters-text">
            当前筛选：{filtersText}
          </p>
        ) : null}
      </div>

      {loading ? <p>检索中…</p> : null}

      {!loading && hits.length === 0 ? (
        <div className="empty-state" data-testid="search-empty">
          没有匹配的结果。可以尝试修改检索词或缩小筛选范围。
        </div>
      ) : null}

      {!loading && hits.length > 0 ? (
        <ul className="search-hit-list" data-testid="search-hit-list">
          {hits.map((hit, index) => (
            <li key={`${hit.context_type}-${hit.ref_id ?? hit.display_name}-${index}`}>
              <button
                type="button"
                className="search-hit"
                onClick={() => openHit(hit)}
                data-testid={`search-hit-${hit.context_type}`}
              >
                <span className="status-badge">{CONTEXT_LABELS[hit.context_type]}</span>
                <span className="status-badge">{hit.visibility === "user_private" ? "我的" : "Account 共享"}</span>
                <strong>{hit.display_name}</strong>
                <span className="profile-hint">{hit.abstract ?? "—"}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {memoryHit ? (
        <div className="memory-drawer" role="dialog" aria-label="Memory 只读详情" data-testid="memory-drawer">
          <div className="memory-drawer-head">
            <h4>Memory 详情（只读）</h4>
            <button type="button" onClick={() => setMemoryHit(null)} aria-label="关闭抽屉">
              关闭
            </button>
          </div>
          <dl className="memory-drawer-body">
            <div className="profile-row">
              <dt>Memory 类型</dt>
              <dd data-testid="memory-drawer-type">{memoryHit.memory_type || "—"}</dd>
            </div>
            <div className="profile-row">
              <dt>摘要</dt>
              <dd data-testid="memory-drawer-abstract">{memoryHit.abstract ?? "—"}</dd>
            </div>
            <div className="profile-row">
              <dt>匹配原因</dt>
              <dd data-testid="memory-drawer-reason">{memoryHit.match_reason ?? "—"}</dd>
            </div>
          </dl>
          <p className="profile-hint">
            Memory 由 Session 归档整理自动生成与更新；本视图只读，不提供编辑、删除、恢复或下载。
          </p>
        </div>
      ) : null}
    </section>
  );
}
