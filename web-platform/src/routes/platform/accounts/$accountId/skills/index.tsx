/**
 * /platform/accounts/{accountId}/skills 平台共享 Skill 只读列表
 * （10 §60、13 §89–§90，05 §12.6 平台表，P4-E4 AC⑦⑨）。
 *
 * - Skill 始终只读：无创建/上传/编辑/删除/恢复入口（范围 Out：平台代用户
 *   发布/写入 Skill）；本列表只读展示 Account 共享 Skill；
 * - 目标 Account 是管理浏览的 Subject：页头明确 Account 上下文，不改变
 *   登录者身份、无 Account 切换入口（AC②）；
 * - 详情跳转使用产品 ID 契约（lib/links.ts）。
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { SkillList } from "@/components/skills/SkillList";
import {
  listPlatformAccountSkills,
  listPlatformAccounts,
  type PlatformAccount,
} from "@/features/iam/platform";
import type { SkillRecord } from "@/features/skills";

export default function PlatformAccountSkillsPage() {
  const params = useParams({ strict: false }) as Record<string, string>;
  const accountId = params["accountId"] ?? "";

  const [account, setAccount] = useState<PlatformAccount | null>(null);
  const [items, setItems] = useState<SkillRecord[] | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(null);

  const accountLabel = account?.name ?? account?.code ?? accountId ?? "—";

  const load = useCallback(() => {
    setLoadError(null);
    setItems(null);
    listPlatformAccounts()
      .then((page) => {
        setAccount(page.items.find((a) => a.id === accountId) ?? null);
      })
      .catch(() => setAccount(null));
    listPlatformAccountSkills(accountId)
      .then((page) => setItems(page.items))
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "无法加载 Skill 列表" : "无法加载 Skill 列表",
          requestId: isPlatformError(error) ? error.requestId : null,
        });
      });
  }, [accountId]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="admin-page" data-testid="platform-account-skills-page">
      <div className="admin-page-header">
        <div>
          <h2>共享 Skill（平台只读）</h2>
          <p className="admin-account-context" data-testid="platform-skills-account-context">
            管理浏览目标 Account：<strong>{accountLabel}</strong>
            （选择目标 Account 是管理浏览，不改变登录者身份，无 Account 切换入口，AC②）
          </p>
        </div>
      </div>
      <div className="profile-actions">
        <Link to="/platform/accounts" className="placeholder-back" data-testid="platform-skills-back-accounts">
          ← 返回 Accounts
        </Link>
      </div>

      {loadError ? (
        <div className="admin-load-error" role="alert" data-testid="platform-skills-load-error">
          <p className="login-error">{loadError.message}</p>
          {loadError.requestId ? (
            <p className="error-page-request-id">
              请求 ID：<code>{loadError.requestId}</code>
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="button" onClick={load} data-testid="platform-skills-retry">
              重试
            </button>
          </div>
        </div>
      ) : null}

      {items == null && !loadError ? (
        <p className="profile-hint" data-testid="platform-skills-loading">
          加载中…
        </p>
      ) : null}

      {items != null ? (
        <section className="card" data-testid="platform-skills-list">
          <SkillList
            items={items}
            ownershipLabel="Account 共享"
            detailHref={(skillId) => `/platform/accounts/${accountId}/skills/${skillId}`}
            emptyText="该 Account 暂无共享 Skill。"
            hint="平台对 Skill 始终只读（10 §60）：无创建、上传、编辑、删除或恢复入口（范围 Out：平台代用户发布/写入 Skill）。"
          />
        </section>
      ) : null}
    </div>
  );
}
