/**
 * 通用错误页（05 §12.2）：展示稳定错误码与 Request ID，不泄露底层异常。
 * 绕过 Guard 直接请求后端仍 403/404 时，错误同样落到本页并带 Request ID（AC⑤）。
 */

import { isPlatformError } from "@/lib/platform-client";

export function extractRequestId(error: unknown): string | null {
  if (isPlatformError(error)) return error.requestId;
  if (typeof error === "object" && error !== null) {
    const requestId = (error as { requestId?: unknown }).requestId;
    if (typeof requestId === "string") return requestId;
  }
  return null;
}

export default function ErrorPage({ error }: { error?: unknown }) {
  const requestId = extractRequestId(error);
  const code = isPlatformError(error) ? error.code : "UNKNOWN";
  const message = error instanceof Error ? error.message : "发生未知错误";

  return (
    <div className="error-page" role="alert">
      <h2>请求失败</h2>
      <p className="error-page-code">{code}</p>
      <p className="error-page-message">{message}</p>
      {requestId ? (
        <p className="error-page-request-id">
          请求 ID：<code>{requestId}</code>
        </p>
      ) : null}
      <p className="error-page-hint">如问题持续，请携带上述请求 ID 联系管理员。</p>
    </div>
  );
}
