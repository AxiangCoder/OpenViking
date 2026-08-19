/**
 * Resource 表单校验单测（09 §40.3/§40.4/§40.6，P3-E4 AC④⑧）。
 * 标签严格 key=value；远程来源仅公开 HTTPS、禁 userinfo/私网。
 */

import { describe, expect, it } from "vitest";
import {
  isStableRemoteUrl,
  normalizeTags,
  validateGitUrl,
  validateRemoteUrl,
  validateResourceFormBase,
} from "@/features/resources";

describe("normalizeTags（AC④：标签严格 key=value）", () => {
  it("合法标签：转小写、去重、允许空输入", () => {
    expect(normalizeTags([])).toEqual({ tags: [], error: null });
    expect(normalizeTags(["Type=DOC", "type=doc", "project=OpenViking"])).toEqual({
      tags: ["type=doc", "project=openviking"],
      error: null,
    });
    expect(normalizeTags(["  a=b  ", ""])).toEqual({ tags: ["a=b"], error: null });
  });

  it("非法：缺 '='、key/value 为空、含空格、超长、超量", () => {
    expect(normalizeTags(["notag"]).error).toMatch(/key=value/);
    expect(normalizeTags(["=value"]).error).toMatch(/key=value/);
    expect(normalizeTags(["key="]).error).toMatch(/key=value/);
    expect(normalizeTags(["key =value"]).error).toMatch(/key=value/);
    expect(normalizeTags(["a".repeat(41) + "=b"]).error).toMatch(/40/);
    expect(normalizeTags(Array.from({ length: 21 }, (_, i) => `k${i}=v`)).error).toMatch(
      /20/,
    );
  });
});

describe("validateResourceFormBase（09 §40.3 字段限制）", () => {
  const base = { name: "", description: "", tags: [], instruction: "" };
  it("空表单合法（名称可选，服务端从来源生成）", () => {
    expect(validateResourceFormBase(base)).toBeNull();
  });
  it("名称 128、说明 1000、处理要求 2000 上限", () => {
    expect(validateResourceFormBase({ ...base, name: "n".repeat(129) })).toMatch(/128/);
    expect(validateResourceFormBase({ ...base, description: "d".repeat(1001) })).toMatch(/1000/);
    expect(validateResourceFormBase({ ...base, instruction: "i".repeat(2001) })).toMatch(/2000/);
  });
  it("标签非法时整体不可提交", () => {
    expect(validateResourceFormBase({ ...base, tags: ["bad"] })).toMatch(/key=value/);
  });
});

describe("validateRemoteUrl（09 §40.6，AC⑧）", () => {
  it("接受公开 HTTPS（含 Query：可一次性导入）", () => {
    expect(validateRemoteUrl("https://example.com/docs?page=1").error).toBeNull();
    expect(validateRemoteUrl("https://github.com/org/repo").error).toBeNull();
  });

  it("拒绝 userinfo、file://、ftp://、非 http(s) 协议", () => {
    expect(validateRemoteUrl("https://user:pass@example.com/").error).toMatch(/userinfo|用户名|密码/);
    expect(validateRemoteUrl("file:///etc/passwd").error).toMatch(/协议/);
    expect(validateRemoteUrl("ftp://example.com/f").error).toMatch(/协议/);
    expect(validateRemoteUrl("not a url").error).toMatch(/URL/);
  });

  it("拒绝 localhost/私网/云元数据（SSRF 防护）", () => {
    for (const bad of [
      "https://localhost:8080/x",
      "https://127.0.0.1/x",
      "https://10.0.0.8/x",
      "https://192.168.1.1/x",
      "https://169.254.169.254/latest/meta-data",
      "https://172.16.0.1/x",
      "http://[::1]/x",
    ]) {
      expect(validateRemoteUrl(bad).error).toMatch(/SSRF|localhost|内网/);
    }
  });
});

describe("validateGitUrl（09 §40.6：仅公开 HTTPS，SSH/git@ 拒绝）", () => {
  it("拒绝 SSH 形态", () => {
    expect(validateGitUrl("git@github.com:org/repo.git").error).toMatch(/HTTPS/);
    expect(validateGitUrl("ssh://git@github.com/org/repo.git").error).toMatch(/HTTPS/);
  });
  it("接受公开 HTTPS 仓库", () => {
    expect(validateGitUrl("https://github.com/openviking/ov.git").error).toBeNull();
  });
});

describe("isStableRemoteUrl（可 Watch：无 userinfo/Query/Fragment，09 §40.6）", () => {
  it("稳定：裸公开 URL", () => {
    expect(isStableRemoteUrl("https://example.com/docs")).toBe(true);
  });
  it("不稳定：带 Query/Fragment", () => {
    expect(isStableRemoteUrl("https://example.com/docs?token=1")).toBe(false);
    expect(isStableRemoteUrl("https://example.com/docs#top")).toBe(false);
  });
});
