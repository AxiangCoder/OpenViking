# OpenViking 产品化平台 — 设计系统总纲

> Design v0.1 · 文档路径：`docs/ovp/DESIGN-SYSTEM.md`
> 受众：产品 / 设计 / 前端 全角色
> 范围：OpenViking 产品化平台 `/login` 与 `/platform/accounts` 设计稿中已用到的元素
> 形式：表格 + 状态示意 + `.pen` 文件 / 节点 ID 引用
> 不含：未使用组件（Pagination、Tooltip、Tabs、Drawer 等后续页面用到再补）
>
> 风格定稿：2026-08-20 选用 **Aceternity 风格**（深色化 + 渐变光斑 + 玻璃感卡片 + 渐变文字），并把界面文案统一改为中文。
> 字体：拉丁字符 Geist / Geist Mono；中文回退到系统字体栈（PingFang SC / Microsoft YaHei / Noto Sans CJK SC）。

---

## §1 概述

### 1.1 文档定位

- **基线版本**：OpenViking v0.4.12，Design v0.1
- **设计参考**：shadcn/ui 浅色 + OpenViking 品牌蓝
- **覆盖页面**：`/login`、`/platform/accounts` 5 帧
- **协作约定**：变量、间距、圆角、字号、组件变体均以本文档为唯一来源；新增组件时先补本文档再设计

### 1.2 设计原则

| # | 原则 | 落地 |
|---|---|---|
| 1 | 一致性优先 | 颜色、间距、圆角一律 8px 基准 + 4 步进 |
| 2 | 中文优先 | 导航、表单、提示、状态文案均为中文 |
| 3 | 状态显式 | 5 种 Account 状态徽标 + 通用 4 色提示，必须可见 |
| 4 | 触控友好 | 关键按钮 ≥ 38px 高，行高 ≥ 36px |
| 5 | 渐进呈现 | 弹窗始终是全屏遮罩 + 居中卡片，不破坏上下文 |

### 1.3 与 OpenViking v0.1 关系

- 本文档不替代 `01-14-*.md` 任何编号设计契约
- 路由、权限、API 边界以 `06-frontend-security-and-operations.md` §13 为准
- 本文档仅约束「视觉与交互」

### 1.4 风格定稿与背景

- 颜色：主色由单一 `#2563eb` 改为 **紫 → 蓝渐变**（`#7c3aed` → `#2563eb`），用于主按钮、标题、强调、链接
- 背景：浅色画板（`#fafafa`）+ 4 个柔光斑（紫 / 蓝 / 青 / 粉），模拟 Aceternity 的 aurora 背景
- 字体：拉丁字符 Geist / Geist Mono；中文回退到系统字体栈
- 卡片：玻璃感（半透明白 + 1px 边框 + 大圆角 12-16px）
- 状态徽标：保留 OpenViking 5 状态色（绿 / 橙 / 红 / 灰），但加 1px 同色描边让徽标"发光"
- 表格：行高 72px，Account Avatar 36×36 圆角 10 + 紫色字母；hover 高亮紫底 8% 透明度
- 详情：见 §12 设计稿索引

---

## §2 品牌

### 2.1 品牌色

| 名称 | HEX | 用途 |
|---|---|---|
| **品牌主色** | `#7c3aed`（紫罗兰） | 主按钮底、链接、强调、Account Avatar 底 |
| 品牌副色 | `#2563eb`（蓝） | 渐变终点、focus ring、信息色 |
| 品牌渐变 | `#7c3aed` → `#2563eb` | 页面大标题文字、Avatar 渐变、装饰光斑 |
| 辅助色-青 | `#06b6d4` | 装饰光斑 |
| 辅助色-粉 | `#ec4899` | 装饰光斑、Avatar 渐变终点 |
| 主色-深（hover） | `#1d4ed8` | 主按钮 hover |
| 主色-光（背景） | `#eff6ff` | 角色徽标 / 选中态背景 |
| 主色-紫光（背景） | `#7c3aed1a` (10% alpha) | 紫色 hover 底、激活态背景 |
| 主色-环（focus） | `#93c5fd` | 输入框 / 按钮 focus ring |
| 主色-淡（装饰） | `#dbeafe` | 登录页背景装饰椭圆 |

> 说明：v0.1 之前用单一 `#2563eb` 作为主色。2026-08-20 调整为以紫色 `#7c3aed` 为主，蓝色降为副色，两者组合成渐变，与 Aceternity 风格一致。

### 2.2 品牌标识

- 形态：文字 logo「OpenViking」+ 副标题「Platform · 记忆与上下文管理平台」
- 字号：28px / 700（logo），14px / 400（副标题）
- 不使用图标徽章（v0.1 范围内不设计字母徽章）
- 引用：`login.pen` → `n14` 品牌区

---

## §3 颜色系统

### 3.1 shadcn 变量表（Light 主题）

