/**
 * Skill 前端校验（10 §54.1/§56.1，05 §12.5，P3-E5 AC②）。
 */

import { describe, expect, it } from "vitest";
import {
  describeUploadFile,
  normalizeTags,
  splitTagsInput,
  splitToolsInput,
  validateSkillName,
} from "./validation";

describe("validateSkillName（10 §54.1：≤64 字符、ASCII 字母/数字/下划线/连字符，AC②）", () => {
  it("接受合法名称", () => {
    expect(validateSkillName("fix-me").ok).toBe(true);
    expect(validateSkillName("Fix_Me_2026").ok).toBe(true);
    expect(validateSkillName("a").ok).toBe(true);
    expect(validateSkillName("a".repeat(64)).ok).toBe(true);
    expect(validateSkillName("  trim-me  ").ok).toBe(true);
  });

  it("拒绝空/超长/非法字符", () => {
    expect(validateSkillName("").ok).toBe(false);
    expect(validateSkillName("   ").ok).toBe(false);
    expect(validateSkillName("a".repeat(65)).ok).toBe(false);
    expect(validateSkillName("中文名").ok).toBe(false);
    expect(validateSkillName("has space").ok).toBe(false);
    expect(validateSkillName("dot.name").ok).toBe(false);
    expect(validateSkillName("slash/name").ok).toBe(false);
  });
});

describe("normalizeTags（05 §12.5：≤20 项、≤40 字符、key=value、小写去重）", () => {
  it("规范化为小写 key=value 并去重", () => {
    const result = normalizeTags(["Type=Guide", "project=OpenViking", "type=guide"]);
    expect(result.ok).toBe(true);
    expect(result.tags).toEqual(["type=guide", "project=openviking"]);
  });

  it("拒绝缺少 = 或 key/value 为空的条目", () => {
    expect(normalizeTags(["plain"]).ok).toBe(false);
    expect(normalizeTags(["=value"]).ok).toBe(false);
    expect(normalizeTags(["key="]).ok).toBe(false);
  });

  it("拒绝超长条目与超量条目", () => {
    expect(normalizeTags([`${"k".repeat(20)}=${"v".repeat(25)}`]).ok).toBe(false);
    expect(normalizeTags(Array.from({ length: 21 }, (_, i) => `k${i}=v`)).ok).toBe(false);
  });

  it("splitTagsInput 支持中英文逗号与换行", () => {
    expect(splitTagsInput("a=1, b=2，c=3\n d=4")).toEqual(["a=1", "b=2", "c=3", "d=4"]);
  });

  it("splitToolsInput 拆分并去空", () => {
    expect(splitToolsInput("read_file, write_file\n, run")).toEqual(["read_file", "write_file", "run"]);
  });
});

describe("describeUploadFile（10 §54.2 上传形态提示）", () => {
  it("识别 .md 与 .zip，其余为 other", () => {
    expect(describeUploadFile("SKILL.md")).toBe("skill_md");
    expect(describeUploadFile("guide.md")).toBe("skill_md");
    expect(describeUploadFile("skill.zip")).toBe("zip");
    expect(describeUploadFile("notes.txt")).toBe("other");
  });
});
