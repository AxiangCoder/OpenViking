/**
 * Resource 展示格式化单测（09 §39.2/§41.1/§42，P3-E4）。
 * 上传文件不显示 Watch 列；Refresh 失败保持可用状态文案。
 */

import { describe, expect, it } from "vitest";
import {
  formatBytes,
  latestOperationText,
  operationTypeLabel,
  watchColumnText,
} from "@/features/resources";

describe("formatBytes", () => {
  it("字节/千字节/兆字节/吉字节", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatBytes(2 * 1024 * 1024 * 1024)).toBe("2.00 GB");
    expect(formatBytes(null)).toBe("—");
  });
});

describe("watchColumnText（09 §39.2：上传文件不显示 Watch，AC⑤）", () => {
  it("上传来源返回 null", () => {
    expect(watchColumnText("upload", { state: "active" })).toBeNull();
  });
  it("远程来源映射状态", () => {
    expect(watchColumnText("web", { state: "active" })).toBe("运行中");
    expect(watchColumnText("web", { state: "paused" })).toBe("已暂停");
    expect(watchColumnText("git", null)).toBe("未启用");
  });
});

describe("latestOperationText（09 §39.2：Refresh 失败不改变可用版本）", () => {
  it("首次处理中", () => {
    expect(
      latestOperationText({
        lifecycle_status: "provisioning",
        processing: { state: "fetching", last_succeeded_at: null },
      }),
    ).toContain("正在获取来源");
  });
  it("首次失败", () => {
    expect(
      latestOperationText({
        lifecycle_status: "failed",
        processing: { state: "failed", last_succeeded_at: null },
      }),
    ).toBe("首次处理失败");
  });
  it("最近同步失败（对象仍可用）", () => {
    expect(
      latestOperationText({
        lifecycle_status: "active",
        processing: { state: "failed", last_succeeded_at: "2026-08-01T00:00:00Z" },
      }),
    ).toBe("最近同步失败");
  });
  it("正在同步", () => {
    expect(
      latestOperationText({
        lifecycle_status: "active",
        processing: { state: "indexing", last_succeeded_at: "2026-08-01T00:00:00Z" },
      }),
    ).toBe("正在同步");
  });
});

describe("operationTypeLabel（09 §43.3）", () => {
  it("产品类型映射，未知回落", () => {
    expect(operationTypeLabel("resource_import")).toBe("导入");
    expect(operationTypeLabel("resource_replace")).toBe("替换来源");
    expect(operationTypeLabel("resource_refresh")).toBe("刷新");
    expect(operationTypeLabel("resource_watch")).toBe("自动同步");
    expect(operationTypeLabel("unknown_kind")).toBe("处理任务");
  });
});
