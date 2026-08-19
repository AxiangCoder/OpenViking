# OpenViking 产品化平台设计文档（OVP）

本目录保存基于 OpenViking 二次开发的产品化平台设计，与上游 [`docs/design/`](../design/README.md) 并列、互不污染。

| 设计版本 | 上游源码基线 | 状态 | 入口 |
| --- | --- | --- | --- |
| Design v0.1 | OpenViking v0.4.12 / `c1d38eb47ff2ebf9ff4cee46756728893fd8caf3` | 讨论中（曾冻结于 `design-v0.1.0`，2026-08-18 解除） | [v0.1](v0.1/README.md) |

## 顶层文档

- [DESIGN-SYSTEM.md](DESIGN-SYSTEM.md) — UI 设计系统总纲（颜色变量、字体阶梯、间距圆角、14 类核心组件规范、状态色、5 状态徽标、对话框、表格、空状态等）

## 版本目录结构（以 v0.1 为例）

```text
v0.1/
├── 01-14-*.md        # 14 份产品契约（产品定位/架构/认证/数据/后端/前端/质量/能力归属/资源/Skill/Memory+Search+Session/设计评审/管理+个人设置/开发计划）
├── README.md          # 版本总览与变更记录
├── p5-e1-ac-report.md # Phase 5 部署单元报告
├── p5-e2-ac-report.md
├── p5-e2-init-runbook.md
├── p5-e3-ac-report.md
├── p5-e3-backup-tooling.md
├── p5-e3-drill-record.md
├── p5-e3-runbook.md
├── p5-e4-ac-report.md
└── p5-e4-go-no-go.md
```

## 版本号含义

- `OpenViking v0.4.12`：上游源码基线。
- `Design v0.1`：当前设计修订版本。
- 产品发布版本将在设计冻结和实现完成后单独定义。

新增设计版本时创建新的版本目录，不覆盖已冻结版本；跨版本共识和变更原因记录在各版本索引中。