| 变量 | HEX | 语义 |
|---|---|---|
| `--primary` | `#7c3aed` | 主色（紫罗兰，2026-08-20 调整） |
| `--primary-foreground` | `#ffffff` | 主色按钮文字 |
| `--secondary-primary` | `#2563eb` | 副色（蓝，与 primary 组成渐变） |
| `--ring` | `#93c5fd` | focus 环 |
| `--background` | `#fafafa` | 全局背景（与 `--sidebar` 同步） |
| `--foreground` | `#0a0a0a` | 主文字 |
| `--card` | `#fafafa` | 卡片底（与 background 同） |
| `--card-foreground` | `#0a0a0a` | 卡片文字 |
| `--popover` | `#fafafa` | 浮层底 |
| `--popover-foreground` | `#0a0a0a` | 浮层文字 |
| `--secondary` | `#f5f5f5` | 次级表面 |
| `--secondary-foreground` | `#171717` | 次级文字 |
| `--muted` | `#f5f5f5` | 弱化背景 |
| `--muted-foreground` | `#737373` | 弱化文字 |
| `--accent` | `#f5f5f5` | 强调背景 |
| `--accent-foreground` | `#171717` | 强调文字 |
| `--accent-violet` | `#7c3aed1a` | 紫色激活态 / hover 背景（10% alpha） |
| `--accent-violet-border` | `#7c3aed40` | 紫色描边（25% alpha） |
| `--destructive` | `#e7000b` | 危险 |
| `--border` | `#e5e5e5` | 默认边框 |
| `--input` | `#e5e5e5` | 输入框边框 |
| `--sidebar` | `#ffffffe5` | 侧边栏底（90% alpha 玻璃感） |
| `--sidebar-foreground` | `#09090b` | 侧边栏文字 |
| `--sidebar-accent` | `#f4f4f4` | 侧边栏弱化 |
| `--sidebar-accent-foreground` | `#18181b` | 侧边栏弱化文字 |
| `--sidebar-border` | `#e4e4e7` | 侧边栏边框 |
| `--sidebar-primary` | `#7c3aed` | 侧边栏选中底（=主色紫） |
| `--sidebar-primary-foreground` | `#fafafa` | 侧边栏选中文字 |
| `--sidebar-ring` | `#71717a` | 侧边栏 focus |
| `--white` | `#ffffff` | 纯白 |
| `--black` | `#000000` | 纯黑 |

### 3.2 语义映射

| 用途 | 变量 / HEX |
|---|---|
| 画板背景 | `#fafafa` + 4 个柔光斑（紫 / 蓝 / 青 / 粉，opacity 0.35-0.5） |
| 卡片 / 对话框底 | `#ffffffd9`（85% alpha 玻璃感） |
| 表头底 | `#fafafacc`（80% alpha） |
| 输入框底 | `#ffffff` |
| 按钮次级底 | `#ffffff` |
| 主文字 | `#0a0a0a` |
| 次文字 | `#334155` / `#475569` |
| 弱文字 | `#64748b` / `#a1a1aa` |
| 链接 / 操作 | `#7c3aed` |
| 默认边框 | `#e4e4e7` |
| 输入框边框 | `#d0d5dd` |
| 渐变文字 | `#7c3aed` → `#2563eb`（紫→蓝 90°） |
| Account Avatar 底 | `#7c3aed` 纯色，或 `#7c3aed` → `#ec4899` 渐变（45°） |

> **说明**：v0.1 Aceternity 风格下画板采用浅色 `#fafafa` + 4 个柔光斑装饰，与上一版 `#f7f8fa` 纯色画板不同。卡片、表头、对话框统一加 alpha 让光斑透出，形成玻璃感。

### 3.3 状态色（参考 §9 详细规范）

| 状态 | 背景 | 圆点 | 文字 |
|---|---|---|---|
| 成功 / 正常 | `#dcfce7` | `#16a34a` | `#15803d` |
| 提示 / 等待 | `#fef3c7` | `#f59e0b` / `#d97706` | `#92400e` / `#b45309` |
| 危险 / 失败 | `#fee2e2` / `#fef2f2` | `#dc2626` | `#b91c1c` |
| 中性 / 已禁用 | `#f1f5f9` | `#94a3b8` | `#64748b` |
| 信息 | `#eff6ff` | `#2563eb` | `#1d4ed8` |

---

## §4 字体系统

### 4.1 字体族

Aceternity 风格下拉丁字符使用 Geist / Geist Mono（与 shadcn new-york 风格一致），中文回退到系统字体栈：

```css
font-family: "Geist", -apple-system, BlinkMacSystemFont, "Segoe UI",
             "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
             "Noto Sans CJK SC", sans-serif;

font-family-mono: "Geist Mono", "JetBrains Mono", "SF Mono", Menlo, Consolas,
                  "Microsoft YaHei", "Noto Sans CJK SC", monospace;
```

- 拉丁字符：`Geist` / `Geist Mono`（Vercel 开源，Pencil 直接可用）
- 中文：`PingFang SC`（macOS） → `Hiragino Sans GB` → `Microsoft YaHei` → `Noto Sans CJK SC`
- 数字、code、ID：`Geist Mono`
- 不引入 Web 字体（Geist 通过系统 / 浏览器内置字体回落加载）

