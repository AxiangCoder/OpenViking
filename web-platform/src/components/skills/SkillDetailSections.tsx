/**
 * Skill 详情区块（10 §53.3，P3-E5 AC⑤）。
 *
 * - 概览：名称、描述、标签、归属；
 * - 使用说明：渲染后的 SKILL.md 正文（纯文本渲染，不执行任何 HTML）；
 * - 文件：辅助文件清单（ZIP Skill 只读浏览，更新走整体重传）；
 * - 工具范围：`allowed-tools` 声明（非 Platform Permission，不替代运行时授权）；
 * - 使用提示：可由已连接的 Agent/插件/MCP 客户端按权限检索和读取；
 * - 页面不提供「在新 Session 中使用」或独立执行器（10 §57，AC⑤）。
 */

import type { SkillDetail, SkillSourceType } from "@/features/skills";

export interface SkillDetailSectionsProps {
  skill: SkillDetail;
  ownershipLabel: string;
}

function formatBytes(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`;
  return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`;
}

const SOURCE_LABELS: Record<SkillSourceType, string> = {
  online: "在线创建",
  skill_md: "SKILL.md 上传",
  zip: "ZIP 上传",
};

export function SkillDetailSections({ skill, ownershipLabel }: SkillDetailSectionsProps) {
  return (
    <div className="skill-detail" data-testid="skill-detail">
      <section className="card" aria-label="概览" data-testid="skill-overview">
        <h2>概览</h2>
        <dl className="profile-row">
          <dt>名称</dt>
          <dd data-testid="skill-detail-name">{skill.name}</dd>
          <dt>描述</dt>
          <dd>{skill.description}</dd>
          <dt>标签</dt>
          <dd>
            {skill.tags.length > 0 ? skill.tags.join("、") : "—"}
          </dd>
          <dt>归属</dt>
          <dd>{ownershipLabel}</dd>
          <dt>来源</dt>
          <dd>{SOURCE_LABELS[skill.source_type] ?? skill.source_type}</dd>
          <dt>更新时间</dt>
          <dd>{skill.updated_at ? new Date(skill.updated_at).toLocaleString() : "—"}</dd>
        </dl>
      </section>

      <section className="card" aria-label="使用说明" data-testid="skill-usage">
        <h2>使用说明</h2>
        <pre className="skill-markdown" data-testid="skill-content">{skill.content}</pre>
      </section>

      <section className="card" aria-label="文件清单" data-testid="skill-files">
        <h2>文件清单</h2>
        {skill.files.length === 0 ? (
          <p className="profile-hint">
            {skill.source_type === "online"
              ? "在线 Skill 无辅助文件。"
              : "无辅助文件。"}
          </p>
        ) : (
          <div>
            <table className="data-table">
              <thead>
                <tr>
                  <th>文件名</th>
                  <th>大小</th>
                </tr>
              </thead>
              <tbody>
                {skill.files.map((file) => (
                  <tr key={file.name} data-testid={`skill-file-${file.name}`}>
                    <td>{file.name}</td>
                    <td>{formatBytes(file.size_bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="profile-hint">
              ZIP Skill 支持浏览文件清单；更新采用完整 ZIP 整体重传，不提供逐文件在线编辑（10 §56.2）。
            </p>
          </div>
        )}
      </section>

      <section className="card" aria-label="工具范围" data-testid="skill-tools">
        <h2>工具范围（allowed-tools）</h2>
        {skill.allowed_tools.length === 0 ? (
          <p className="profile-hint">未声明。</p>
        ) : (
          <p data-testid="skill-tools-list">{skill.allowed_tools.join("、")}</p>
        )}
        <p className="profile-hint">
          allowed-tools 是 Skill 声明希望使用的工具范围，不是 Platform Permission，
          也不能替代 Agent/客户端运行时的工具授权（10 §53.3）。
        </p>
      </section>

      <section className="card" aria-label="使用提示" data-testid="skill-usage-hint">
        <h2>使用提示</h2>
        <p className="profile-hint">
          该 Skill 由已连接的 Codex、其他 Agent、插件或 MCP 客户端按权限检索和读取；
          页面不提供「在新 Session 中使用」或独立执行器（10 §57）。
        </p>
      </section>
    </div>
  );
}
