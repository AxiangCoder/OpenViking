/**
 * 平台级 Subject 数据视图横幅（13 §79.3/§89–§90，14 号计划 §98.9，P4-E4 AC②⑦）。
 *
 * - 固定展示「以 {Actor} 身份查看 {Subject} 的数据」，文案同 79.3；
 * - 选择目标 Account 是管理浏览（指定 Subject），不改变登录者 Actor 身份
 *   （06 §13.2，AC②）；页面明确 Account 上下文、无普通用户意义的 Account
 *   切换入口；
 * - 平台级查询记录 Actor、Subject Account/User 与 Scope（04 §10.8，AC⑧）；
 * - 只读声明：平台对成员数据与 Skill 始终只读（10 §60，AC⑦⑨）。
 */

export interface PlatformSubjectDataBannerProps {
  /** Actor 展示（当前登录 PSA：显示名/邮箱/id）。 */
  actorLabel: string;
  /** 目标 Account 展示（管理浏览的 Subject Account）。 */
  accountLabel: string;
  /** Subject 用户展示（行/页头级；账户级页面可不传）。 */
  subjectLabel?: string;
}

export default function PlatformSubjectDataBanner({
  actorLabel,
  accountLabel,
  subjectLabel,
}: PlatformSubjectDataBannerProps) {
  return (
    <div className="subject-data-banner" data-testid="platform-subject-data-banner">
      <p className="subject-data-line" data-testid="platform-subject-data-line">
        以 <strong data-testid="platform-subject-data-actor">{actorLabel}</strong> 身份查看{" "}
        {subjectLabel ? (
          <strong data-testid="platform-subject-data-subject">{subjectLabel}</strong>
        ) : (
          <strong data-testid="platform-subject-data-subject">目标 Account</strong>
        )}
        的数据
      </p>
      <p className="admin-account-context" data-testid="platform-subject-data-account">
        操作固定作用于目标 Account：<strong>{accountLabel}</strong>
        （选择目标 Account 是管理浏览，不改变登录者身份；无 Account 切换入口，06 §13.2）
      </p>
      <p className="profile-hint" data-testid="platform-subject-data-readonly-note">
        平台级查询记录 Actor、Subject Account/User 与 Scope；成员数据与 Skill 始终只读，
        不提供修改、导出、下载、Watch、发布或删除能力（13 §90、10 §60）。
      </p>
    </div>
  );
}
