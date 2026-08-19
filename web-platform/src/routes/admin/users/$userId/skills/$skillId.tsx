/**
 * /admin/users/{userId}/skills/{skillId} 成员私有 Skill 只读详情 + 发布为共享
 * （13 §86.2、10 §58，14 号计划 §98.7，P4-E2 AC④⑤⑥）。
 *
 * - 只读详情：复用 SkillDetailSections（概览/使用说明/文件/工具范围/使用提示）；
 *   无编辑、删除、恢复入口（10 §61.3，AC⑥ 设计禁止）；
 * - 发布为共享（仅 `skill.user_private.publish.account` 且 Skill active）：
 *   - 确认弹窗完整展示影响与不可取消提示（10 §58.3，AC④：Skill 名称/当前
 *     所属 User/目标 Account/发布后所有成员可读可用/私有区不保留/不可取消）；
 *   - 提交失败展示失败状态与「重试」入口（10 §58.4 Operation failed 可重试，
 *     AC⑤）；成功展示 Operation 状态（归属转换已生效，迁移后台执行）；
 * - 加载失败保留页面框架，展示 Request ID 与重试（13 §84.3）。
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { isPlatformError } from "@/lib/platform-client";
import { useMe } from "@/features/auth/useAuth";
import { canPerform } from "@/lib/permissions";
import { isProductId } from "@/lib/links";
import SubjectDataBanner from "@/components/subject/SubjectDataBanner";
import { SkillDetailSections } from "@/components/skills/SkillDetailSections";
import { listAdminUsers, type AdminUserRecord } from "@/features/iam/admin-users";import {
  fetchMemberSkill,
  isPublishRetryable,
  memberDataErrorMessage,
  publishMemberSkill,
  type MemberSkillPublishResult,
} from "@/features/iam/member-data";
import type { SkillDetail } from "@/features/skills";

type PublishPhase = "idle" | "confirming" | "submitting" | "success" | "failed";

export default function AdminUserMemberSkillPage() {
  const params = useParams({ strict: false }) as Record<string, string>;
  const userId = params["userId"] ?? "";
  const skillId = params["skillId"] ?? "";
  const me = useMe();
  const accountLabel = me?.account?.name ?? me?.account?.code ?? me?.account?.id ?? "—";
  const actorLabel = me?.user.display_name ?? me?.user.email ?? me?.user.id ?? "当前管理员";

  const [subject, setSubject] = useState<AdminUserRecord | null | undefined>(undefined);
  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(null);
  const [phase, setPhase] = useState<PublishPhase>("idle");
  const [publishError, setPublishError] = useState<string | null>(null);
  const [publishResult, setPublishResult] = useState<MemberSkillPublishResult | null>(null);

  const invalidId = !isProductId(skillId);

  const canPublish = canPerform(me, "skill.user_private.publish.account");
  const submitting = phase === "submitting";

  const load = useCallback(() => {
    setLoadError(null);
    setSkill(null);
    listAdminUsers()
      .then((page) => {
        setSubject(page.items.find((u) => u.id === userId) ?? null);
      })
      .catch(() => setSubject(null));
    // 非法产品 ID 直接 404 语义，不发起后端详情请求（AC⑥）
    if (!isProductId(skillId)) return;
    fetchMemberSkill(userId, skillId)
      .then(setSkill)
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "无法加载 Skill" : "无法加载 Skill",
          requestId: isPlatformError(error) ? error.requestId : null,
        });
      });
  }, [userId, skillId]);

  useEffect(() => {
    load();
  }, [load]);

  function handlePublishSubmit() {
    if (phase === "submitting") return;
    setPhase("submitting");
    setPublishError(null);
    publishMemberSkill(userId, skillId)
      .then((result) => {
        setPublishResult(result);
        setPhase("success");
      })
      .catch((error) => {
        // AC⑤：失败状态展示 + 重试入口（Operation failed 可重试，10 §58.4）
        setPublishError(
          memberDataErrorMessage(error, "发布失败，请稍后重试") +
            (isPublishRetryable(error) ? "（该失败可重试）" : ""),
        );
        setPhase("failed");
      });
  }

  const subjectLabel = subject
    ? `${subject.display_name ?? subject.username}（${subject.email}）`
    : `用户 ${userId}`;

  if (invalidId) {
    return (
      <div className="card" data-testid="member-skill-invalid-id">
        <h2>未找到该 Skill</h2>
        <p className="profile-hint">
          地址中的产品 ID 无效（404 语义）。URL 只接受产品 ID，不接受 Viking URI / Account / User 标识符（AC⑥）。
        </p>
        <Link to={`/admin/users/${userId}/data`} className="placeholder-back">
          ← 返回成员数据
        </Link>
      </div>
    );
  }

  return (
    <div className="admin-page" data-testid="member-skill-page">
      <SubjectDataBanner actorLabel={actorLabel} subjectLabel={subjectLabel} accountLabel={accountLabel} />
      <div className="profile-actions">
        <Link to={`/admin/users/${userId}/data`} className="placeholder-back" data-testid="member-skill-back">
          ← 返回成员数据
        </Link>
      </div>

      {loadError ? (
        <div className="admin-load-error" role="alert" data-testid="member-skill-load-error">
          <p className="login-error">{loadError.message}</p>
          {loadError.requestId ? (
            <p className="error-page-request-id">
              请求 ID：<code>{loadError.requestId}</code>
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="button" onClick={load} data-testid="member-skill-retry">
              重试
            </button>
          </div>
        </div>
      ) : null}

      {skill == null && !loadError ? <p className="profile-hint">加载中…</p> : null}

      {skill != null ? (
        <div className="skill-page">
          <div className="admin-page-header">
            <h2 data-testid="member-skill-name">{skill.name}</h2>
            <span className="status-badge" data-testid="member-skill-ownership">
              成员私有
            </span>
            {skill.has_auxiliary_files ? <span className="status-badge active">含辅助文件</span> : null}
          </div>
          <SkillDetailSections skill={skill} ownershipLabel="成员私有" />

          {phase === "success" && publishResult ? (
            <section className="card" data-testid="member-skill-publish-success">
              <h2>已提交发布为共享 Skill</h2>
              <ul className="admin-dialog-list">
                <li>
                  Skill：<strong>{skill.name}</strong>（{publishResult.skill_id}）
                </li>
                <li>目标 Account：{accountLabel}</li>
                <li>归属转换已生效（user_private → account_shared，ID 与名称不变）。</li>
                <li>
                  发布 Operation：<code>{publishResult.operation_id}</code>（状态 {publishResult.status}，
                  文件迁移在后台执行，成功后所有成员可读取和使用）。
                </li>
                <li>原 User 私有区不再保留副本；发布不可取消、不可反向转换（10 §58.3）。</li>
              </ul>
              <p className="profile-hint">
                该 Skill 已归属 Account 共享；成员私有列表不再显示，可在共享 Skill 中查看。
              </p>
              <div className="profile-actions">
                <Link to={`/admin/users/${userId}/data`} className="placeholder-back" data-testid="member-skill-publish-done">
                  返回成员数据
                </Link>
              </div>
            </section>
          ) : null}

          {phase !== "success" && canPublish && skill.status === "active" ? (
            <section className="card" aria-label="管理操作" data-testid="member-skill-publish-section">
              <h2>发布为共享</h2>
              <p className="profile-hint">
                仅 Account Admin 可将本 Account 成员的私有 Skill 原地发布为共享（10 §58.1）；
                发布不是复制，原私有区不保留副本且不可取消。
              </p>

              {phase === "failed" ? (
                <div className="admin-load-error" role="alert" data-testid="member-skill-publish-error">
                  <p className="login-error">{publishError}</p>
                  <div className="profile-actions">
                    {/* AC⑤：失败展示 + 重试入口（Operation failed 可重试） */}
                    <button
                      type="button"
                      className="primary-button"
                      onClick={() => {
                        setPhase("idle");
                        setPublishError(null);
                      }}
                      data-testid="member-skill-publish-dismiss"
                    >
                      关闭
                    </button>
                    <button
                      type="button"
                      className="primary-button"
                      onClick={handlePublishSubmit}
                      disabled={submitting}
                      data-testid="member-skill-publish-retry"
                    >
                      重试发布
                    </button>
                  </div>
                </div>
              ) : (
                <div className="profile-actions">
                  <button
                    type="button"
                    className="primary-button"
                    onClick={() => {
                      setPublishError(null);
                      setPhase("confirming");
                    }}
                    disabled={submitting}
                    data-testid="member-skill-publish-open"
                  >
                    发布为共享
                  </button>
                </div>
              )}

              {phase === "confirming" ? (
                <div className="confirm-dialog" role="dialog" aria-label="发布为共享确认" data-testid="member-skill-publish-dialog">
                  <h3>发布为共享 Skill</h3>
                  <p>确认将以下成员私有 Skill 发布为 Account 共享 Skill？</p>
                  <ul className="admin-dialog-list" data-testid="member-skill-publish-impact">
                    <li>Skill 名称：<strong>{skill.name}</strong></li>
                    <li>当前所属 User：{subjectLabel}</li>
                    <li>目标 Account：{accountLabel}</li>
                    <li>发布后所有 Account 成员可读取和使用（10 §58.3）。</li>
                    <li>原 User 私有区不再保留（归属转换，不保留副本）。</li>
                    <li><strong>操作不可取消发布</strong>，不可反向转换。</li>
                  </ul>
                  <p className="profile-hint">
                    不要求所属 User 审批；无需再次输入密码或 Account 名称。
                  </p>
                  <div className="profile-actions">
                    <button
                      type="button"
                      onClick={() => setPhase("idle")}
                      disabled={submitting}
                      data-testid="member-skill-publish-cancel"
                    >
                      取消
                    </button>
                    <button
                      type="button"
                      className="danger-button"
                      onClick={handlePublishSubmit}
                      disabled={submitting}
                      data-testid="member-skill-publish-confirm"
                    >
                      {submitting ? "发布中…" : "确认发布"}
                    </button>
                  </div>
                </div>
              ) : null}
            </section>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
