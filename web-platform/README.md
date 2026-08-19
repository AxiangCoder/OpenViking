# web-platform

OpenViking 产品化平台前端（设计 06 号文档 §13，P3-E1 交付基线）。

## 技术栈

Vite + React 19 + TypeScript + TanStack Router（代码式路由树）+ TanStack Query（06 §13.1）。

## 运行

```bash
npm install
npm run dev        # http://localhost:18082，/api 代理到 http://127.0.0.1:18080
npm run build      # tsc -b && vite build（必须通过）
npm test           # vitest（51 项，见 src/**/*.test.*）
npm run typecheck  # tsc -b
```

后端未运行时 `/api/*` 代理返回 502/500 属正常；启动后应用首屏调用 `/auth/me`（AC①）。

## 目录结构（06 §13.1 落地）

```text
src/
  routes/            # 路由树全量展开（06 §13.2；业务页为占位，见 P3-E3~E5、P4-E1~E4）
    login/ app/ admin/ platform/ oauth/ not-found.tsx
  components/
    layouts/         # /app、/admin、/platform 三布局 + 侧边栏（依权限过滤，AC②）
    ui/              # PermissionGate / PermissionButton（按权限隐藏/禁用）
    ErrorPage.tsx    # 稳定错误码 + Request ID（05 §12.2，AC⑤）
    PlaceholderPage.tsx
  features/auth/     # auth 状态层：启动调 /auth/me、401/403 刷新、会话过期回登录
    auth-state.ts    # useSyncExternalStore store + CSRF Token 内存持有（AC③④）
    guards.ts        # 路由 Guard 骨架（AC①；/admin 仅 Account Admin，/platform 仅 PSA）
    useAuth.ts
  lib/
    platform-client/ # Cookie+CSRF、{status,result,error} envelope、稳定错误码、Request ID
    permissions.ts   # 权限目录（与后端 iam/permissions.py 对齐，冻结点）
    links.ts         # 跨页面跳转契约（产品 ID 参数，AC⑥，冻结点）
    redirect.ts      # 登录回跳净化（OAuth 仅回跳同源授权路由，AC①）
    storage.ts       # 本地存储规则（06 §13.5，AC④）
  styles.css         # UI 基线（P3-E1 冻结）
```

## 冻结点（14 号计划 §98.1 / §95）

`lib/`、路由树、跳转契约（`lib/links.ts`）、`permissions.ts` 交付后冻结，
后续 Epic 只增量扩展，禁止重构。前端权限不是安全边界（06 §13.4），后端每次请求仍重复鉴权。

## 关键契约

- 错误信封：成功 `{status:"ok", result}`；失败 `{status:"error", error:{code,message}}`
  或 FastAPI 默认 `{detail:{code}}`（client 两种都解析为 `PlatformError`）。
- 每个请求携带 `X-Request-ID`，错误页展示服务端回显的 Request ID（AC⑤）。
- 写请求携带内存态 `X-CSRF-Token`（03 §8.2），支持 `Idempotency-Key`（05 §12.2）。
- 跳转 URL 只接受产品 ID（UUID），URL 无 Viking URI/Account/User ID（AC⑥）。
