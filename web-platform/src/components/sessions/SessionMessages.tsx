/**
 * Session 只读消息历史（11 §70.5，P3-E3 AC⑤）。
 *
 * - 纯只读：无消息输入框/Composer/发送/停止生成/模型选择（AC⑤）；
 * - 渲染 User/Assistant/System 消息与复制文本；消息 DTO 由服务端组装并脱敏
 *   （无 URI/宿主机路径/Secret/底层 JSON，Tool 事件不包含原始参数与结果）；
 * - 内容按纯文本展示并保留换行，避免把未经脱敏的结构当富文本渲染。
 */

import type { SessionMessage } from "@/features/sessions/sessions";

const ROLE_LABELS: Record<SessionMessage["role"], string> = {
  user: "用户",
  assistant: "助手",
  system: "系统",
};

function formatMessageTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export default function SessionMessages({ messages }: { messages: SessionMessage[] }) {
  if (messages.length === 0) {
    return (
      <p className="profile-hint" data-testid="messages-empty">
        该 Session 还没有消息。
      </p>
    );
  }
  return (
    <ol className="session-messages" data-testid="session-messages">
      {messages.map((message) => (
        <li key={`${message.sequence}-${message.turn_id ?? ""}`} className="session-message" data-testid={`session-message-${message.sequence}`}>
          <div className="session-message-head">
            <span className={`role-badge ${message.role === "user" ? "role-user" : ""}`}>
              {ROLE_LABELS[message.role] ?? message.role}
            </span>
            <span className="profile-hint">{formatMessageTime(message.created_at)}</span>
          </div>
          <pre className="session-message-content">{message.content}</pre>
        </li>
      ))}
    </ol>
  );
}