### 4.2 字号阶梯

| 级别 | 字号 px | 用途 | 引用 |
|---|---|---|---|
| micro | 11 | ID / 次级标签 / 副文本 | `n91` acct_xxx |
| caption | 12 | 表格表头 / 状态徽标 / hint | `n84`、`n95` |
| body-sm | 13 | 导航 / 表单标签 / 表格行 / hint | `n37`、`n75` |
| body | 14 | 正文 / 副标题 / 输入框 | `n82`、`n71` |
| body-lg | 16 | 品牌名（侧边栏 16px） | `n37` |
| subtitle | 17 | 对话框标题 / 卡片标题 | `n159`、`n193` |
| h6 | 18 | 空状态标题 | `n269` |
| h5 | 20 | 一次性密码 / 错误态标题 | `n222`、`n19` |
| h4 | 22 | — | （未用） |
| h3 | 24 | 页面大标题「Accounts」 | `n70` |
| display | 28 | 登录品牌字 | `n14` |

### 4.3 字重

| 字重 | 数值 | 用途 |
|---|---|---|
| Regular | 400 | 正文 / hint / 输入框占位 |
| Medium | 500 | 标签 / 导航 / 副文本 |
| Semibold | 600 | 标题 / 按钮文字 / 状态文字 |
| Bold | 700 | 品牌名 / 页面大标题 / 一次性密码值 |

> **说明**：shadcn 规范中 300 / 800 / 900 不在 v0.1 设计稿使用，全部省略。

### 4.4 行高

- 默认 1.5（CSS 行高继承）
- 表头 / 状态徽标文字：1.4
- 多行 hint：1.6

---

## §5 间距与圆角

### 5.1 间距基准

栅格 = 8px，步进：4 / 8 / 12 / 16 / 20 / 24 / 40

| 步进 | 像素 | 用途 |
|---|---|---|
| 4 | 4px | 标签-输入框 内间距 |
| 8 | 8px | 文本节点 padding、徽标内间距 |
| 12 | 12px | 导航项内边距、按钮内边距 |
| 16 | 16px | 卡片内边距、字段间距、对话框头/底 |
| 20 | 20px | 页面边距、侧边栏内边距、对话框边距 |
| 24 | 24px | 内容区 padding、页面头 padding |
| 40 | 40px | 登录卡片内边距、对话框大内边距 |
| 60 | 60px | （备用） |

### 5.2 圆角阶梯

| 圆角 | 像素 | 用途 |
|---|---|---|
| sm | 6 | 副按钮、输入框、筛选下拉 |
| md | 8 | 主按钮、提示条、密码卡、影响预览卡 |
| lg | 10 | 筛选栏、表格卡 |
| xl | 12 | 对话框 |
| 2xl | 16 | 登录大卡片 |
| 3xl | 20 | 空状态图标背景 |
| full | 999 | 角色徽标、状态徽标 |

### 5.3 内容元素间距（内组件 Micro-spacing）

§5.1 的 8px 栅格控制**组件外间距**（容器与容器、容器与边），本节约束**组件内**的"图标+文字""label+输入框""按钮组"等微场景的固定间距。

| 场景 | 间距 | 说明 | 引用 |
|---|---|---|---|
| **图标 / 圆点 ↔ 文字**（水平） | `8` | notice 条圆点 + 文案、状态徽标圆点 + 文案、影响预览行图标 + 文案 | `n17` `n93` `n199` |
| **按钮 ↔ 按钮**（水平） | `12` | 对话框底部「取消/创建」「取消/确认删除」、按钮组 | `n184` `n207` |
| **链接 ↔ 链接**（水平） | `12` | 表格行操作列「查看/删除」 | `n98` `n111` |
| **label ↔ 输入框**（垂直） | `6` | 表单字段 frame 内部 vertical gap | `n163` `n167` 等 |
| **字段 ↔ 字段**（垂直） | `16` | 表单字段之间（dlg1Body vertical gap） | `n161` |
| **section 标题 ↔ 字段**（垂直） | `16` | 「Account 信息」「首位 Account Admin」分区标题与第一个字段 | `n161` `n162` `n171` |
| **错误 / hint ↔ 输入框**（垂直） | `4` | 输入框下方红字 hint（不抢主内容） | login `n26` `n27` |
| **按钮 ↔ 段落**（垂直） | `16` | 字段与底部按钮区距离 | `n161` |
| **icon ↔ 文字**（垂直，empty 状态） | `20` | 空状态图标与标题 | `n266` |
| **标题 ↔ 副标题**（垂直，empty 状态） | `6` | 空状态标题与说明 | `n269` `n270` |
| **副标题 ↔ CTA**（垂直，empty 状态） | `20` | 空状态说明与按钮 | `n270` `n271` |
| **左 icon ↔ 卡片内文字**（水平） | `10` | 影响预览行的左侧引导 | `n198` `n201` `n204` |
| **状态徽标内 圆点 ↔ 文案**（水平） | `6` | 胶囊内部 6px 圆点 + 12px 文字 | `n93` `n106` 等 |
| **角色徽标内 圆点 ↔ 文案**（水平） | `6` | 胶囊内部 6px 圆点 + 12px 文字 | `n60` |

