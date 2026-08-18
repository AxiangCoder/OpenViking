/**
 * ErrorPage 单测（05 §12.2，AC⑤）：错误 UI 展示 Request ID，不泄露底层异常。
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import ErrorPage, { extractRequestId } from "./ErrorPage";
import { PlatformError } from "../lib/platform-client";

describe("ErrorPage（AC⑤）", () => {
  it("PlatformError：展示稳定错误码 + 服务端回显的 Request ID", () => {
    render(
      <ErrorPage
        error={
          new PlatformError({
            code: "PERMISSION_NOT_GRANTED",
            status: 403,
            message: "无权限",
            requestId: "req-abc-123",
          })
        }
      />,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("PERMISSION_NOT_GRANTED")).toBeInTheDocument();
    expect(screen.getByText("req-abc-123")).toBeInTheDocument();
  });

  it("非平台错误：不展示 Request ID，仍给统一兜底文案（不泄露底层异常）", () => {
    render(<ErrorPage error={new Error("secret internal detail")} />);
    expect(screen.getByText("UNKNOWN")).toBeInTheDocument();
    expect(screen.queryByText("secret internal detail")).not.toBeInTheDocument();
  });

  it("extractRequestId：从嵌套对象提取", () => {
    expect(extractRequestId({ requestId: "rid-1" })).toBe("rid-1");
    expect(extractRequestId(null)).toBeNull();
  });
});
