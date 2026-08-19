# docs/design — OpenViking 上游技术设计

> 本目录内容来自 [OpenViking](https://github.com/volcengine/OpenViking) 上游主仓，与产品代码同步发布。
>
> 本仓库的二次开发设计（包括 OVP 产品化平台）位于 [`docs/ovp/`](../ovp/README.md)，与本目录并列。

## 目录说明

| 子路径 | 说明 |
|---|---|
| `*.md` | 单文件技术设计文档（如 `memory-link-design.md`、`metric-design.md`、`mcp-oauth2-1.md` 等） |
| `assets/` | 设计图中立目录，存放 SVG / PNG 资源 |

## 同步策略

1. **来源**：上游 OpenViking 主仓 `docs/design/` 目录
2. **同步方式**：每次 OpenViking release 后手动 `git pull` 同步；不接受本地修改
3. **本地修改原则**：本目录下任何 `.md` 的修改都应被拒绝（需提交到上游 PR）；如有分歧，在 `docs/ovp/` 派生文档中说明

## 派生文档入口

如有二开相关的设计、契约、计划、报告，请到 [`docs/ovp/`](../ovp/) 查看，包括：

- [`docs/ovp/DESIGN-SYSTEM.md`](../ovp/DESIGN-SYSTEM.md) — UI 设计系统
- [`docs/ovp/v0.1/README.md`](../ovp/v0.1/README.md) — OVP Design v0.1 总览
- `docs/ovp/v0.1/01-14-*.md` — OVP 14 份产品设计契约
- `docs/ovp/v0.1/p5-e*.md` — Phase 5 部署与生产加固报告

## 旧索引文档

`openviking-product-platform-iam-rbac-design.zh-CN.md` 是 OVP 总设计文档的旧索引入口，链接已重定向到 `docs/ovp/`。保留此文件仅用于外部引用兼容。