/**
 * 一次性凭证组件单测（06 §13.9、13 §85.2/85.4，P4-E1 AC②④⑧）。
 *
 * - 凭证只在当前视图展示，可复制；
 * - 关闭后由父组件卸载，组件本身不落任何存储（06 §13.5）；
 * - 交接收限制提示（06 §13.9，AC⑧）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import OneTimeSecret from "@/components/ui/OneTimeSecret";

describe("OneTimeSecret（一次性凭证展示）", () => {
  let clipboardWrite: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    clipboardWrite = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: clipboardWrite },
    });
  });

  afterEach(() => {
    cleanup();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
  });

  it("展示凭证与交接收限制提示，复制调用 clipboard（AC②⑧）", async () => {
    render(
      <OneTimeSecret
        title="密码已重置"
        secret="Sec-1234"
        subjectLine="用户：alice（alice@example.com）"
        onClose={() => undefined}
        testIdPrefix="reset-secret"
      />,
    );
    expect(screen.getByTestId("reset-secret-secret")).toHaveTextContent("Sec-1234");
    expect(screen.getByTestId("reset-secret-subject")).toHaveTextContent("alice@example.com");
    expect(screen.getByTestId("reset-secret-once-notice")).toHaveTextContent("只显示这一次");
    // AC⑧：交接收限制提示（创建者可能长期知晓密码）
    expect(screen.getByTestId("reset-secret-handover")).toHaveTextContent("创建者可能长期知晓该密码");

    fireEvent.click(screen.getByTestId("reset-secret-copy"));
    expect(clipboardWrite).toHaveBeenCalledWith("Sec-1234");
  });

  it("关闭回调触发（凭证视图随父组件内存态卸载，不可再取）", () => {
    const onClose = vi.fn();
    render(
      <OneTimeSecret
        title="用户已创建"
        secret="Init-5678"
        onClose={onClose}
        testIdPrefix="created-secret"
      />,
    );
    fireEvent.click(screen.getByTestId("created-secret-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("复制失败时提示手动选择复制，不抛错", async () => {
    clipboardWrite.mockRejectedValue(new Error("denied"));
    render(<OneTimeSecret title="密码已重置" secret="Sec-1234" onClose={() => undefined} />);
    fireEvent.click(screen.getByTestId("one-time-secret-copy"));
    expect(await screen.findByText("复制失败，请手动选择复制。")).toBeInTheDocument();
  });
});
