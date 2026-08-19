/**
 * /admin 治理页数据层单测（13 §87，14 号计划 §98.8，P4-E3 AC①②）。
 */

import { describe, expect, it } from "vitest";
import {
  auditActorLabel,
  auditSubjectLabel,
  filterAuditEvents,
  permissionDomain,
  type AuditEvent,
} from "./admin-governance";

function event(overrides: Partial<AuditEvent>): AuditEvent {
  return {
    id: "ev-1",
    occurred_at: "2026-08-18T09:00:00Z",
    request_id: "req-1",
    account_id: "acc-1",
    actor_type: "user",
    actor_user_id: "aaaaaaaa-0000-0000-0000-000000000001",
    actor_account_id: "acc-1",
    actor_system_component: null,
    actor_session_id: "sess-1",
    authentication_method: "session",
    actor_credential_id: "cred-1",
    subject_account_id: null,
    subject_user_id: "bbbbbbbb-0000-0000-0000-000000000002",
    action: "user.password.reset",
    target_type: "user",
    target_id: "bbbbbbbb-0000-0000-0000-000000000002",
    target_visibility: "user_private",
    scope: "account",
    result: "success",
    reason: null,
    metadata: { secret: "x" },
    ...overrides,
  };
}

describe("permissionDomain（权限矩阵分区）", () => {
  it("取第一个点前 domain；无点返回原样", () => {
    expect(permissionDomain("skill.account_shared.manage.account")).toBe("skill");
    expect(permissionDomain("audit.read")).toBe("audit");
    expect(permissionDomain("plain")).toBe("plain");
  });
});

describe("auditActorLabel / auditSubjectLabel（13 §87.2）", () => {
  it("User Actor 用短 ID；系统组件用组件名", () => {
    expect(auditActorLabel(event({}))).toBe("User（aaaaaaaa）");
    expect(
      auditActorLabel(event({ actor_type: "system", actor_system_component: "provisioning-worker" })),
    ).toBe("provisioning-worker");
  });

  it("Subject 同时保留 User 与 Account；跨用户事件两者齐备", () => {
    expect(auditSubjectLabel(event({}))).toBe("User（bbbbbbbb）");
    expect(
      auditSubjectLabel(event({ subject_account_id: "acc-1", subject_user_id: null })),
    ).toBe("Account（acc-1）");
    expect(
      auditSubjectLabel(event({ subject_account_id: "acc-1" })),
    ).toBe("User（bbbbbbbb） / Account（acc-1）");
    expect(
      auditSubjectLabel(event({ subject_account_id: null, subject_user_id: null })),
    ).toBe("—");
  });

  it("不展示会话/凭据内部 ID", () => {
    expect(auditActorLabel(event({}))).not.toContain("sess-1");
    expect(auditActorLabel(event({}))).not.toContain("cred-1");
  });
});

describe("filterAuditEvents（AC② 前端筛选）", () => {
  const base = [
    event({ id: "e1", result: "success", action: "user.password.reset", occurred_at: "2026-08-18T09:00:00Z" }),
    event({
      id: "e2",
      result: "denied",
      action: "provisioning.retry",
      occurred_at: "2026-08-18T08:00:00Z",
      actor_type: "system",
      actor_system_component: "provisioning-worker",
    }),
    event({
      id: "e3",
      result: "failed",
      action: "user.delete",
      occurred_at: "2026-08-17T09:00:00Z",
      subject_account_id: "acc-1",
    }),
  ];

  it("空筛选返回全部", () => {
    expect(
      filterAuditEvents(base, { result: "all", action: "", actor: "", subject: "", since: "", until: "" }),
    ).toHaveLength(3);
  });

  it("按结果筛选", () => {
    const denied = filterAuditEvents(base, { result: "denied", action: "", actor: "", subject: "", since: "", until: "" });
    expect(denied.map((e) => e.id)).toEqual(["e2"]);
  });

  it("按动作子串筛选", () => {
    const byAction = filterAuditEvents(base, { result: "all", action: "user.", actor: "", subject: "", since: "", until: "" });
    expect(byAction.map((e) => e.id).sort()).toEqual(["e1", "e3"]);
  });

  it("按 Actor（组件名/User ID）与 Subject 筛选", () => {
    const byActor = filterAuditEvents(base, { result: "all", action: "", actor: "provisioning", subject: "", since: "", until: "" });
    expect(byActor.map((e) => e.id)).toEqual(["e2"]);

    // e3 的 Subject 含 Account（acc-1），e1/e2 的 Subject 只有 User ID
    const bySubject = filterAuditEvents(base, { result: "all", action: "", actor: "", subject: "acc-1", since: "", until: "" });
    expect(bySubject.map((e) => e.id)).toEqual(["e3"]);
  });

  it("按时间范围筛选（since 含当日零点起）", () => {
    const since = filterAuditEvents(base, { result: "all", action: "", actor: "", subject: "", since: "2026-08-18", until: "" });
    expect(since.map((e) => e.id)).toEqual(["e1", "e2"]);

    const range = filterAuditEvents(base, { result: "all", action: "", actor: "", subject: "", since: "2026-08-17", until: "2026-08-17" });
    expect(range.map((e) => e.id)).toEqual(["e3"]);
  });
});