#### 5.3.1 文本内字符间距（不可见间距）

| 场景 | 规范 | 例子 |
|---|---|---|
| **数字 ↔ 单位** | 半角空格 1 | `12 个用户`、`8 个 Resource · 3 个 Skill` |
| **必填星号** | 紧贴 label 文字（半角空格 0），整体后置 1 空格与右侧控件对齐 | `Account 名称 *` |
| **路径分隔** | 前缀斜杠，section 名首字母大写 | `/platform` |
| **多选分隔** | 中点 `·` 前后各 1 半角空格 | `Resource · Skill` |
| **括号注释** | 全角括号 `（）` 紧贴前文 | `Account code（唯一标识）` |
| **邮箱 / 域名** | 全小写 | `admin@example.com` |
| **范围区间** | 全角波浪 `～` | `2026-08-10 09:20`（24h 时间，分隔用半角） |

#### 5.3.2 必填字段星号规范

- 必填星号 `*` 颜色与 label 主文字同色（`#0a0a0a`），不单独用红色（避免与错误态混淆）
- 星号紧贴 label 文字，**不留空格**（空格 1 = label 与右侧控件间距）
- 可选字段在 placeholder 提示「（可选）」，label 不加 *（例：`管理员显示名（可选）`）
- 4 类必填：name / code / email / username；可选：display_name

#### 5.3.3 链接与按钮水平间距速记

```text
按钮组（对话框底）：       取消(8)          创建(12)
                          取消(8)          确认删除(12)

操作列（表格行）：        查看(12)          删除(12)
                          查看(12)  重试(12)  删除(12)
```

#### 5.3.4 排版密度

- **紧凑**（表格行 / 列表项）：行高 60-72，垂直 gap 0-8
- **标准**（表单 / 卡片）：字段 gap 16，section gap 24
- **宽松**（登录 / 一次性密码）：垂直 gap 20-40，section 间距 60+

### 5.4 AppShell 布局（2026-08-20 增）

AppShell Template 是 `/app`、`/admin`、`/platform` 三个区域共用的唯一外壳，固定为 1920×1080 画布。结构按**左 / 右**划分：

```
┌────────────────┬──────────────────────────────────┐
│                │  Header (毛玻璃)              │ ← top-right
│   Sider        ├──────────────────────────────────┤
│   (毛玻璃)     │                                  │
│   左 240px     │  Main (毛玻璃)                 │ ← bottom-right
│                │                                  │
│                │                                  │
└────────────────┴──────────────────────────────────┘
Aurora Background（4 柔光斑）垫在最底层
```

| 区块 | x | y | width | height | 圆角 | 填充 | 备注 |
|---|---|---|---|---|---|---|---|
| AppShell Template | 0 | 0 | 1920 | 1080 | — | `#fafafa` | `layout:none`，子锚点定位 |
| Aurora Background | 0 | 0 | 1920 | 1080 | — | `#fafafa` + 4 柔光斑 | 紫/蓝/青/粉，opacity 0.30-0.40 |
| Sider Instance | 0 | 0 | 240 | 1080 | **2xl (16)** | `#ffffffd9` | ref → `z7EPl`，Footer User 用 Spacer 落底 |
| Right Column | **264** | 0 | **1656** | 1080 | — | — | layout:vertical，**gap:24** |
| Header Instance | 0 | 0 | 1656 | 56 | **2xl (16)** | `#ffffffd9` | ref → `ELalX` |
| Main | 0 | 80 | 1656 | 1000 | **2xl (16)** | `#ffffffd9` | padding [24,32], gap 24 |

**关键约束**：

1. **Sider / Right Column 间距 = 24px**（§5.1 内容区 padding 步进），让 Aurora 光斑从间隙透出，强化玻璃感
2. **Header / Main 间距 = 24px**（Right Column 的 `gap:24` 同步落地），Header 在 `y:0, h:56`，Main 在 `y:80, h:1000`，中间 24px 让 Header 底边阴影与 Main 上沿毛玻璃视觉解耦
3. **Sider 圆角 2xl = 16**：与 §5.2 表中「2xl 16 登录大卡片」一致；Header / Main 同样 16，三块玻璃组件视觉锚定同一圆角语言
4. **Footer User 落底**：Sider 内用 `Spacer (height:"fill_container")` 把 Footer User 推到 240×1080 容器底部，与 nav 项之间留出弹性空间
5. **layout:none 而非 layout:horizontal**：绝对坐标便于像素级精确控制 Aurora 光斑透出的 24px 间隙，flex 布局无法表达

### 5.5 画板与组件实例化约定

- 1920×1080 是桌面 Web 全高清画板；组件库展示页也按此尺寸铺陈
- 每个组件在文档库中是一**独立 frame**（独立节点 ID、名称清晰、可单独选中截图）
- AppShell Template 是**唯一**外壳 frame，不再有 `/app /admin /platform` 三套；三个区域的差异通过 ref descendants override 表达（nav 项名称 / 标题文案 / Footer User 信息）

