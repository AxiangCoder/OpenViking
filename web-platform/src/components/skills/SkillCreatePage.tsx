/**
 * Skill 创建页（在线创建 / SKILL.md / ZIP 上传）（10 §54，P3-E5 AC②④⑦）。
 *
 * - 在线创建：表单序列化为合法 SKILL.md，不要求手写 YAML，不提交 target_uri（AC②）；
 * - 上传：单个 SKILL.md 或 ZIP，经 `resource-uploads` 一次性 `upload_id` 消费（10 §61.5）；
 * - 目标归属由入口决定（scope），页面无归属下拉/visibility/URI 字段（AC⑦）。
 */

import { useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  createSkillFromUpload,
  createSkillOnline,
  skillErrorMessage,
  type SkillOnlineInput,
  type SkillScope,
} from "@/features/skills";
import { SkillForm } from "./SkillForm";
import { SkillUploadSection } from "./SkillUploadSection";

export interface SkillCreatePageProps {
  scope: SkillScope;
  /** 创建成功后的详情跳转前缀（产品 ID 契约，lib/links.ts）。 */
  detailPrefix: string;
  backHref: string;
}

export function SkillCreatePage({ scope, detailPrefix, backHref }: SkillCreatePageProps) {
  const navigate = useNavigate();
  const [tab, setTab] = useState<"online" | "upload">("online");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  function goDetail(skillId: string) {
    void navigate({ to: `${detailPrefix}/${skillId}`, replace: true });
  }

  function handleOnlineSubmit(input: SkillOnlineInput): Promise<void> {
    setBusy(true);
    setActionError(null);
    return createSkillOnline(scope, input)
      .then((skill) => goDetail(skill.id))
      .catch((error) => setActionError(skillErrorMessage(error)))
      .finally(() => setBusy(false));
  }

  function handleUploadSubmit(uploadId: string, name?: string): Promise<void> {
    setBusy(true);
    setActionError(null);
    return createSkillFromUpload(scope, name ?? "", uploadId)
      .then((skill) => goDetail(skill.id))
      .catch((error) => setActionError(skillErrorMessage(error)))
      .finally(() => setBusy(false));
  }

  return (
    <div className="skill-page" data-testid="skill-create-page">
      <div className="admin-page-header">
        <h2 data-testid="skill-create-title">
          {scope === "me" ? "新建私有 Skill" : "新建共享 Skill"}
        </h2>
      </div>
      <div className="skill-tabs" role="tablist" aria-label="创建方式">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "online"}
          onClick={() => {
            setTab("online");
            setActionError(null);
          }}
          data-testid="skill-create-tab-online"
        >
          在线创建
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "upload"}
          onClick={() => {
            setTab("upload");
            setActionError(null);
          }}
          data-testid="skill-create-tab-upload"
        >
          上传文件（SKILL.md / ZIP）
        </button>
      </div>
      {tab === "online" ? (
        <div className="card">
          <SkillForm
            mode="create"
            submitLabel="创建 Skill"
            busy={busy}
            error={actionError}
            onSubmit={handleOnlineSubmit}
          />
        </div>
      ) : (
        <div className="card">
          <SkillUploadSection
            scope={scope}
            mode="create"
            submitLabel="上传并创建"
            busy={busy}
            error={actionError}
            onSubmit={handleUploadSubmit}
          />
        </div>
      )}
      <p className="profile-hint" data-testid="skill-create-scope-hint">
        {scope === "me"
          ? "普通 User 的创建入口固定进入自己的私有区（10 §54.1）。"
          : "Account Admin 从共享管理入口创建，固定进入当前 Account 共享区（10 §54.1，AC⑦）。"}
      </p>
      <p className="placeholder-back">
        <Link to={backHref} data-testid="skill-create-back">
          返回列表
        </Link>
      </p>
    </div>
  );
}
