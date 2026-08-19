/**
 * lib/links.ts 单测：跳转契约冻结（AC⑥：URL 无 Viking URI/Account/User ID）。
 */

import { describe, expect, it } from "vitest";
import { isProductId, resourceDetailPath, searchTargetPath, skillDetailPath } from "@/lib/links";

const UUID = "3f2b9c4e-1a5d-4b7e-9c2f-6d8a0b1e3c45";

describe("links（跳转契约，14 号计划 §98.1）", () => {
  it("产品 ID 校验", () => {
    expect(isProductId(UUID)).toBe(true);
    expect(isProductId("viking://resources/abc")).toBe(false);
    expect(isProductId("acc-123")).toBe(false);
    expect(isProductId("not-a-uuid")).toBe(false);
  });

  it("Resource 详情路径按 §13.2 生成", () => {
    expect(resourceDetailPath("private", UUID)).toBe(`/app/resources/private/${UUID}`);
    expect(resourceDetailPath("shared", UUID)).toBe(`/app/resources/shared/${UUID}`);
  });

  it("Skill 详情路径按 §13.2 生成", () => {
    expect(skillDetailPath("private", UUID)).toBe(`/app/skills/private/${UUID}`);
    expect(skillDetailPath("shared", UUID)).toBe(`/app/skills/shared/${UUID}`);
  });

  it("非法 ID（Viking URI/Account/User 标识符）拒绝生成链接", () => {
    expect(() => resourceDetailPath("private", "viking://resources/abc")).toThrow(/拒绝/);
    expect(() => skillDetailPath("shared", "u-42")).toThrow(/拒绝/);
    expect(() => resourceDetailPath("private", "acc-acme")).toThrow(/拒绝/);
  });

  it("Search 结果 → 详情链接映射（P3-E3 消费）", () => {
    expect(
      searchTargetPath({ kind: "resource", visibility: "shared", productId: UUID }),
    ).toBe(`/app/resources/shared/${UUID}`);
    expect(
      searchTargetPath({ kind: "skill", visibility: "private", productId: UUID }),
    ).toBe(`/app/skills/private/${UUID}`);
  });
});