---

## §6 阴影与边框

### 6.1 边框

| 场景 | 颜色 | 宽度 |
|---|---|---|
| 卡片 / 表格 | `#e3e6ea` | 1px |
| 输入框 / 副按钮 | `#d0d5dd` | 1px |
| 对话框 / 一次性密码卡 | `#e3e6ea` / `#f59e0b`（密码） | 1px |
| 一次性密码卡（强调） | `#f59e0b` | 1px |
| 删除按钮（危险描边） | `#dc2626` | 1px |
| 表行底 | `#eef0f3` | 1px |
| 导航项底（与背景融合） | 无 | 0 |

### 6.2 阴影

v0.1 设计稿**未使用任何阴影**，全靠边框分层。后续如需可补充：

- 卡片悬浮：`0 1px 2px rgba(0,0,0,0.04)`
- 对话框：`0 10px 30px rgba(0,0,0,0.12)`

---

## §7 核心组件

### 7.1 按钮

| 变体 | 底 | 边 | 文字 | 引用 |
|---|---|---|---|---|
| **Primary（主）** | `#2563eb` | 无 | `#ffffff` 600 | `n72`、`n187`、`n230` |
| **Danger（危险）** | `#dc2626` | 无 | `#ffffff` 600 | `n210` |
| **Outline（描边）** | `#ffffff` | `#d0d5dd` 1px | `#334155` 500 | `n185`、`n208` |
| **Ghost（重试开通）** | `#ffffff` | `#dc2626` 1px | `#dc2626` 600 | `n126` |

- 高度：44px（页面主操作）、38px（页面头）、34px（对话框内）
- 圆角：8
- 内边距：水平 12-24
- 禁用态：`opacity 0.45 + cursor not-allowed`
- 加载态：替换文字为「加载中…」+ 禁用

### 7.2 输入框

- 默认：白底 + 1px `#d0d5dd` + 圆角 6-8 + 高度 38-40
- 必填：label 后加 ` *`（4 个字段：名称 / code / 邮箱 / 用户名）
- 错误：红字 hint 行 12px
- 占位：13px 400 `#9ca3af`
- 引用：`n22`（邮箱）、`n25`（密码）、`n165`-`n183`（对话框字段）

### 7.3 选择器（Select）

- 高度 32-40
- 默认值文本 13px 400 `#0a0a0a`
- 箭头 `▾` 12px `#64748b`
- 引用：`n76` 状态筛选下拉

### 7.4 卡片

| 类型 | 背景 | 边框 | 圆角 | 引用 |
|---|---|---|---|---|
| 登录大卡片 | `#ffffff` | `#e3e6ea` 1px | 16 | `n13` |
| 表格卡 | `#ffffff` | `#e3e6ea` 1px | 10 | `n80` |
| 筛选栏 | `#ffffff` | `#e3e6ea` 1px | 10 | `n74` |
| 影响预览卡 | `#f8fafc` | `#e3e6ea` 1px | 8 | `n197` |
| 一次性密码卡 | `#fffbeb` | `#f59e0b` 1px | 8 | `n220` |
| 角色徽标胶囊 | `#eff6ff` | 无 | 999 | `n60`、`n259` |
| 状态徽标胶囊 | 见 §9 | 无 | 999 | `n93`、`n106` 等 |

### 7.5 对话框

| 元素 | 规范 |
|---|---|
| 圆角 | 12 |
| 宽度 | 540（确认）/ 600（创建）/ 640（一次性密码） |
| 头高 | 64，分割线 `#e3e6ea` |
| 底高 | 68，背景 `#f8fafc`，分割线 `#e3e6ea` |
| 遮罩 | `#0f172a` 45% 透明度（覆盖整个画板） |
| 居中 | 垂直水平（顶部偏移 170-250） |
| 引用 | 创建 `n157`、删除 `n191`、密码 `n214` |

### 7.6 状态徽标

| 状态 | 文案 | 背景 | 圆点 | 文字 | 引用 |
|---|---|---|---|---|---|
| 正常 | 正常 | `#dcfce7` | `#16a34a` | `#15803d` 12px 600 | `n93` |
| 开通中 | 开通中 | `#fef3c7` | `#d97706` | `#b45309` 12px 600 | `n106` |
| 开通失败 | 开通失败 | `#fee2e2` | `#dc2626` | `#b91c1c` 12px 600 | `n119` |
| 删除中 | 删除中 | `#f1f5f9` | `#94a3b8` | `#64748b` 12px 600 | `n134` |
| 已暂停 | 已暂停 | `#f1f5f9` | `#94a3b8` | `#64748b` 12px 600 | `n147` |

- 结构：胶囊 + 圆点（6-8px）+ 文字
- 高度：28
- 水平 padding：12
- 圆角：999

### 7.7 提示条（Notice / Banner）

| 类型 | 背景 | 圆点 | 文字 | 圆角 | 引用 |
|---|---|---|---|---|---|
| 会话过期（琥珀） | `#fef3c7` | `#f59e0b` 8px | `#92400e` 12px 400 | 8 | `n16` |
| 一次性警告（红） | `#fef2f2` | `#dc2626` 8px | `#b91c1c` 12px 500 | 8 | `n223` |

