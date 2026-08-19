/**
 * Subject 数据视图横幅（13 §84.2，14 号计划 §98.7，P4-E2 AC①）。
 *
 * - 固定显示「以 {Actor} 身份查看 {Subject} 的数据」，Account 固定来自登录
 *   Session，路径中的用户只作为 Subject（05 §12.6）；页面无任何身份替换入口
 *   （无 Actor/Subject/Account 切换，AC① 不允许无痕替换身份）；
 * - 只读声明：不提供修改/导出/下载/Watch/发布/删除（84.2，AC②）。
 */

export interface SubjectDataBannerProps {
  /** Actor 展示（当前登录 Admin：显示名/邮箱/id）。 */
  actorLabel: string;
  /** Subject 展示（路径用户：显示名/code/邮箱）。 */
  subjectLabel: string;
  /** 固定 Account 展示。 */
  accountLabel: string;
}

export default function SubjectDataBanner({
  actorLabel,
  subjectLabel,
  accountLabel,
}: SubjectDataBannerProps) {
  return (
    <div className="subject-data-banner" data-testid="subject-data-banner">
      <p className="subject-data-line" data-testid="subject-data-line">
        以 <strong data-testid="subject-data-actor">{actorLabel}</strong> 身份查看{" "}
        <strong data-testid="subject-data-subject">{subjectLabel}</strong>
        的数据
      </p>
      <p className="admin-account-context" data-testid="subject-data-account">
        操作固定作用于当前登录 Account：<strong>{accountLabel}</strong>（05 §12.6，路径中的用户仅作为 Subject）
      </p>
      <p className="profile-hint" data-testid="subject-data-readonly-note">
        只读视图：不提供修改、导出、下载、Watch、发布或删除能力；查看权限不自动推导写入权限（84.2）。
      </p>
    </div>
  );
}
