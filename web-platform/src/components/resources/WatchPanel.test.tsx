/**
 * 自动同步（Watch）面板测试（09 §43.2，P3-E4 AC⑤）。
 *
 * - 周期只接受 Capabilities 预设值；
 * - 上传文件不能 Watch（面板显示不可用说明，无配置入口）；
 * - RESOURCE_BUSY：current_operation 运行中时按钮禁用；
 * - 无写权限只读；启用/暂停/恢复/立即同步/停止走对应端点。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { configurePlatformClient } from "@/lib/platform-client";
import WatchPanel from "@/components/resources/WatchPanel";
import type { ResourceCapabilities, ResourceDetail } from "@/features/resources";

const CAPABILITIES: ResourceCapabilities = {
  source_types: ["upload", "web", "git"],
  upload: { max_files_per_batch: 3, max_file_size_bytes: 10 * 1024 * 1024, ttl_minutes: 15, accepts_archives: false },
  watch: { enabled: true, interval_presets_minutes: [60, 360, 1440], manual_refresh_min_interval_seconds: 300 },
  git: { ignore_dirs_max: 50, include_exclude_max: 20, processing_mode_fixed: "semantic_and_vectors" },
};

function baseResource(overrides: Partial<ResourceDetail> = {}): ResourceDetail {
  return {
    id: "res_00000000-0000-0000-0000-000000000001",
    visibility: "user_private",
    name: "文档",
    description: null,
    source_type: "web",
    source_display: "example.com",
    tags: [],
    lifecycle_status: "active",
    processing: { state: "succeeded", stage: "succeeded", latest_operation_id: null, last_succeeded_at: null },
    watch: { state: "not_configured" },
    version: 1,
    created_at: null,
    updated_at: null,
    overview: null,
    content: { node_count: 1, size_bytes: 100 },
    current_operation: null,
    ...overrides,
  };
}

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("WatchPanel（09 §43.2，AC⑤）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let changed: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async () =>
      jsonResponse(200, { status: "ok", result: { state: "active", interval_minutes: 360 } }),
    );
    configurePlatformClient({ fetchImpl: fetchMock, csrfTokenProvider: () => "csrf" });
    changed = vi.fn();
  });

  afterEach(() => {
    configurePlatformClient({ fetchImpl: undefined, csrfTokenProvider: undefined });
  });

  it("上传文件不能 Watch：显示不可用说明且无配置按钮（AC⑤）", () => {
    render(
      <WatchPanel
        scope="private"
        resource={baseResource({ source_type: "upload", source_display: "a.pdf" })}
        capabilities={CAPABILITIES}
        canWrite
        onChanged={changed}
      />,
    );
    expect(screen.getByText(/上传文件不支持自动同步/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("Watch 未启用：周期选项取自 Capabilities 预设（AC⑤ 不硬编码）", async () => {
    render(
      <WatchPanel
        scope="private"
        resource={baseResource()}
        capabilities={CAPABILITIES}
        canWrite
        onChanged={changed}
      />,
    );
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(Array.from(select.options).map((option) => option.value)).toEqual(["60", "360", "1440"]);
    fireEvent.change(select, { target: { value: "360" } });
    fireEvent.click(screen.getByRole("button", { name: "启用自动同步" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/platform/v1/me/resources/00000000-0000-0000-0000-000000000001/watch",
        expect.objectContaining({ method: "PUT" }),
      );
    });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ interval_minutes: 360 });
    await waitFor(() => expect(changed).toHaveBeenCalled());
  });

  it("RESOURCE_BUSY：运行中的 Operation 使全部按钮禁用（AC⑤）", () => {
    render(
      <WatchPanel
        scope="private"
        resource={baseResource({
          watch: { state: "active", interval_minutes: 360 },
          current_operation: {
            id: "op-1",
            operation_type: "resource_watch",
            status: "running",
            stage: "fetching",
            initiated_by: "user",
            created_at: null,
            completed_at: null,
            cancellable: true,
            error: null,
            generation: 2,
          },
        })}
        capabilities={CAPABILITIES}
        canWrite
        onChanged={changed}
      />,
    );
    const buttons = screen.getAllByRole("button");
    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
      expect(button).toBeDisabled();
    }
  });

  it("暂停 → POST /watch/pause；停止自动同步 → DELETE /watch（不删除 Resource）", async () => {
    render(
      <WatchPanel
        scope="private"
        resource={baseResource({ watch: { state: "active", interval_minutes: 360 } })}
        capabilities={CAPABILITIES}
        canWrite
        onChanged={changed}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "暂停" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/platform/v1/me/resources/00000000-0000-0000-0000-000000000001/watch/pause",
        expect.objectContaining({ method: "POST" }),
      );
    });
    fireEvent.click(screen.getByRole("button", { name: "停止自动同步（不删除 Resource）" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/platform/v1/me/resources/00000000-0000-0000-0000-000000000001/watch",
        expect.objectContaining({ method: "DELETE" }),
      );
    });
    await waitFor(() =>
      expect(changed).toHaveBeenLastCalledWith({ state: "not_configured" }),
    );
  });

  it("无写权限：配置区只读提示，无动作按钮", () => {
    render(
      <WatchPanel
        scope="shared"
        resource={baseResource({ watch: { state: "active", interval_minutes: 360 } })}
        capabilities={CAPABILITIES}
        canWrite={false}
        onChanged={changed}
      />,
    );
    expect(screen.getByText(/无写权限，自动同步配置只读/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
