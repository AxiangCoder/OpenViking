/**
 * 一次性凭证展示组件（复用 P3-E2 API Key 一次性明文模式，13 §85.2/85.4，P4-E1）。
 *
 * - 凭证（创建初始密码 / 重置新密码）只在该次响应和当前组件内存态中存在
 *   （06 §13.9：密码只展示一次，关闭后不能重新查看）；
 * - 组件自身不把凭证写入 localStorage/sessionStorage/URL/剪贴板历史（06 §13.5）；
 * - 复制按钮使用 navigator.clipboard；失败时提示手动选择复制；
 * - 交接收限制提示（06 §13.9，AC⑧）：创建/重置者可能长期知晓密码，系统无法
 *   从密码登录事件中绝对区分目标用户本人与持有该密码的创建者。
 */

import { useState, type ReactNode } from "react";

export interface OneTimeSecretProps {
  title: string;
  secret: string;
  /** 用户/角色/所属 Account 等上下文行（展示在密码上方）。 */
  subjectLine?: string;
  onClose: () => void;
  testIdPrefix?: string;
  children?: ReactNode;
}

export default function OneTimeSecret({
  title,
  secret,
  subjectLine,
  onClose,
  testIdPrefix = "one-time-secret",
  children,
}: OneTimeSecretProps) {
  const [notice, setNotice] = useState<string | null>(null);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(secret);
      setNotice("已复制到剪贴板（仅本次会话可见）。");
    } catch {
      setNotice("复制失败，请手动选择复制。");
    }
  }

  return (
    <section className="card one-time-secret" data-testid={testIdPrefix}>
      <h2>{title}</h2>
      {subjectLine ? (
        <p className="profile-hint" data-testid={`${testIdPrefix}-subject`}>
          {subjectLine}
        </p>
      ) : null}
      <p className="profile-hint" data-testid={`${testIdPrefix}-once-notice`}>
        密码只显示这一次，关闭后无法再次查看（请立即复制并妥善保存）。
      </p>
      <div className="one-time-key" data-testid={`${testIdPrefix}-secret`}>
        {secret}
      </div>
      <div className="profile-actions">
        <button
          type="button"
          className="primary-button"
          onClick={() => void handleCopy()}
          data-testid={`${testIdPrefix}-copy`}
        >
          复制
        </button>
        <button
          type="button"
          onClick={onClose}
          data-testid={`${testIdPrefix}-close`}
        >
          我已保存，关闭
        </button>
      </div>
      <p className="profile-hint admin-handover-notice" data-testid={`${testIdPrefix}-handover`}>
        交接限制：创建者可能长期知晓该密码，系统无法从密码登录事件中绝对区分目标用户本人与
        持有该密码的创建者；请通过系统之外的方式（线下或加密渠道）将密码交给目标用户。
      </p>
      {children}
      {notice ? (
        <p className="profile-hint" role="status">
          {notice}
        </p>
      ) : null}
    </section>
  );
}
