/**
 * Skill 列表（10 §53.2，AC①：名称/描述/标签/归属/更新时间/辅助文件标识；
 * 不展示 Viking URI、User ID 与控制文件）。
 */

import { Link } from "@tanstack/react-router";
import type { SkillRecord } from "@/features/skills";

export interface SkillListProps {
  items: SkillRecord[];
  /** 归属列文案：私有页=「我的私有」，共享页=「Account 共享」。 */
  ownershipLabel: string;
  /** 详情链接生成（产品 ID 契约，lib/links.ts）。 */
  detailHref: (skillId: string) => string;
  emptyText: string;
  /** 普通 User 共享页只读提示（06 §13.3，AC⑦）。 */
  hint?: string;
}

function formatTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export function SkillList({ items, ownershipLabel, detailHref, emptyText, hint }: SkillListProps) {
  if (items.length === 0) {
    return (
      <div>
        {hint ? <p className="profile-hint" data-testid="skills-list-hint">{hint}</p> : null}
        <div className="empty-state" data-testid="skills-list-empty">
          {emptyText}
        </div>
      </div>
    );
  }
  return (
    <div>
      {hint ? <p className="profile-hint" data-testid="skills-list-hint">{hint}</p> : null}
      <table className="data-table" data-testid="skills-list">
        <thead>
          <tr>
            <th>名称</th>
            <th>描述</th>
            <th>标签</th>
            <th>归属</th>
            <th>更新时间</th>
            <th>辅助文件</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.map((skill) => (
            <tr key={skill.id} data-testid={`skill-row-${skill.id}`}>
              <td>
                <Link to={detailHref(skill.id)} data-testid={`skill-link-${skill.id}`}>
                  {skill.name}
                </Link>
              </td>
              <td className="skill-description">{skill.description}</td>
              <td>
                {skill.tags.length > 0 ? (
                  <span className="skill-tags">
                    {skill.tags.map((tag) => (
                      <span key={tag} className="skill-tag" data-testid={`skill-tag-${tag}`}>
                        {tag}
                      </span>
                    ))}
                  </span>
                ) : (
                  "—"
                )}
              </td>
              <td>{ownershipLabel}</td>
              <td>{formatTime(skill.updated_at)}</td>
              <td>
                {skill.has_auxiliary_files ? (
                  <span className="skill-file-badge" data-testid={`skill-files-${skill.id}`}>
                    含辅助文件
                  </span>
                ) : (
                  "—"
                )}
              </td>
              <td>
                <Link to={detailHref(skill.id)} className="row-link">
                  详情
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
