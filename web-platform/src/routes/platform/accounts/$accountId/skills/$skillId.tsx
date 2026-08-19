/**
 * /platform/accounts/{accountId}/skills/{skillId} 平台共享 Skill 只读详情
 * （10 §60、13 §89–§90，05 §12.6 平台表，P4-E4 AC⑦⑨）。
 *
 * - 只读详情：复用 SkillDetailSections（概览/使用说明/文件/工具范围/使用提示）；
 *   无编辑、删除、恢复入口（Skill 始终只读，AC⑨）；
 * - 跳转参数契约：URL 只携带产品 ID（lib/links.ts，非法 ID 404 语义）。
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { isProductId } from "@/lib/links";
import { SkillDetailSections } from "@/components/skills/SkillDetailSections";
import {
  fetchPlatformAccountSkill,
  listPlatformAccounts,
  type PlatformAccount,
} from "@/features/iam/platform";
import type { SkillDetail } from "@/features/skills";

export default function PlatformAccountSkillDetailPage() {
  const params = useParams({ strict: false }) as Record<string, string>;
  const accountId = params["accountId"] ?? "";
  const skillId = params["skillId"] ?? "";

  const [account, setAccount] = useState<PlatformAccount | null>(null);
  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(null);

  const accountLabel = account?.name ?? account?.code ?? accountId ?? "—";
  const invalidId = !isProductId(skillId);

  const load = useCallback(() => {
    setLoadError(null);
    setSkill(null);
    listPlatformAccounts()
      .then((page) => {
        setAccount(page.items.find((a) => a.id === accountId) ?? null);
      })
      .catch(() => setAccount(null));
    // 非法产品 ID 直接 404 语义，不发起后端详情请求
    if (!isProductId(skillId)) return;
    fetchPlatformAccountSkill(accountId, skillId)
      .then(setSkill)
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "无法加载 Skill" : "无法加载 Skill",
          requestId: isPlatformError(error) ? error.requestId : null,
        });
      });
  }, [accountId, skillId]);

  useEffect(() => {
    load();
  }, [load]);

  if (invalidId) {
    return (
      <div className="card" data-testid="platform-skill-invalid-id">
        <h2>未找到该 Skill</h2>
        <p className="profile-hint">
          地址中的产品 ID 无效（404 语义）。URL 只接受产品 ID，不接受 Viking URI / Account / User 标识符。
        </p>
        <Link to={`/platform/accounts/${accountId}/skills`} className="placeholder-back">
          ← 返回共享 Skill
        </Link>
      </div>
    );
  }

  return (
    <div className="admin-page" data-testid="platform-skill-detail-page">
      <div className="admin-page-header">
        <div>
          <h2>共享 Skill 详情（平台只读）</h2>
          <p className="admin-account-context" data-testid="platform-skill-detail-account">
            管理浏览目标 Account：<strong>{accountLabel}</strong>
            （选择目标 Account 是管理浏览，不改变登录者身份，AC②）
          </p>
        </div>
      </div>
      <div className="profile-actions">
        <Link to={`/platform/accounts/${accountId}/skills`} className="placeholder-back" data-testid="platform-skill-back-list">
          ← 返回共享 Skill
        </Link>
      </div>

      {loadError ? (
        <div className="admin-load-error" role="alert" data-testid="platform-skill-load-error">
          <p className="login-error">{loadError.message}</p>
          {loadError.requestId ? (
            <p className="error-page-request-id">
              请求 ID：<code>{loadError.requestId}</code>
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="button" onClick={load} data-testid="platform-skill-retry">
              重试
            </button>
          </div>
        </div>
      ) : null}

      {skill == null && !loadError ? (
        <p className="profile-hint" data-testid="platform-skill-loading">
          加载中…
        </p>
      ) : null}

      {skill != null ? (
        <div className="skill-page">
          <div className="admin-page-header">
            <h2 data-testid="platform-skill-name">{skill.name}</h2>
            <span className="status-badge" data-testid="platform-skill-ownership">
              Account 共享
            </span>
            {skill.has_auxiliary_files ? <span className="status-badge active">含辅助文件</span> : null}
          </div>
          <p className="profile-hint" data-testid="platform-skill-readonly-note">
            平台对 Skill 始终只读（10 §60）：无编辑、删除或恢复入口（范围 Out：平台代用户发布/写入 Skill）。
          </p>
          <SkillDetailSections skill={skill} ownershipLabel="Account 共享" />
        </div>
      ) : null}
    </div>
  );
}