- 高度：44-48
- 水平 padding：14
- 圆点 + 文案两段式

### 7.8 一次性密码视图

- 主体：密码值大字号 20px 700 `#78350f`
- 卡片：琥珀底 `#fffbeb` + 琥珀边 `#f59e0b`
- 警告：底部红色提示条（见 §7.7）
- 角色说明：12px 400 `#64748b`
- 复制 / 完成按钮：描边 + 主色
- 引用：`n214`-`n231`

### 7.9 表格

| 元素 | 规范 |
|---|---|
| 表头底 | `#f8fafc` |
| 表头高 | 44 |
| 表头文字 | 12px 600 `#475569` |
| 行高 | 60 |
| 行底色 | `#ffffff` |
| 行分割线 | `#eef0f3` 1px |
| 名称列 | 主名 14px 500 + ID 11px 400 `#9ca3af` |
| 状态列 | 状态徽标（见 §7.6） |
| 成员数列 | `12` 或 `—`（数据缺失） 13px 400 `#334155` |
| 创建时间列 | `2026-08-10 09:20` 13px 400 `#64748b` |
| 操作列 | 链接式（查看 / 删除）12-13px 500 |

- 引用：表头 `n81`、行 1-5 `n88`/`n101`/`n114`/`n129`/`n142`

### 7.10 列表项 / 导航项

- 高度 40，水平 padding 12，水平 gap 10
- 默认：透明底 + lucide 图标 18px `#71717a` + 文字 14px 500 `#52525b`
- 选中：底 `#7c3aed1a`（10% alpha 紫）+ 左侧 3px 紫色高亮条（圆角 2，height 20）+ 图标 `#7c3aed` + 文字 14px 600 `#7c3aed`
- 圆角 10
- 引用：`vUbq7` 选中态、`g1t8L` / `TgyeG` / `gRE2B` 默认态

> 2026-08-20 调整：从 v0.1 之前的「圆点 + 文字」改为「图标 + 文字」，与 Aceternity 风格一致；高度从 36 提到 40（容纳 lucide 18px 图标）。

### 7.11 角色徽标

- 形状：胶囊（圆点 6 + 文字 + 圆角 999）
- 底 `#7c3aed1a`（10% alpha 紫）、描边 `#7c3aed40`（25% alpha 紫）、圆点 `#7c3aed`、文字 12px 600 `#7c3aed`
- 高度 24-26
- 引用：`n60`、`n259`（v0.1 之前）/ `BC8uT`（Aceternity 帧）

> 2026-08-20 调整：从蓝底 `#eff6ff` / 蓝点 `#2563eb` 改为紫底 `#7c3aed1a` / 紫点 `#7c3aed`，与主色紫对齐。

### 7.12 空状态

- 居中布局：图标 + 标题 + 副标题 + CTA
- 图标：72×72 圆角 20 胶囊 + 居中 `+` 48px `#2563eb`
- 标题：18px 600 `#0a0a0a`
- 副标题：14px 400 `#64748b`
- CTA：与主按钮一致
- 引用：`n266`-`n272`

### 7.13 加载态

- 文字「加载中…」13px 400 `#64748b`
- 错误态：红色文字 12px 400 `#dc2626`
- 引用：login `n26` 错误、平台列表「加载中…」hint

---

## §8 导航与布局

### 8.1 AppShell（三层结构）

| 层 | 名称 | 尺寸 | 引用 |
|---|---|---|---|
| 1 | Sidebar | 240 × 全高 | `n34`、`n233` |
| 2 | Topbar | 全宽 - 240 × 56 | `n57`、`n256` |
| 3 | Content | 1680 × 1024 | `n67`、`n266` |

### 8.2 Sidebar

- 品牌区：高度 64，水平 padding 20，图标 28 + 文字 OpenViking 16px 700
- 导航区：垂直 padding 14，水平 padding 10
- 分区标签：11px 600 `#9ca3af`（如 `PLATFORM`）
- 导航项：见 §7.10
- 底部版本：v0.1.0，padding 20，文字 12px 400 `#9ca3af`
- 引用：`n34`-`n56`、`n233`-`n255`

### 8.3 Topbar

- 高度 56，水平 padding 24，分割线 `#e3e6ea` 1px
- 左侧：区名（`/platform` 14px 600）
- 右侧：角色徽标 + 头像 + 用户名/邮箱（垂直布局）
- 引用：`n57`-`n66`、`n256`-`n265`

### 8.4 页面头

- 高度 72，水平 space-between
- 左侧：标题 24px 700 + 副说明 13px 400 `#64748b`
- 右侧：主操作按钮（高度 38）
- 引用：`n68`-`n73`

### 8.5 筛选栏

- 高度 44，圆角 10，水平 padding 16
- 「状态筛选」label + 下拉 + hint 文字
- 引用：`n74`-`n79`

---

## §9 状态色规范

### 9.1 Account 5 状态（详见 §7.6）

