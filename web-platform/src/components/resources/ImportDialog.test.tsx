/**
 * 新增 Resource 弹窗测试（09 §40，P3-E4 AC①②）。
 *
 * - AC①：入口固定归属——无归属下拉，目标文案由挂载方传入；
 * - AC②：上传数量/大小限制取自 capabilities，不硬编码；批量按文件
 *   独立成败（上传失败项不阻断其他文件与批次提交）；
 * - AC④：标签严格 key=value 校验；
 * - AC⑧：URL 校验拒绝 userinfo/私网；导入请求体无 visibility/URI。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { configurePlatformClient } from "@/lib/platform-client";
import ImportDialog from "@/components/resources/ImportDialog";

const CAPABILITIES = {
  source_types: ["upload", "web", "git"] as const,
  upload: { max_files_per_batch: 3, max_file_size_bytes: 10 * 1024 * 1024, ttl_minutes: 15, accepts_archives: false },
  watch: { enabled: true, interval_presets_minutes: [60, 360, 1440], manual_refresh_min_interval_seconds: 300 },
  git: { ignore_dirs_max: 50, include_exclude_max: 20, processing_mode_fixed: "semantic_and_vectors" },
};

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function makeFile(name: string, size = 1024): File {
  return new File([new Uint8Array(size)], name, { type: "text/plain" });
}

describe("ImportDialog（09 §40，AC①②）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url).endsWith("/resources/capabilities")) {
        return jsonResponse(200, { status: "ok", result: CAPABILITIES });
      }
      if (String(url).endsWith("/resource-uploads")) {
        const body = init?.body as FormData;
        const file = body.get("file") as File;
        return jsonResponse(200, {
          status: "ok",
          result: {
            upload_id: `up-${file.name}`,
            filename: file.name,
            mime_type: "text/plain",
            size_bytes: file.size,
            expires_at: "2026-08-19T01:00:00Z",
          },
        });
      }
      if (String(url).endsWith("/resources/imports")) {
        return jsonResponse(200, {
          status: "ok",
          result: { batch_id: "b-1", items: [{ resource_id: "r-1", operation_id: "op-1", error: null }] },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });
    configurePlatformClient({ fetchImpl: fetchMock, csrfTokenProvider: () => "csrf" });
  });

  afterEach(() => {
    configurePlatformClient({ fetchImpl: undefined, csrfTokenProvider: undefined });
  });

  it("AC①：入口固定归属——展示「保存到：我的 Resource」且无归属下拉", async () => {
    render(<ImportDialog scope="private" targetLabel="我的 Resource" onClose={() => undefined} onImported={() => undefined} />);
    expect(await screen.findByText("我的 Resource")).toBeInTheDocument();
    expect(screen.getByText(/入口固定归属，无归属切换/)).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("共享入口：展示 Account 共享目标文案", async () => {
    render(<ImportDialog scope="shared" targetLabel="测试Account 共享 Resource" onClose={() => undefined} onImported={() => undefined} />);
    expect(await screen.findByText("测试Account 共享 Resource")).toBeInTheDocument();
  });

  it("AC②：上传数量/大小限制取自 capabilities 文案", async () => {
    render(<ImportDialog scope="private" targetLabel="我的 Resource" onClose={() => undefined} onImported={() => undefined} />);
    expect(await screen.findByText(/每批最多 3 个文件/)).toBeInTheDocument();
    expect(await screen.findByText(/10 MiB/)).toBeInTheDocument();
  });

  it("AC②：超量文件被拒绝（提示来自 capabilities，不硬编码）", async () => {
    render(<ImportDialog scope="private" targetLabel="我的 Resource" onClose={() => undefined} onImported={() => undefined} />);
    await screen.findByText(/每批最多 3 个文件/);
    const input = screen.getByLabelText("选择文件") as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [makeFile("a.txt"), makeFile("b.txt"), makeFile("c.txt"), makeFile("d.txt")] },
    });
    expect(await screen.findByText(/其余文件未添加/)).toBeInTheDocument();
    const list = screen.getByRole("list");
    expect(within(list).queryByText(/d\.txt/)).not.toBeInTheDocument();
  });

  it("AC②：超限单文件标记失败但不阻断其他文件", async () => {
    render(<ImportDialog scope="private" targetLabel="我的 Resource" onClose={() => undefined} onImported={() => undefined} />);
    await screen.findByText(/每批最多 3 个文件/);
    const input = screen.getByLabelText("选择文件") as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [makeFile("ok.txt", 1024), makeFile("big.txt", 11 * 1024 * 1024)] },
    });
    expect(await screen.findByText(/超过单文件/)).toBeInTheDocument();
    expect(screen.getByText(/ok\.txt/)).toBeInTheDocument();
  });

  it("AC②：批量按文件独立成败——上传失败项标记失败，其余照常提交批次", async () => {
    const uploadUrl = "/api/platform/v1/me/resource-uploads";
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url).endsWith("/resources/capabilities")) {
        return jsonResponse(200, { status: "ok", result: CAPABILITIES });
      }
      if (String(url).endsWith("/resource-uploads")) {
        const body = init?.body as FormData;
        const file = body.get("file") as File;
        if (file.name === "bad.txt") {
          return jsonResponse(413, {
            status: "error",
            error: { code: "RESOURCE_FILE_TOO_LARGE", message: "too large" },
          });
        }
        return jsonResponse(200, {
          status: "ok",
          result: { upload_id: `up-${file.name}`, filename: file.name, mime_type: "text/plain", size_bytes: 1, expires_at: "" },
        });
      }
      if (String(url).endsWith("/resources/imports")) {
        return jsonResponse(200, {
          status: "ok",
          result: { batch_id: "b-1", items: [{ resource_id: "r-1", operation_id: "op-1", error: null }] },
        });
      }
      return jsonResponse(200, { status: "ok", result: null });
    });

    render(<ImportDialog scope="private" targetLabel="我的 Resource" onClose={() => undefined} onImported={() => undefined} />);
    await screen.findByText(/每批最多 3 个文件/);
    const input = screen.getByLabelText("选择文件") as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [makeFile("bad.txt"), makeFile("good.txt")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始导入" }));
    await waitFor(() => {
      expect(screen.getByText(/bad\.txt.*失败|失败.*bad\.txt/)).toBeInTheDocument();
    });
    // 批次仅包含成功上传的文件（bad.txt 不上传）
    const importsCall = fetchMock.mock.calls.find((call) =>
      String(call[0]).endsWith("/me/resources/imports"),
    );
    expect(importsCall).toBeDefined();
    const sent = JSON.parse(String(importsCall![1].body)) as { items: { upload_id: string }[] };
    expect(sent.items.map((item) => item.upload_id)).toEqual(["up-good.txt"]);
    expect(fetchMock.mock.calls.filter((call) => String(call[0]) === uploadUrl).length).toBe(2);
    expect(await screen.findByText(/导入请求已提交/)).toBeInTheDocument();
  });

  it("AC⑧：网页来源拒绝 userinfo URL；导入请求体无 visibility/URI", async () => {
    render(<ImportDialog scope="private" targetLabel="我的 Resource" onClose={() => undefined} onImported={() => undefined} />);
    await screen.findByText(/每批最多 3 个文件/);
    fireEvent.click(screen.getByRole("button", { name: "公开网页" }));
    const urlInput = screen.getByPlaceholderText("https://example.com/article");
    fireEvent.change(urlInput, { target: { value: "https://user:pass@example.com/x" } });
    fireEvent.click(screen.getByRole("button", { name: "开始导入" }));
    expect(await screen.findByText(/用户名\/密码/)).toBeInTheDocument();

    fireEvent.change(urlInput, { target: { value: "https://example.com/article" } });
    fireEvent.click(screen.getByRole("button", { name: "开始导入" }));
    await waitFor(() => {
      const importsCall = fetchMock.mock.calls.find((call) =>
        String(call[0]).endsWith("/me/resources/imports"),
      );
      expect(importsCall).toBeDefined();
      const sent = JSON.parse(String(importsCall![1].body)) as { items: Record<string, unknown>[] };
      expect(sent.items[0]).toMatchObject({ source_url: "https://example.com/article" });
      expect("visibility" in sent.items[0]).toBe(false);
      expect("uri" in sent.items[0]).toBe(false);
    });
  });

  it("AC④：标签非法（非 key=value）阻止提交", async () => {
    render(<ImportDialog scope="private" targetLabel="我的 Resource" onClose={() => undefined} onImported={() => undefined} />);
    await screen.findByText(/每批最多 3 个文件/);
    const tagInput = screen.getByPlaceholderText("type=requirement, project=openviking");
    fireEvent.change(tagInput, { target: { value: "bad-tag" } });
    fireEvent.click(screen.getByRole("button", { name: "公开网页" }));
    fireEvent.change(screen.getByPlaceholderText("https://example.com/article"), {
      target: { value: "https://example.com/article" },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始导入" }));
    const matches = await screen.findAllByText(/key=value/);
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });
});
