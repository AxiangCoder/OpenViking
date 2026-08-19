/**
 * 稳定错误码（05 §12.2）：业务错误使用稳定 code，不依赖英文 message 判断。
 *
 * 本文件是前端唯一错误码事实来源（P3-E1 冻结点，后续 Epic 只追加新码）。
 * 后端信封经 app.py exception handler 规范为 `{status:"error", error:{code,...}}`；
 * 未挂载 handler 的 FastAPI 默认形态为 `{"detail":{"code":...}}`，client 两种都解析。
 */

export const STABLE_ERROR_CODES = [
  "INVALID_ARGUMENT",
  "UNAUTHENTICATED",
  "PERMISSION_DENIED",
  "PERMISSION_NOT_GRANTED",
  "NOT_FOUND",
  "CONFLICT",
  "CONSTRAINT_VIOLATION",
  "RESOURCE_EXHAUSTED",
  "UNAVAILABLE",
  "DEADLINE_EXCEEDED",
  "INTERNAL",
  "UNKNOWN",
  "LOGIN_FAILED",
  "LOGIN_RATE_LIMITED",
  "SESSION_EXPIRED",
  "CSRF_INVALID",
  "ACCOUNT_SUSPENDED",
  "USER_DISABLED",
  "INVALID_CREDENTIAL",
  "ACCOUNT_CODE_ALREADY_EXISTS",
  "EMAIL_ALREADY_EXISTS",
  "LAST_ACCOUNT_ADMIN_REQUIRED",
  "PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN",
  "ROLE_IN_USE",
  "PROVISIONING_PENDING",
  "PROVISIONING_FAILED",
  "DELETION_PENDING",
  "RESTORE_WINDOW_EXPIRED",
  "INVALID_CURSOR",
  // resource（09 §46.5 稳定错误码，P3-E4 追加）
  "RESOURCE_NOT_FOUND",
  "RESOURCE_BUSY",
  "RESOURCE_VERSION_CONFLICT",
  "RESOURCE_SOURCE_UNSUPPORTED",
  "RESOURCE_SOURCE_NOT_STABLE",
  "RESOURCE_SOURCE_BLOCKED",
  "RESOURCE_UPLOAD_EXPIRED",
  "RESOURCE_UPLOAD_ALREADY_CONSUMED",
  "RESOURCE_FILE_TOO_LARGE",
  "RESOURCE_FORMAT_UNSUPPORTED",
  "RESOURCE_PARSE_FAILED",
  "RESOURCE_INDEX_FAILED",
  "RESOURCE_WATCH_UNAVAILABLE",
  "RESOURCE_WATCH_CONFLICT",
  "RESOURCE_OPERATION_NOT_CANCELLABLE",
  "RESOURCE_DELETION_PENDING",
  "RESOURCE_RESTORE_WINDOW_EXPIRED",
  "RESOURCE_PUBLISH_FORBIDDEN",
  "TAG_INVALID_FORMAT",
  "TAG_LIMIT_EXCEEDED",
  "TAG_TOO_LONG",
  // Skill（10 §63，P3-E5；SKILL_RESTORE_NAME_CONFLICT 外部统一映射 SKILL_NAME_CONFLICT）
  "SKILL_NAME_CONFLICT",
  "SKILL_NAME_IMMUTABLE",
  "SKILL_INVALID_FORMAT",
  "SKILL_SHARED_WRITE_FORBIDDEN",
  "SKILL_PRIVATE_MANAGE_FORBIDDEN",
  "SKILL_PUBLISH_FORBIDDEN",
  "SKILL_UNPUBLISH_UNSUPPORTED",
  // Skill 私密配置（05 §12.5，P3-E5）
  "CONFIG_INVALID",
  "CONFIG_VERSION_NOT_FOUND",
  // 一次性上传（04 §10.14，05 §12.5，P3-E4/E5）
  "UPLOAD_NOT_FOUND",
  "UPLOAD_EXPIRED",
  "UPLOAD_CONSUMED",
  "UPLOAD_SCOPE_MISMATCH",
  // 删除/恢复（04 §10.11，05 §12.6）
  "NOT_RESTORABLE",
  "ALREADY_RESTORED",
] as const;

export type StableErrorCode = (typeof STABLE_ERROR_CODES)[number];

/**
 * 平台 API 业务错误。`requestId` 为服务端回显的 X-Request-ID（错误页必须展示，
 * 05 §12.2「错误 UI 展示 Request ID 不泄露底层异常」）。
 */
export class PlatformError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string | null;
  /**
   * 服务端 Retry-After（秒）：登录/改密限流时随 401 LOGIN_FAILED 下发
   * （03 §8.3，P3-E2 登录页冷却用；非限流响应为 null）。
   */
  readonly retryAfterSeconds: number | null;

  constructor(options: {
    code: string;
    status: number;
    message?: string;
    requestId?: string | null;
    retryAfterSeconds?: number | null;
  }) {
    super(options.message || options.code);
    this.name = "PlatformError";
    this.code = options.code;
    this.status = options.status;
    this.requestId = options.requestId ?? null;
    this.retryAfterSeconds = options.retryAfterSeconds ?? null;
  }
}

export function isPlatformError(error: unknown): error is PlatformError {
  return error instanceof PlatformError;
}