| 状态枚举 | 标签 | 主色 | 引用节点 |
|---|---|---|---|
| `active` | 正常 | 绿 | `n93` |
| `provisioning` | 开通中 | 琥珀 | `n106` |
| `failed` | 开通失败 | 红 | `n119` |
| `pending_deletion` | 删除中 | 灰 | `n134` |
| `suspended` | 已暂停 | 灰 | `n147` |

### 9.2 通用 4 色提示

| 语义 | 背景 | 文字 | 用途 |
|---|---|---|---|
| success | `#dcfce7` | `#15803d` | 状态徽标 / 成功提示 |
| warning | `#fef3c7` | `#92400e` / `#b45309` | notice 条 / 等待态 |
| danger | `#fee2e2` / `#fef2f2` | `#b91c1c` / `#dc2626` | 错误 / 失败 / 警告 |
| info | `#eff6ff` | `#1d4ed8` | 角色徽标 / 信息 |
| neutral | `#f1f5f9` | `#64748b` | 中性 / 已禁用 / 已暂停 |

### 9.3 表格行状态

- 普通行：`#ffffff` 底 + `#eef0f3` 行分割线
- 失败行：状态徽标 + 额外「重试开通」描边按钮（仅 failed 出现）
- 删除中行：状态徽标 + 删除链接仍可见（回收站可恢复）

---

## §10 文案规范

### 10.1 语言

- **界面文案**：中文为主
- **专有名词**：保留英文（Account、code、Resource、Skill、Session、Activity、Platform Super Admin、v0.1.0 等）
- **时间格式**：`YYYY-MM-DD HH:MM` 24 小时制（如 `2026-08-10 09:20`）

### 10.2 术语对照

| 中文 | 英文 / 字段 | 用途 |
|---|---|---|
| 正常 | active | 状态 |
| 开通中 | provisioning | 状态 |
| 开通失败 | failed | 状态 |
| 删除中 | pending_deletion | 状态 |
| 已暂停 | suspended | 状态 |
| 创建 | Create | 操作 |
| 取消 | Cancel | 操作 |
| 确认删除 | Confirm Delete | 危险操作 |
| 重试开通 | Retry Provisioning | 失败行操作 |
| 复制 | Copy | 一次性密码 |
| 我已保存 | I've Saved | 一次性密码 |
| 创建第一个 Account 以开始使用 OpenViking 平台 | — | 空状态副标题 |
| 以 Platform Super Admin 身份管理平台；选择目标 Account 是管理浏览，不改变登录者身份 | — | 页面说明 |

### 10.3 错误提示

- 单行 12px 400 `#dc2626`
- 行内 hint，不抢主内容
- 引用：login `n26`「邮箱或密码不正确」

---

## §11 暗色模式

- v0.1 **仅浅色**，设计稿全部基于 Light 主题
- shadcn Mode 主题轴已就位（`Dark`），变量已配置 Dark 变体
- 后续如启用暗色，遵循以下顺序：
  1. 在 `apply_design_system` 时切换 `Mode` 轴到 `Dark`
  2. 校验所有组件对比度
  3. 补完阴影（暗色下边框不够分明，需用阴影）
  4. 导出 6 帧的 Dark 变体

---

## §12 设计稿索引

### 12.1 .pen 文件

| 文件 | 内容 | 帧数 | 尺寸 |
|---|---|---|---|
| `ovp-v0.1.pen` | OVP v0.1 组件库（Aurora 背景 + Layer 2 容器与布局 + Layer 3 AppShell 三层） | 多帧（独立 frame 分层） | 1920×1080 |

> 注：旧版 `login.op` / `platform-accounts.op`（OpenPencil 旧格式）于 2026-08-20 切换到 `.pen` 格式并收敛到 Aceternity 风格，随后组件库进一步收敛为单一 `ovp-v0.1.pen`（mega 文件，每组件独立 top-level frame）。下述 §12.2–§12.4 的历史节点 ID 引用旧文件，仅作追溯参考。

### 12.2 帧清单

| 帧 | 文件 | 节点 ID | 状态 | 节点名 |
|---|---|---|---|---|
| Aurora 背景 | `ovp-v0.1.pen` | `lyloZ` | ✅ | 00 · Aurora Background Demo |
| Glass Card | `ovp-v0.1.pen` | `XqIgA` | ✅ | L2-01 · Glass Card |
| Page Header | `ovp-v0.1.pen` | `a5wyb` | ✅ | L2-02 · Page Header |
| Filter Bar | `ovp-v0.1.pen` | `dS5rq` | ✅ | L2-03 · Filter Bar |
| Empty State | `ovp-v0.1.pen` | `dgsNg` | ✅ | L2-04 · Empty State |
| Loading State | `ovp-v0.1.pen` | `I6yCk` | ✅ | L2-05 · Loading State |
| Header | `ovp-v0.1.pen` | `ELalX` | ✅ | Header（reusable） |
| Sider | `ovp-v0.1.pen` | `z7EPl` | ✅ | Sider（reusable） |
| AppShell Template | `ovp-v0.1.pen` | `us5di` | ✅ | AppShell Template（唯一外壳） |

