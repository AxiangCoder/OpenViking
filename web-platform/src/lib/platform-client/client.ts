/**
 * Platform API 客户端（05 §12.2–12.3，P3-E1 冻结点）。
 *
 * - 登录状态来自 HttpOnly Cookie（`__Host-ov_session`），`credentials: "include"`；
 * - 每个请求携带客户端生成的 `X-Request-ID`（服务端原样回显，错误页展示）；
 * - 写请求携带内存态 CSRF Token（03 §8.2，Token 仅内存持有、绝不落存储）；
 * - 统一解析 `{status, result, error}` envelope，稳定错误码映射为 PlatformError；
 * - 401/403 触发全局 auth-challenge 事件（auth 状态层刷新 /auth/me，AC③）。
 */

import { PlatformError, isPlatformError } from "./errors";

export const API_PREFIX = "/api/platform/v1";

export const AUTH_CHALLENGE_EVENT = "platform:auth-challenge";

const REQUEST_ID_HEADER = "X-Request-ID";
const CSRF_HEADER = "X-CSRF-Token";
const IDEMPOTENCY_HEADER = "Idempotency-Key";

interface EnvelopeOk {
  status: "ok";
  result: unknown;
}

interface EnvelopeErrorBody {
  status?: string;
  result?: unknown;
  error?: { code?: string; message?: string };
  detail?: { code?: string; message?: string } | string;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  idempotencyKey?: string;
  /** 乐观锁 If-Match（09 §42.4/§45.4：PATCH/DELETE 携带资源版本）。 */
  ifMatch?: string;
  signal?: AbortSignal;
}

/** 写请求体：JSON 对象或 FormData（multipart 上传，P3-E4 resource-uploads）。 */
export type RequestBody = unknown;

export interface PlatformClientConfig {
  fetchImpl?: typeof fetch;
  csrfTokenProvider?: () => string | null;
}

let fetchImpl: typeof fetch = (...args) => globalThis.fetch(...args);
let csrfTokenProvider: () => string | null = () => null;

/** 测试注入点（jsdom 下也可用，默认使用全局 fetch）。 */
export function configurePlatformClient(config: PlatformClientConfig): void {
  if (config.fetchImpl) fetchImpl = config.fetchImpl;
  if (config.csrfTokenProvider) csrfTokenProvider = config.csrfTokenProvider;
}

function newRequestId(): string {
  const cryptoObj = globalThis.crypto as Crypto | undefined;
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
    return cryptoObj.randomUUID();
  }
  return `f${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
}

function emitAuthChallenge(): void {
  globalThis.dispatchEvent(new CustomEvent(AUTH_CHALLENGE_EVENT));
}

function isJson(contentType: string | null): boolean {
  return contentType != null && /application\/json/i.test(contentType);
}

async function parseErrorBody(response: Response, raw: unknown): Promise<PlatformError> {
  const body = (typeof raw === "object" && raw !== null ? raw : {}) as EnvelopeErrorBody;
  const errorEnvelope = body.error;
  const detail = body.detail;
  let code: string | undefined;
  if (typeof errorEnvelope?.code === "string" && errorEnvelope.code) {
    code = errorEnvelope.code;
  } else if (typeof detail === "object" && detail !== null && typeof detail.code === "string") {
    code = detail.code;
  }
  let message = errorEnvelope?.message ?? "";
  if (typeof detail === "string") message = detail;
  if (!code) {
    code = response.status === 401 ? "UNAUTHENTICATED" : response.status === 403 ? "PERMISSION_DENIED" : response.status === 404 ? "NOT_FOUND" : response.status >= 500 ? "INTERNAL" : "UNKNOWN";
  }
  return new PlatformError({
    code,
    status: response.status,
    message: message || undefined,
    requestId: response.headers.get(REQUEST_ID_HEADER),
    retryAfterSeconds: parseRetryAfter(response.headers.get("retry-after")),
  });
}

/** Retry-After：仅接受秒数形态（03 §8.3 限流冷却；日期形态按无处理）。 */
function parseRetryAfter(raw: string | null): number | null {
  if (raw == null || raw.trim().length === 0) return null;
  if (!/^\d+$/.test(raw.trim())) return null;
  const seconds = Number.parseInt(raw.trim(), 10);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

/**
 * 统一 fetch + 信封解析（request/requestForm 共用）：
 * 401/403 触发 auth-challenge；非 ok 抛 PlatformError；ok 返回 envelope 的 `result`。
 */
async function fetchEnvelope(path: string, init: RequestInit): Promise<unknown> {
  let response: Response;
  try {
    response = await fetchImpl(path, init);
  } catch (error) {
    throw new PlatformError({
      code: "UNAVAILABLE",
      status: 0,
      message: error instanceof Error ? error.message : "network error",
    });
  }

  let raw: unknown = null;
  try {
    raw = isJson(response.headers.get("content-type")) ? await response.json() : null;
  } catch {
    raw = null;
  }

  if (response.status === 401 || response.status === 403) {
    emitAuthChallenge();
    throw await parseErrorBody(response, raw);
  }

  if (!response.ok) {
    throw await parseErrorBody(response, raw);
  }

  const body = (typeof raw === "object" && raw !== null ? raw : {}) as EnvelopeOk;
  if (body.status === "ok") {
    return body.result;
  }
  throw new PlatformError({
    code: "UNKNOWN",
    status: response.status,
    message: "响应信封格式非法",
    requestId: response.headers.get(REQUEST_ID_HEADER),
  });
}

/**
 * 统一 JSON 请求入口：返回 envelope 的 `result`。
 * 成功：`{status:"ok", result}`；失败：抛 PlatformError（status/code/requestId 稳定）。
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? (options.body === undefined ? "GET" : "POST");
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
  const headers: Record<string, string> = { [REQUEST_ID_HEADER]: newRequestId() };
  if (options.body !== undefined && !isFormData) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    const csrf = csrfTokenProvider();
    if (csrf) headers[CSRF_HEADER] = csrf;
  }
  if (options.idempotencyKey) headers[IDEMPOTENCY_HEADER] = options.idempotencyKey;
  if (options.ifMatch) headers["If-Match"] = options.ifMatch;

  return fetchEnvelope(path, {
    method,
    headers,
    credentials: "include",
    body:
      options.body === undefined
        ? undefined
        : isFormData
          ? (options.body as FormData)
          : JSON.stringify(options.body),
    signal: options.signal,
  }) as Promise<T>;
}

/**
 * multipart/form-data 上传入口（05 §12.5 `me|account resource-uploads`；
 * P3-E4/P3-E5 文件导入复用）。与 `request` 同信封/CSRF/Request-ID/401/403 语义；
 * Content-Type 由浏览器生成（含 multipart boundary），不手动设置。
 */
export async function requestForm<T>(
  path: string,
  form: FormData,
  options: RequestOptions = {},
): Promise<T> {
  const method = options.method ?? "POST";
  const headers: Record<string, string> = { [REQUEST_ID_HEADER]: newRequestId() };
  const csrf = csrfTokenProvider();
  if (csrf) headers[CSRF_HEADER] = csrf;
  if (options.idempotencyKey) headers[IDEMPOTENCY_HEADER] = options.idempotencyKey;

  return fetchEnvelope(path, {
    method,
    headers,
    credentials: "include",
    body: form,
    signal: options.signal,
  }) as Promise<T>;
}

export function requestIdHeaderValue(): string {
  return REQUEST_ID_HEADER;
}

export { isPlatformError };
