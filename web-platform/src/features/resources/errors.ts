/**
 * Resource 稳定错误码 → 页面文案（09 §46.5，P3-E4）。
 * 不依赖英文 message；未知码回落 fallback。错误响应不含远程正文/
 * 绝对路径/堆栈/Token/底层 URI（09 §46.5，AC⑧）。
 */

import { PlatformError } from "@/lib/platform-client";

export function resourceErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof PlatformError) {
    switch (error.code) {
      case "RESOURCE_NOT_FOUND":
        return "Resource 不存在或已不可见（可能已被删除）。";
      case "RESOURCE_BUSY":
        return "该 Resource 正在进行导入/刷新/同步，请稍后再试。";
      case "RESOURCE_VERSION_CONFLICT":
        return "信息已在其他窗口更新，页面已重新加载，请确认后再次提交。";
      case "RESOURCE_SOURCE_UNSUPPORTED":
        return "该来源类型不受支持。";
      case "RESOURCE_SOURCE_NOT_STABLE":
        return "该来源不稳定（含 Query/登录信息），不能开启自动同步。";
      case "RESOURCE_SOURCE_BLOCKED":
        return "来源地址被安全策略拒绝（禁止内网/本地地址等）。";
      case "RESOURCE_UPLOAD_EXPIRED":
        return "上传已过期（默认 15 分钟），请重新选择文件。";
      case "RESOURCE_UPLOAD_ALREADY_CONSUMED":
        return "该上传已被使用，请重新选择文件。";
      case "RESOURCE_FILE_TOO_LARGE":
        return "文件超过上传大小限制，请检查后重试。";
      case "RESOURCE_FORMAT_UNSUPPORTED":
        return "文件格式不受支持。";
      case "RESOURCE_PARSE_FAILED":
        return "内容解析失败，请检查来源后重试。";
      case "RESOURCE_INDEX_FAILED":
        return "内容索引生成失败，请稍后重试。";
      case "RESOURCE_WATCH_UNAVAILABLE":
        return "当前来源或状态不支持自动同步。";
      case "RESOURCE_WATCH_CONFLICT":
        return "已有同步任务正在执行，请稍后再试。";
      case "RESOURCE_OPERATION_NOT_CANCELLABLE":
        return "该任务当前不可取消。";
      case "RESOURCE_DELETION_PENDING":
        return "该 Resource 已进入删除流程，操作被拒绝。";
      case "RESOURCE_RESTORE_WINDOW_EXPIRED":
        return "恢复窗口（30 天）已过期，该对象无法恢复。";
      case "RESOURCE_PUBLISH_FORBIDDEN":
        return "仅 Account Admin 可以发布自己的私有 Resource 为共享副本。";
      case "PERMISSION_NOT_GRANTED":
      case "PERMISSION_DENIED":
        return "权限不足，操作被拒绝。";
      case "UNAVAILABLE":
        return "网络异常，请检查连接后重试。";
      default:
        break;
    }
  }
  return fallback;
}
