/**
 * /platform/accounts/{accountId}/users/{userId}/skills/{skillId}
 * 平台级成员私有 Skill 只读详情（13 §89.3 同 84.2、10 §60，P4-E4 AC②⑦⑨）。
 *
 * - 只读详情：复用 SkillDetailSections（概览/使用说明/文件/工具范围/使用提示）；
 *   无编辑、删除、恢复入口（10 §61.3，AC⑦）；
 * - 平台代用户发布/写入 Skill 不在 v0.1（范围 Out）：无发布入口、无重试发布
 *   （与 /admin 成员 Skill 详情不同，AC⑦⑨ Skill 始终只读）；
 * - 跳转参数契约：URL 只携带产品 ID（lib/links.ts，非法 ID 404 语义）；
 * - 加载失败保留页面框架，展示 Request ID 与重试（13 §84.3）。
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { useMe } from "@/features/auth/useAuth";
import { isProductId } from "@/lib/links";
import PlatformSubjectDataBanner from "@/components/subject/PlatformSubjectDataBanner";
import { SkillDetailSections } from "@/components/skills/SkillDetailSections";
import { formatIsoDateTime, type AdminUserRecord } from "@/features/iam/admin-users";
import {
  fetchPlatformMemberSkill,
  listPlatformAccountUsers,
  listPlatformAccounts,
} from "@/features/iam/platform";
import type { SkillDetail } from "@/features/skills";

export default function PlatformAccountUserSkillPage() {
  const params = useParams({ strict: false }) as Record<string, string>;
  const accountId = params["accountId"] ?? "";
  const userId = params["userId"] ?? "";
  const skillId = params["skillId"] ?? "";
  const me = useMe();
  const actorLabel = me?.user.display_name ?? me?.user.email ?? me?.user.id ?? "当前管理员";

  const [accountLabel, setAccountLabel] = useState("—");
  const [subject, setSubject] = useState<AdminUserRecord | null | undefined>(undefined);
  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(null);

  const invalidId = !isProductId(skillId);

  const load = useCallback(() => {
    setLoadError(null);
    setSkill(null);
    listPlatformAccounts()
      .then((page) => {
        const account = page.items.find((a) => a.id === accountId);
        setAccountLabel(account?.name ?? account?.code ?? accountId ?? "—");
      })
      .catch(() => setAccountLabel(accountId ?? "—"));
    listPlatformAccountUsers(accountId)
      .then((page) => {
        setSubject(page.items.find((u) => u.id === userId) ?? null);
      })
      .catch(() => setSubject(null));
    // 非法产品 ID 直接 404 语义，不发起后端详情请求（AC⑦）
    if (!isProductId(skillId)) return;
    fetchPlatformMemberSkill(accountId, userId, skillId)
      .then(setSkill)
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "无法加载 Skill" : "无法加载 Skill",
          requestId: isPlatformError(error) ? error.requestId : null,
        });
      });
  }, [accountId, userId, skillId]);

  useEffect(() => {
    load();
  }, [load]);

  const subjectLabel = subject
    ? `${subject.display_name ?? subject.username}（${subject.email}）`
    : `用户 ${userId}`;

  if (invalidId) {
    return (
      <div className="card" data-testid="platform-member-skill-invalid-id">
        <h2>未找到该 Skill</h2>
        <p className="profile-hint">
          地址中的产品 ID 无效（404 语义）。URL 只接受产品 ID，不接受 Viking URI / Account / User 标识符（AC⑦）。
        </p>
        <Link to={`/platform/accounts/${accountId}/users/${userId}/data`} className="placeholder-back">
          ← 返回成员数据
        </Link>
      </div>
    );
  }

  return (
    <div className="admin-page" data-testid="platform-member-skill-page">
      <PlatformSubjectDataBanner actorLabel={actorLabel} accountLabel={accountLabel} subjectLabel={subjectLabel} />
      <div className="profile-actions">
        <Link to={`/platform/accounts/${accountId}/users/${userId}/data`} className="placeholder-back" data-testid="platform-member-skill-back">
          ← 返回成员数据
        </Link>
      </div>

      {loadError ? (
        <div className="admin-load-error" role="alert" data-testid="platform-member-skill-load-error">
          <p className="login-error">{loadError.message}</p>
          {loadError.requestId ? (
            <p className="error-page-request-id">
              请求 ID：<code>{loadError.requestId}</code>
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="button" onClick={load} data-testid="platform-member-skill-retry">
              重试
            </button>
          </div>
        </div>
      ) : null}

      {skill == null && !loadError ? <p className="profile-hint">加载中…</p> : null}

      {skill != null ? (
        <div className="skill-page">
          <div className="admin-page-header">
            <h2 data-testid="platform-member-skill-name">{skill.name}</h2>
            <span className="status-badge" data-testid="platform-member-skill-ownership">
              成员私有
            </span>
            {skill.has_auxiliary_files ? <span className="status-badge active">含辅助文件</span> : null}
          </div>
          <p className="profile-hint" data-testid="platform-member-skill-meta">
            更新时间：{formatIsoDateTime(skill.updated_at)} ｜ 平台对成员私有 Skill 始终只读
            （10 §60）：无发布、编辑、删除或恢复入口（范围 Out：平台代用户发布/写入 Skill）。
          </p>
          <SkillDetailSections skill={skill} ownershipLabel="成员私有" />
        </div>
      ) : null}
    </div>
  );
}