### 12.3 组件引用索引（Aceternity 帧）

| 组件 | 节点 ID 范围 |
|---|---|
| 主按钮（紫色玻璃） | `QuFcM` |
| 副按钮（紫色描边 View） | `rV8RL` / `UDcMr` / `Va79v` |
| 危险按钮（红色描边 Retry） | `ufvZS` |
| More 按钮 | `aTnDc` / `pGj9G` / `NoDq6` / `FBb5U` / `Kqd2c` |
| 搜索框 | `T5Gts1` |
| 状态徽标（5 种） | `YU6qq` / `Bwbbr` / `Oafq0` / `jxPXu` / `KW7LC` |
| 角色徽标（紫色） | `RC8uT` |
| Account Avatar | `QPNCg` / `Q2O0B` / `nkkqk` / `j3QwR` / `w1ptoo` |
| Code 胶囊 | `o3PpX` / `z2bDi` / `JBAv0` / `Sswzc` / `KgfnX` |
| 表格卡（玻璃感） | `S8Qw4a` |
| 筛选栏 | `m2bwh` |
| 装饰光斑（紫 / 蓝 / 青 / 粉） | `RO1iZ` / `ZTPju` / `P4U64` / `izxR4` |
| 登录卡片（保留） | `ncC3W`（login.pen） |

### 12.4 备份 / 对照组

| 帧 | 节点 ID | 状态 | 用途 |
|---|---|---|---|
| OVP / platform-accounts / A · shadcn Default | `PYiia` | 已隐藏（`enabled: false`） | 仅留作风格对照，不导出 |
| OVP / platform-accounts / B · shadcn New York | `EUX8k` | 已隐藏（`enabled: false`） | 仅留作风格对照，不导出 |

---

## §13 变更记录

| 日期 | 版本 | 修订 | 提交 |
|---|---|---|---|
| 2026-08-19 | v0.1.0 | 初版：基于 login.op + platform-accounts.op 6 帧归纳 | — |
| 2026-08-19 | v0.1.1 | 补 §5.3 内容元素间距（图标+文字 8、按钮+按钮 12、label↔输入框 6、空状态 icon↔标题 20 等 14 项微场景） | — |
| 2026-08-19 | v0.1.2 | 应用规范修复两个页面：login n15/n18/n27/n30 字号与错误位置、n12 装饰椭圆透明度；accounts n266 空状态间距（用 8px spacer frame 插入 icon↔title / subtitle↔CTA 间隙）、n71 副描述 13→14 | — |
| 2026-08-20 | v0.1.3 | 风格定稿为 Aceternity（深色化 + 紫→蓝渐变 + 4 柔光斑 + 玻璃感卡片 + 渐变文字）；主色从 `#2563eb` 改为 `#7c3aed`；字体栈前缀 Geist / Geist Mono；Sidebar 形态从「圆点+文字」改为「图标+文字」；角色徽标改紫色；文件命名从 `.op` 改为 `.pen`；删除 4 个变体对话框帧；界面文案统一改为中文（DESIGN-SYSTEM §10.1）；同步 docs/ovp/README.md §12 与 v0.1/README.md 与 06 §13.5 与 13 §89.2。 | — |

---

## 附录 A：OpenPencil Token 速查

OpenPencil 中 `$--xxx` 变量引用映射：

```text
$--primary            → #2563eb   (主色)
$--primary-foreground → #ffffff   (主按钮文字)
$--ring               → #93c5fd   (focus 环)
$--background         → #fafafa   (全局背景)
$--foreground         → #0a0a0a   (主文字)
$--muted              → #f5f5f5   (弱化背景)
$--muted-foreground   → #737373   (弱化文字)
$--border             → #e5e5e5   (默认边框)
$--input              → #e5e5e5   (输入框边框)
$--destructive        → #e7000b   (危险)
$--sidebar            → #fafafa
$--sidebar-primary    → #2563eb   (侧边栏选中)
```

## 附录 B：与现有 web-platform 样式表对照

OpenViking 产品化平台 `web-platform/src/styles.css` 的 token（v0.1 之前）：

```css
--ov-bg:            #f7f8fa  →  画板背景
--ov-surface:       #ffffff  →  卡片 / 对话框底
--ov-border:        #e3e6ea  →  默认边框
--ov-text:          #1f2329  →  主文字（≈ #0a0a0a）
--ov-text-secondary: #646a73 →  次文字（≈ #64748b）
--ov-primary:       #2563eb  →  主色（已对齐 shadcn）
--ov-primary-hover: #1d4ed8  →  主色 hover
--ov-danger:        #dc2626  →  危险
--ov-radius:        8px      →  默认圆角
--ov-sidebar-width: 232px    →  侧边栏宽（设计稿改为 240）
--ov-font:          系统字体  →  与 shadcn 一致
```

**迁移建议**：在 web-platform 中将 `--ov-*` 变量映射到 shadcn `$--*` 变量，保留 `ov-bg`、`ov-sidebar-width` 等产品专用差异。
