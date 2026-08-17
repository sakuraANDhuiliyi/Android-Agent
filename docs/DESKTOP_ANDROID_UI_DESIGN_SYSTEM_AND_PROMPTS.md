# Android Agent 桌面端与 Android 端完整 UI/UX 设计方案与执行提示词

> 版本：1.0
> 日期：2026-08-15
> 适用范围：`desktop/` Electron + Monaco 桌面端、`android-app/` Kotlin + XML Android 端
> 交付性质：设计基线、交互规格、实现约束、验收标准与可直接执行的提示词
> 注意：本方案基于当前工作区代码和现有截图集编写；工作区存在未提交改动，实施时必须保留用户改动。

---

## 0. 一句话结论

将 Android Agent 设计为一套“安静但高可信”的双端 Agent 控制台：桌面端是以代码、Diff 和任务线程为核心的 Agent Workbench；Android 端是以查看进度、处理审批、改变方向、审阅结果和安装 APK 为核心的 Remote Console。

不建议继续把桌面端做成通用 VS Code 的视觉复刻，也不建议把 Android 端做成缩小版 IDE。双端应共享状态语义、颜色、组件语法和文案，但采用不同的信息架构。

---

## 1. 研究范围与案例结论

### 1.1 本地项目审阅范围

已审阅：

- 产品与功能：`README.md`、`MVP_SPEC.md`、`docs/ARCHITECTURE.md`、`docs/NEXT_UPGRADE_MASTER_PROMPT.md`。
- 桌面端：`desktop/src/index.html`、`styles.css`、Agent timeline、Diff、terminal、state 与 API 层。
- Android 端：所有 Activity、核心 Adapter/Normalizer/Timeline Builder、全部布局、颜色、主题、尺寸和字符串资源。
- 视觉证据：`desktop/tests/screenshot-*.png`、`desktop/tests/smoke-*.png`、`android-app/screenshots/*.png`。
- 已有只读审计：`desktop-android-audit/desktop-android-audit.html`。该报告只作为线索，最终判断以当前源码和当前截图为准。

### 1.2 参考案例与应吸收的模式

#### Codex App：线程是工作单元，Diff 是一等公民

OpenAI 将 Codex 桌面端定义为 agent command center：任务按项目和线程组织，用户可在线程里审阅改动、评论 Diff，并在编辑器继续人工修改。移动端强调“在关键时刻介入”：查看状态、回答问题、改变方向、审批下一步和补充想法。

本项目应吸收：

- 线程，而非散乱聊天消息，是主要工作单元。
- 运行中的工具过程默认折叠成可扫描摘要，结果和待决策事项优先。
- Diff、测试结果、审批和产物应从线程直接进入，不藏在深层菜单。
- 手机端服务于“及时介入”，不是完整编码。

来源：

- [Introducing the Codex app](https://openai.com/index/introducing-the-codex-app/)
- [Work with Codex from anywhere](https://openai.com/index/work-with-codex-from-anywhere/)

#### Cursor：Agent-first 与 IDE 可并存

Cursor 3 将多 Agent 窗口与 IDE 分离但允许同时存在；在移动/平板端，用固定线程侧栏、分屏审阅和全尺寸 Diff 适配更大屏幕。

本项目应吸收：

- 桌面端提供“编辑焦点”和“Agent 焦点”两种工作模式，不必永久展示所有面板。
- Android 平板/折叠屏使用 list-detail，而不是把手机布局横向拉宽。
- 审阅态可让线程与 Diff 并列，保持上下文。

来源：

- [Cursor 3 — New Cursor Interface](https://cursor.com/changelog/3-0)
- [Cursor changelog — iPad review and split screen](https://cursor.com/changelog)

#### GitHub Agents：相似工具分组，降低时间线噪声

GitHub 的 Agent session log 将相似工具调用分组，工具结果只显示内联预览，文件改动使用熟悉的 Diff，并保留 Bash 命令的透明度；Copilot Review 也会合并冗余时间线事件。

本项目应吸收：

- 连续读文件、搜索、编辑、构建分别聚类，不逐条堆叠大卡片。
- 命令、网络域名、工作目录等安全关键信息不能为了简洁而隐藏。
- 已完成过程退居次要，失败步骤和待审批步骤自动展开。

来源：

- [GitHub Agents tab and redesigned session logs](https://github.blog/changelog/2026-01-26-introducing-the-agents-tab-in-your-repository/)
- [Copilot timeline UI improvements](https://github.blog/changelog/2026-06-18-copilot-code-review-agents-md-support-and-ui-improvements/)

#### VS Code：容器职责稳定，不重复导航

VS Code 的 Activity Bar、Primary Sidebar、Editor、Secondary Sidebar、Panel 和 Status Bar 各有稳定职责；侧栏 View 数量应克制，相关信息成组，避免重复已有功能。

本项目应吸收：

- 桌面端保留工作台容器模型，但取消 Activity Bar 与侧栏顶部 tabs 的重复导航。
- Bottom Panel 只承载 Terminal、Problems、Output、Build Log。
- Agent 线程属于 Secondary Sidebar；完整 Diff、文件和审阅属于 Editor Area。
- 状态栏只放全局/当前文件上下文，不放营销色块。

来源：

- [VS Code UX Guidelines](https://code.visualstudio.com/api/ux-guidelines/overview)
- [VS Code Sidebars Guidelines](https://code.visualstudio.com/api/ux-guidelines/sidebars)

#### Linear：密度不等于喧闹，视觉权重要“挣得”

Linear 的设计刷新强调：导航应后退，主内容应前进；通过一致的 header、对齐、低彩度背景和统一主题生成减少噪声，而不是简单增加空白。

本项目应吸收：

- 中性色占界面 90% 以上，品牌色只用于选中、主要操作、运行焦点。
- 状态不能全部用高饱和彩色胶囊；仅异常、待处理和关键成功使用语义色。
- header 结构、图标尺寸、内边距与基线必须跨页面一致。

来源：

- [How we redesigned the Linear UI](https://linear.app/now/how-we-redesigned-the-linear-ui)
- [A calmer interface for a product in motion](https://linear.app/now/behind-the-latest-design-refresh)

#### Android 官方：按窗口而非设备做适配

Android 官方对 Views 项目建议使用窗口尺寸等级、responsive navigation、list-detail/supporting-pane 模式；紧凑宽度使用底部导航，中等宽度使用 navigation rail，大屏使用 rail 或 persistent drawer。Android 15+ edge-to-edge 要求所有可点击内容正确处理系统栏和 IME insets。

本项目应吸收：

- `<600dp` 单栏；`600–839dp` rail + list/detail；`>=840dp` 多栏或 supporting pane。
- 继续使用 Kotlin + XML + ViewBinding，不因适配需求强制迁移 Compose。
- 交互目标至少 48dp；桌面 Web 目标至少 24 CSS px，并给高频按钮更大命中区。
- 输入框、审批 dock、跳转到底部按钮必须正确响应 IME 与 system bars。

来源：

- [Responsive navigation with Views](https://developer.android.com/develop/ui/views/layout/build-responsive-navigation)
- [Canonical layouts with Views](https://developer.android.com/develop/ui/views/layout/canonical-layouts)
- [Window size classes with Views](https://developer.android.com/develop/ui/views/layout/use-window-size-classes)
- [Edge-to-edge with Views](https://developer.android.com/develop/ui/views/layout/edge-to-edge)
- [Android accessibility for Views](https://developer.android.com/guide/topics/ui/accessibility/views/apps-views)

---

## 2. 当前界面诊断

### 2.1 当前做得好的部分

桌面端：

- 三栏 IDE、Monaco、Diff、Terminal、文件树、Conversation、Job 和 Agent 时间线的功能结构已完整。
- Agent 时间线已支持工具配对、审批、流式回答、改动摘要、Checkpoint 和折叠。
- 深色模式适合长时间开发，界面密度与专业工具定位基本一致。

Android 端：

- Conversation 已从单 TextView 日志升级为 RecyclerView 时间线。
- 用户消息、Agent 工作组、Markdown、代码块、改动摘要、结果和审批已形成不同 UI Model。
- 深浅色模式、48dp 触控目标、自动跟随与“回到底部”逻辑已经有良好基础。

### 2.2 桌面端主要问题

1. **导航重复**：Activity Bar 已切换资源/搜索/对话/任务，Primary Sidebar 顶部又重复一组 tabs。
2. **视觉权重失衡**：底部高饱和蓝色状态栏持续争夺注意力；右侧的状态、工具、卡片、composer 控件几乎同权。
3. **Agent header 语义弱**：两个 select、圆点、加号、更多、关闭并排，用户很难第一眼确认“项目 / 对话 / 当前任务状态”。
4. **完成态仍显得忙**：工具调用、checkpoint、改动卡、状态行的边框过多；结果没有形成明显终点。
5. **空状态价值低**：中央只显示品牌和快捷键，没有最近项目、最近任务、恢复工作等下一步。
6. **品牌不统一**：桌面为蓝青渐变，Android 使用默认 Material 紫色，像两个产品。
7. **微型按钮偏小**：26px icon button 虽接近桌面最低标准，但重要动作和面板边缘按钮不够易点。
8. **缺少聚焦模式**：对 1024 宽度及以下主要依靠隐藏/覆盖，缺少明确的“代码 / Agent / 审阅”模式切换。

### 2.3 Android 端主要问题

1. **首页仍是开发调试表单**：连接设置、项目、Prompt、APK、日志同时出现在一个长页面，违背当前多 Conversation 主路径。
2. **项目详情操作堆叠**：新对话、工作区、Diff、APK 四个大按钮先于 Conversation 内容，主次不清。
3. **Conversation header 信息拥挤**：长标题截断，状态与更多菜单并列，但项目归属和关键动作缺少稳定位置。
4. **运行过程占屏过多**：展开工作组会将最终回答推得很远；工具过程对手机用户并非主要内容。
5. **审批虽清晰但过高**：键盘打开时，审批卡、模式 chips、输入框和 Stop 同时占据底部，内容区急剧缩小。
6. **Diff 与 Build Log 仍是原始文本视图**：缺少文件层级、行级状态、搜索/复制/跳转等审阅辅助。
7. **缺少跨项目 Activity 与 Approval Inbox**：手机最需要的“有哪些任务需要我处理”没有根级入口。
8. **默认紫色缺乏产品识别**：与桌面端不一致，也容易被误认为未定制 Material 示例。

---

## 3. 产品定位与核心任务

### 3.1 桌面端定位

**Agent Workbench**：在同一工作区里完成“发起任务 → 观察过程 → 审批 → 审阅 Diff → 人工调整 → 运行验证 → 获取产物”。

桌面端按重要性排序的核心任务：

1. 打开项目与文件，理解当前代码。
2. 在明确上下文和权限模式下给 Agent 任务。
3. 查看 Agent 当前在做什么、是否阻塞、是否需要人工决定。
4. 审阅并定位文件改动和测试结果。
5. 必要时手动编辑、运行终端、继续追问或恢复 checkpoint。

### 3.2 Android 端定位

**Remote Console**：在离开桌面后完成“查看 → 决策 → 改向 → 验收 → 安装”。

Android 端按重要性排序的核心任务：

1. 快速发现正在运行、失败或等待审批的任务。
2. 安全地批准/拒绝命令、网络、文件和高风险操作。
3. 对当前任务发送 steer，或在本轮结束后 follow-up。
4. 阅读最终回答、改动摘要、测试状态和关键 Diff。
5. 下载、验证、安装或分享 APK。

不把以下目标放入 Android 首要路径：

- 完整代码编辑。
- 复杂终端操作。
- 同屏多面板 IDE。
- 展示所有原始事件和所有模型元数据。

---

## 4. 统一设计概念：Signal Workbench

### 4.1 设计关键词

- Calm：长时间使用不疲劳。
- Legible：任务状态一眼可扫。
- Trustworthy：命令、域名、路径、权限和结果透明。
- Dense, not crowded：信息密集但不靠堆卡片。
- Native by platform：桌面像桌面工作台，Android 像原生远程控制器。

### 4.2 品牌表达

保留现有蓝青品牌标记，但将其从大面积装饰收敛为小型“Signal”符号：

- 形状：12–16px 圆角方形或两段相接的信号轨迹。
- 渐变只用于 App icon、欢迎页小标记和极少数品牌时刻。
- 工作界面使用单色 Signal Blue，避免蓝、青、紫同时竞争。
- Android 不再使用默认 Material 紫色。

### 4.3 双端一致、布局不同

统一：

- 状态命名与颜色。
- 工具类型图标。
- 审批风险层级。
- Agent 工作组、改动摘要、构建结果、Checkpoint 的信息结构。
- 文案语气、日期、时长、文件路径格式。

不同：

- 桌面端显示更多工具过程、上下文和模型控制。
- Android 默认折叠过程，突出待处理与结果。
- 桌面 Diff 在 Monaco 中完成；Android 使用优化的 unified diff 阅读器。
- 桌面保留 Activity Bar/Panel；Android 使用 bottom navigation/navigation rail。

---

## 5. 统一 Design Tokens

### 5.1 颜色

#### Light

| Token | 值 | 用途 |
|---|---:|---|
| `bg.canvas` | `#F6F7F9` | App 背景 |
| `bg.surface` | `#FFFFFF` | 主内容、卡片、输入面 |
| `bg.subtle` | `#F0F2F5` | 次级区、hover、代码背景 |
| `bg.raised` | `#FFFFFF` | 浮层、菜单、dialog |
| `border.default` | `#DDE1E7` | 普通分割线 |
| `border.strong` | `#C6CBD4` | 选中/控件边界 |
| `text.primary` | `#171A1F` | 主文本 |
| `text.secondary` | `#606772` | 次要文本，白底对比度约 5.71:1 |
| `text.tertiary` | `#7A828E` | 仅较大或非关键元信息 |
| `brand.primary` | `#2F66D0` | 主按钮、选中、链接；白字对比度约 5.34:1 |
| `brand.soft` | `#EAF1FF` | 选中背景 |
| `signal.teal` | `#087F78` | 连接/实时 signal |
| `state.success` | `#18794E` | 成功 |
| `state.warning` | `#9A6700` | 等待/警告 |
| `state.danger` | `#C83532` | 失败/高风险 |
| `diff.add.bg` | `#E9F7EF` | Diff 新增背景 |
| `diff.add.fg` | `#116B42` | Diff 新增文本 |
| `diff.del.bg` | `#FCECEC` | Diff 删除背景 |
| `diff.del.fg` | `#A62D2A` | Diff 删除文本 |

#### Dark

| Token | 值 | 用途 |
|---|---:|---|
| `bg.canvas` | `#0F1115` | App 背景 |
| `bg.surface` | `#15181D` | 主内容 |
| `bg.subtle` | `#1C2027` | 次级区/代码背景 |
| `bg.raised` | `#222731` | 浮层 |
| `border.default` | `#2C313B` | 普通边界 |
| `border.strong` | `#414856` | 强边界 |
| `text.primary` | `#EDF0F5` | 主文本，canvas 对比度约 16.54:1 |
| `text.secondary` | `#A3AAB5` | 次要文本，surface 对比度约 7.60:1 |
| `text.tertiary` | `#7D8592` | 非关键元信息 |
| `brand.primary` | `#8EB1FF` | 链接/焦点/选中，surface 对比度约 8.37:1 |
| `brand.container` | `#1E3768` | 选中背景 |
| `signal.teal` | `#56D6C9` | 连接/实时 signal |
| `state.success` | `#5CCB91` | 成功 |
| `state.warning` | `#E5B84C` | 等待/警告 |
| `state.danger` | `#FF8A86` | 失败/高风险 |
| `diff.add.bg` | `#142A20` | Diff 新增背景 |
| `diff.add.fg` | `#72D9A2` | Diff 新增文本 |
| `diff.del.bg` | `#321A1D` | Diff 删除背景 |
| `diff.del.fg` | `#FF9B98` | Diff 删除文本 |

规则：

- 状态不可只靠颜色，必须同时有图标/文字。
- 完成状态尽量使用普通文字 + check icon，不要整块绿色卡片。
- Warning 仅用于等待审批、部分完成和可恢复阻塞。
- Danger 仅用于失败、拒绝、高风险与破坏性确认。
- 彩色背景透明度控制在 6–12%，边框 25–45%。

### 5.2 字体

桌面端：

- UI：系统字体栈 `-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif`。
- 代码：`"SF Mono", "JetBrains Mono", Menlo, Monaco, Consolas, monospace`。
- 基础字号 13px；Agent 正文 14px；标题 15–18px；meta 11–12px。
- 中文正文行高 1.55；代码行高 1.5。

Android：

- 使用系统 Roboto / 中文系统字体，不额外下载字体。
- 正文 16sp；辅助正文 14sp；meta 12sp；页面标题 22sp；栏标题 18sp。
- 代码 13sp，允许用户系统字号缩放。
- 不能用固定高度容器承载可缩放文本。

### 5.3 间距与尺寸

桌面端使用 4px 基础网格：

- 间距：4 / 8 / 12 / 16 / 24 / 32。
- 普通控件高 28px；主要输入/按钮 32–36px。
- 图标按钮视觉 16px，点击区至少 28px；高频动作 32px。
- 侧栏行高 28px；Conversation 行高 48–64px。
- Agent pane 推荐宽 440px，允许 360–620px。

Android 使用 8dp 基础网格、4dp 微调：

- 间距：4 / 8 / 12 / 16 / 24 / 32dp。
- 页面水平边距 16dp；大屏内容最大宽 720dp。
- 所有交互目标至少 48×48dp。
- 列表最小行高 56dp；主要按钮 48–52dp。
- Conversation 正文最大阅读宽度在大屏限制为 720dp。

### 5.4 圆角与阴影

- Desktop：小控件 6px，输入/卡片 8px，浮层 10–12px。
- Android：chip 8dp，卡片 12dp，bottom sheet/dialog 24–28dp 顶角。
- 不使用所有元素统一大圆角。
- 依赖边界和层级，不靠重阴影堆卡片。
- 桌面浮层可用 `0 12px 32px rgba(0,0,0,.32)`；普通面板无阴影。
- Android 使用 Material elevation，但同屏最多两个 elevation 层级。

### 5.5 图标

- 统一使用 20px/20dp 的线性或轻填充图标。
- 不混用 Unicode 字符、emoji 和 SVG 作为同级操作图标。
- Desktop 可将现有内联 SVG 整理为单一 icon map；Android 使用 Material Symbols 风格 VectorDrawable。
- 工具类型映射固定：read=文档、search=放大镜、edit=铅笔/patch、command=terminal、web=globe、build=hammer、test=beaker/check、approval=shield。

### 5.6 动效

- Hover/focus：80–120ms。
- 展开/折叠：160–200ms。
- pane/sheet：220–260ms。
- Streaming 不使用持续跳动大面积 shimmer，只在状态点/短文本使用低幅呼吸。
- 尊重 `prefers-reduced-motion` 和 Android animator duration scale。
- 新内容到达时不强制滚到底部；用户离底部超过阈值时显示“有新内容”。

---

## 6. 统一状态模型与文案

### 6.1 状态命名

| Canonical | 中文 | 图标 | 色彩 | 展示优先级 |
|---|---|---|---|---|
| `queued` | 排队中 | clock | secondary/blue | 中 |
| `running` | 正在运行 | spinner | blue/teal | 高 |
| `awaiting_approval` | 等待审批 | shield-alert | warning | 最高 |
| `paused` | 已暂停 | pause | warning | 高 |
| `cancel_requested` | 正在停止 | square/clock | secondary | 高 |
| `succeeded` | 已完成 | check | success | 中 |
| `failed` | 执行失败 | error | danger | 最高 |
| `canceled` | 已取消 | ban | secondary | 中 |
| `interrupted` | 已中断 | unplug | danger/warning | 最高 |
| `offline` | 连接中断 | cloud-off | danger | 最高 |

### 6.2 文案原则

- 说明“发生了什么 + 用户现在能做什么”。
- 避免只写“错误”“失败”“无权限”。
- 示例：`服务连接已中断。任务仍可能在电脑上运行；正在尝试重新连接。`
- 示例：`构建失败于 :app:compileDebugKotlin。查看 3 条关键错误或让 Agent 修复。`
- 示例：`该命令会删除 build/ 缓存，不会修改源码。是否仅允许本次？`
- 技术 ID 默认放详情页/复制菜单，不直接作为标题。
- 时间优先相对格式（刚刚、3 分钟前），详情显示绝对时间。

---

## 7. 桌面端完整设计方案

### 7.1 信息架构

```text
Title Bar
└─ Workspace / Current file / Connection status / Command palette

Workbench
├─ Activity Bar
│  ├─ Explorer
│  ├─ Search
│  ├─ Conversations
│  ├─ Jobs
│  └─ Settings (bottom)
├─ Primary Sidebar
│  └─ 只显示 Activity Bar 当前容器，不再重复 tabs
├─ Editor Area
│  ├─ Welcome / Recent
│  ├─ Code editor
│  ├─ Diff editor
│  └─ Layout preview
├─ Secondary Sidebar: Agent Thread
│  ├─ Thread header
│  ├─ Timeline
│  ├─ Approval dock
│  └─ Composer
└─ Bottom Panel
   ├─ Problems
   ├─ Output
   ├─ Build
   └─ Terminal

Status Bar
└─ Branch / sync / diagnostics | cursor / language / encoding / Agent connection
```

### 7.2 全局布局

#### 标准宽度 `>=1360px`

- Title Bar：40px。
- Activity Bar：48px。
- Primary Sidebar：240–300px，默认 260px。
- Editor：剩余空间，最小 480px。
- Agent Pane：420–560px，默认 440px。
- Status Bar：22px，使用中性深色，不再整条高亮蓝。

#### 中等宽度 `1100–1359px`

- Primary Sidebar 默认 224px。
- Agent Pane 默认 400px。
- Layout preview 与 Agent Pane 不同时自动展开；打开 preview 时提示可切换 focus mode。

#### 紧凑桌面 `900–1099px`

- Primary Sidebar 可自动收起但 Activity Bar 保留。
- Agent Pane 使用 400px overlay，不压缩编辑器到不可用。
- 顶部提供三态 segmented control：`代码 / Agent / 审阅`，并支持快捷键。

#### 极窄 `<900px`

- 单主面板模式。
- `代码`：Editor + Bottom Panel。
- `Agent`：Agent Pane 全宽。
- `审阅`：Diff 全宽，Agent 以可拉出的 supporting sheet 显示。
- 不允许右侧面板只剩 320px 以下还继续堆 composer controls。

### 7.3 Activity Bar 与 Primary Sidebar

#### Activity Bar

- 只保留 4–5 个稳定目的地。
- 当前项使用 2px 品牌色左指示条 + 稍亮图标，不使用大块选中背景。
- 所有图标有 tooltip、快捷键和 `aria-label`。
- Settings 放底部；Agent 显示/隐藏不作为重复 chat 图标放底部，改由 `Cmd/Ctrl+L` 和标题栏 focus switch 完成。

#### Primary Sidebar

- 删除 `资源 / 搜索 / 对话 / 任务` 的顶部重复 tabs。
- 统一 header 高 36px：左侧当前容器名称，右侧最多 3 个上下文动作。
- Project selector 只在需要项目上下文的 Conversation/Job 容器顶部出现，不在每个页面重复。
- Conversation list 每行：标题、最后一条摘要、状态 icon、更新时间；待审批显示一个 warning dot/badge。
- Jobs list 支持 `活动 / 全部` 筛选，默认活动优先。
- 列表空状态给具体 CTA，不显示裸 ID。

### 7.4 Title Bar 与 Status Bar

#### Title Bar

- 左：品牌标记 + `Android Agent`。
- 中：`项目名 — 文件名` 或 `项目名 — 对话名`；未打开时显示 `Android Agent`。
- 右：Command Palette、全局 connection icon、用户/设置。
- “需要 Token”不使用红色常驻 pill；用 warning icon + `需要连接`，点击打开设置。

#### Status Bar

- 默认背景 `bg.subtle`/深色 `#171A20`，仅在严重离线或远程连接时改变局部 item。
- 左：branch、dirty、diagnostics。
- 右：cursor、语言、编码、Agent 状态。
- Agent 状态是可点击 item，显示 `Agent 已连接` / `重连中` / `等待审批`。

### 7.5 Editor Area

#### Welcome / Empty

替换单纯品牌中心页：

- 左列：`继续工作`，显示最近 3 个项目/文件/线程。
- 右列：`开始`，打开文件夹、新建项目、连接 Agent。
- 底部小型快捷键提示。
- 已连接但未选择项目时，主 CTA 为“选择或新建项目”。
- 未连接时只显示一个明确 connection card，不让右侧 Agent pane 同时重复报错。

#### Tabs 与 Breadcrumbs

- tab 高 34px；dirty 使用小圆点；关闭按钮 hover 才显。
- Diff tab 使用 `Review: <turn title>`，并带 changed files 数量。
- Breadcrumbs 可点击文件层级；不作为纯装饰占行。

#### Diff Review

- Editor toolbar：`12 个文件 · +214 −58`、前后文件切换、布局（side-by-side / inline）、忽略空白、关闭。
- 左侧 changed files tree 可在 Primary Sidebar 临时替换为 `Changes` view；不把文件列表塞进 Agent 小卡。
- Agent Pane 保持当前线程并定位到对应 turn。
- 文件级动作：打开文件、复制路径、恢复该文件（若 API 支持）。
- 整轮恢复是二次确认的危险操作，展示冲突策略和不可覆盖项。
- 准备中显示真实阶段：`读取 checkpoint` → `加载文件` → `生成 Diff`，失败可重试。

#### Layout Preview

- 明确标注“近似预览”，避免被理解为真机渲染。
- 提供设备宽度、主题、刷新。
- 与 Agent Pane 同开时，低宽度自动进入 focus mode。

### 7.6 Agent Thread Header

将当前多个 select 重构为两行、低噪声 header：

第一行：

- breadcrumb：`项目 / Conversation`，Conversation 可点击切换。
- 右侧：新线程、更多、关闭。

第二行：

- 状态：`正在运行 · 1分24秒` / `等待审批`。
- 当前模型与权限模式以次要文本/可点击 menu 显示：`Auto · 工作区`。
- 连接状态仅在异常时出现。

更多菜单：重命名、归档、任务详情、模型、权限、暂停/继续、打开设置。不要把所有 controls 常驻在 header。

### 7.7 Agent Timeline

#### 信息层级

从高到低：

1. 待审批、失败、中断和需要用户回答。
2. 用户消息与 Agent 最终回答。
3. 改动、构建、测试、APK 等结果。
4. 当前执行步骤。
5. 历史工具细节、usage、provider switch。

#### Turn 结构

```text
User request
Run header: Running / Completed / Failed + duration
├─ Plan (有结构化 plan 时显示)
├─ Work group (reads/searches/edits/commands grouped)
├─ Approval (inline origin + pinned dock when pending)
├─ Agent response
├─ Changes / Tests / Build / APK results
└─ Turn footer: model · tokens · checkpoint · actions
```

#### 默认折叠策略

- 当前 running turn：展开当前 step，旧已完成 step 折叠。
- completed turn：Work group 折叠，只显示 `查看 8 个文件 · 修改 3 个文件 · 运行 2 个命令`。
- failed turn：自动展开失败 step 和相关 stderr 关键段。
- awaiting approval：审批卡自动展开并聚焦；时间线原位保留，composer 上方显示 dock。
- usage/provider/memory/rules 默认并入 `任务详情`，不占时间线主流。

#### 工具展示

- 相似工具连续调用合并：`读取 5 个文件`、`搜索 3 次`、`修改 4 个文件`。
- 命令单独保留，摘要显示命令首行、工作目录、退出码、时长。
- 输出默认预览 6–10 行；可展开到 240px，再进入完整 Output/Build panel。
- 失败时显示“复制错误”“在构建日志打开”“让 Agent 修复”。
- 不能只显示工具内部名称；使用中文动作 + 技术名副标题。

#### Agent 回答

- Assistant 正文不使用重边框卡片，直接落在时间线内容列。
- 14px、1.6 行高，正文宽度随 pane。
- 标题、列表、表格、引用和代码保持清晰间距。
- 表格可横向滚动并固定表头（内容足够长时）。
- 代码块 header：语言、文件名（若有）、复制、在编辑器打开。
- Streaming 在同一 answer block 原地增长，不能产生重复最终消息。

### 7.8 Approval

#### 卡片内容

1. 风险类型：命令 / 网络 / 文件 / 安装 / 破坏性。
2. 一句话意图：Agent 为什么需要它。
3. 精确范围：命令、cwd、域名、路径、目标文件。
4. 影响：是否写入、是否联网、是否可能删除、是否只影响 build cache。
5. 操作：`拒绝`（次要）与 `仅允许本次`（主要）。

#### 风险视觉

- 低风险读取：neutral，不弹审批则不展示。
- workspace write：warning。
- network：blue + globe，仍显示目标域名。
- process：purple 仅作为工具类型，不作为品牌主色。
- destructive：danger，主要按钮不能默认聚焦到“允许”。

#### Dock

- composer 上方固定 36–44px：`1 项操作等待确认` + `查看`。
- 点击滚动到原始卡；原始卡不从时间线移除。
- 多审批时 dock 打开一个队列 sheet/popover，不纵向堆满 composer 上方。

### 7.9 Composer

#### 默认态

- 外层为单一 surface，而不是 textarea + 一排松散控件。
- 上方 context chips，最多两行，溢出显示 `+3`。
- 中间 textarea 1–8 行自动增长。
- 下方左：添加上下文、模型、权限；右：发送。
- `Cmd/Ctrl+Enter` 发送，`Shift+Enter` 换行。

#### 运行态

- 输入模式以 segmented chips 表达：`引导当前任务` / `本轮结束后追问`。
- 默认保持用户上次选择，但每个新任务重置为 `引导`。
- Stop 为独立 danger icon button，必须有 tooltip 和确认策略（按当前能力决定是否二次确认）。
- Pause/Resume 放 header 状态菜单，不与 Send 挤在一行。

#### 禁用/离线态

- 不只降低 opacity；placeholder 明确原因。
- 示例：`选择项目后可发送任务`、`连接中断，输入内容将保留`。
- 用户输入在发送失败后必须保留。

### 7.10 桌面端关键页面/状态清单

必须设计并截图验收：

1. 首次启动/未连接。
2. 已连接、无项目。
3. 有项目、编辑器空状态。
4. 打开代码文件 + Agent 空线程。
5. 正在运行，含 plan 与多工具组。
6. Streaming answer。
7. 命令审批与网络审批。
8. 成功 turn + changes + build passed。
9. 失败 turn + error + retry/recover。
10. Monaco Diff review。
11. Terminal/Problems/Build bottom panel。
12. 三轮历史的折叠与恢复。
13. 断线重连，不重复消息。
14. 1440×900、1024×768、900×700、窄窗口 focus mode。

---

## 8. Android 端完整设计方案

### 8.1 根级信息架构

紧凑宽度底部导航 3 项：

1. **项目**：项目列表与继续工作。
2. **活动**：跨项目 running/paused/failed/recent jobs。
3. **待处理**：审批 Inbox，badge 显示数量。

Settings/Connection 放右上角 toolbar menu，不占第四个高频目的地。

中等宽度：Bottom Navigation 切换为 Navigation Rail。
Expanded：Navigation Rail + list pane + detail pane；必要时 Approval 作为 supporting pane。

### 8.2 导航结构

```text
App
├─ Connection Onboarding / Settings
├─ Projects
│  ├─ Project list
│  └─ Project hub
│     ├─ Conversation list
│     ├─ Workspace
│     ├─ Changes
│     └─ APK artifact
├─ Activity
│  └─ Job / Conversation detail
├─ Approvals
│  └─ Approval detail / decision
└─ Conversation
   ├─ Timeline
   ├─ Task details
   ├─ Diff
   ├─ Build log
   └─ APK
```

### 8.3 Connection Onboarding

不要在项目主页常驻四个连接字段。

#### 首次连接页

- 顶部：小型 Signal brand + `连接到你的 Agent`。
- 说明：`Agent 在你的电脑或服务器运行；手机只用于远程控制和审阅。`
- 主要字段：服务地址、访问 Token。
- Token 使用 password toggle，支持粘贴和扫码作为未来增强，但不作为当前必做。
- 主按钮：`测试并连接`。
- Advanced 折叠：网络初始化/registration token。
- 底部：HTTPS/LAN 安全说明、常见错误帮助。

#### 已连接设置页

- connection card：主机、延迟、用户、最后同步、TLS 状态。
- 操作：重新连接、编辑地址、替换 Token、断开。
- 危险操作与普通设置分区。

### 8.4 Projects 首页

#### Top App Bar

- 标题 `项目`。
- 右侧：搜索、连接状态/设置。
- 连接异常时在内容顶部显示可关闭/可操作 banner，不改变整个 app bar 颜色。

#### 内容

- 若有 active job：顶部 `正在进行` 区，显示 1–3 个 compact activity cards。
- `最近项目` 列表：项目名、包名、最后任务状态、更新时间、是否有 APK。
- 每行点击进 Project Hub；more menu 提供重命名/删除等低频动作。
- 新建项目使用 Extended FAB `新建项目`，滚动后可收缩为 icon。
- 空状态：图标、说明、`新建项目`，不显示固定高度空 RecyclerView。

### 8.5 Project Hub

替换当前四个大按钮的首屏布局。

#### Header

- 项目名。
- 包名作为 secondary mono text。
- 最新状态一行：`上次构建成功 · 12 分钟前`。
- overflow：项目设置、删除。

#### 快速继续

- 若有最近 Conversation：一张 `继续 <title>` card，包含摘要、状态、更新时间。
- 若有等待审批：warning card 置顶，CTA `立即处理`。
- 若有新 APK：artifact compact card，CTA `查看 APK`。

#### Conversation 列表

- section header：`对话` + filter/search。
- 每行：标题、最后摘要 1–2 行、状态、更新时间、待审批 badge。
- 新建对话使用 FAB，不占整行大按钮。

#### 辅助入口

使用统一 list rows：

- `工作区文件` — 最近打开路径。
- `改动与 Checkpoint` — 3 files changed。
- `构建与 APK` — build passed / no artifact。

这些入口放 Conversation 列表之后，或在大屏 supporting pane；不与“新对话”同权。

### 8.6 Activity

跨项目任务监控页：

- filters：`进行中`、`最近`、`失败`。
- Job row：项目、Conversation 标题、状态、当前动作/最终摘要、更新时间。
- running row 显示轻量进度，不假装有不可得的百分比。
- awaiting approval 永远排在最上方。
- failed row 显示第一条可操作错误，不显示全栈。
- swipe 不执行破坏性操作；长按/overflow 提供更多。

### 8.7 Approval Inbox

- 顶部显示 `2 项待处理`，支持按项目分组。
- 卡片摘要：类型、项目/Conversation、Agent 意图、目标范围、等待时长。
- 点击进入详情或直接展开；批准/拒绝必须使用服务端 canonical approval id。
- 多审批批量“全部允许”不是当前目标。
- destructive approval 禁止从列表一键批准，必须进详情确认。
- 审批已在桌面处理时，原位变为 `已在其他设备处理`，不弹错误 toast。

### 8.8 Conversation

#### Top App Bar

- 第一行：Conversation 标题，最长一行省略。
- navigation：返回 Project Hub。
- overflow：任务详情、Diff、构建日志、APK、重命名、归档。
- 标题下状态行：`正在运行 · 1分24秒` / `等待审批` / `已完成`。
- 项目名在返回后的过渡/副标题中可见，大屏 list pane 已显示时可省略。

#### Timeline

- 用户消息：右对齐浅品牌色 bubble，最大宽 88%。
- Agent answer：左对齐无外卡，全文阅读优先。
- Work group：单行 header + 摘要；完成后默认折叠。
- Tool step：只在展开 Work group 后出现；错误 step 自动展开。
- Status lines：合并成 1 个可展开 group，不逐行散落。
- Changes：compact summary，点击进入 Diff。
- Build/APK：独立结果 row，成功后 CTA 明确。
- Turn footer：状态与耗时；provider/model/tokens 进入 Task Details bottom sheet。

#### 自动滚动

- 用户位于底部 96dp 内：新内容自动跟随。
- 用户上滑离开底部：停止跟随，显示 `↓ 3 条新内容` floating pill。
- 提交新消息后可滚到底部。
- 加载更早历史保留当前可见位置，不能跳动。

#### Streaming

- 同一 Assistant row 原地更新。
- 使用 `正在回复…` 小状态，不用大 spinner 占位。
- Markdown block 增量更新要节流，避免每 token 重排整页。

### 8.9 Mobile Composer

#### 默认态

- 固定于 IME 上方并正确处理 navigation bar inset。
- 输入容器 52–144dp 自增长。
- placeholder：`描述要完成的任务`。
- 右侧圆形/圆角方形 Send，48dp touch target。
- 附件/上下文如当前不支持，不放假按钮。

#### 运行态

- 输入框上方一行 compact mode tabs：`引导当前任务` / `后续追问`。
- Stop 使用 48dp danger icon button。
- 当 awaiting approval 时，composer 仍保留但 Approval Dock 优先；不要让完整审批卡永远和键盘同时挤在底部。

#### Approval Dock

- 键盘关闭：composer 上方显示 compact warning bar，点击打开 modal bottom sheet。
- 键盘打开：只保留 40–48dp bar，不直接显示完整审批卡。
- Bottom sheet 展示完整命令/域名/路径与操作，支持复制技术详情。

### 8.10 Markdown 与代码

- 正文 16sp，段落间距 12dp。
- H1/H2 不应达到营销页面尺寸；22/20sp 足够。
- 表格默认横向滚动，首行加权，单元格最小宽度，外边框 1dp。
- 代码块：surfaceVariant、12dp 圆角、header 显示语言/复制。
- 代码内容横向滚动，不强制自动换行；普通长 URL/路径允许 break。
- 超过 16 行默认折叠，显示 `展开全部 · 48 行`。
- 复制成功使用短 Snackbar，不改按钮文案造成布局跳动。

### 8.11 Diff

移动端不复制桌面 side-by-side Diff。

#### 默认 unified diff

- Top App Bar：`改动`，文件计数和 +/- 统计。
- 文件 selector：顶部 sticky row 或 modal bottom sheet。
- 每个 hunk 显示路径、行号范围和上下文。
- 新增/删除使用低饱和背景 + `+/-` 符号；不能只靠红绿。
- 长行水平滚动；保持行号固定区域。
- 提供：复制行、复制文件路径、在工作区查看。
- 大 Diff 分页/分文件加载；截断时明确说明并提供继续加载。

#### Restore

- `恢复到 Checkpoint` 放 overflow 或页面底部 secondary danger action。
- 确认 dialog 显示将影响的文件数、dirty workspace 冲突策略、不可恢复说明。
- 冲突使用可复制文件列表，不只 toast。

### 8.12 Build Log

- 顶部 summary card：成功/失败、duration、task、关键错误数量。
- tabs 或 filters：`摘要` / `完整日志`。
- 摘要提取错误块并支持 `让 Agent 修复`。
- 完整日志使用 mono、搜索、复制、跳到首个错误。
- 大日志增量/分页加载，不一次性渲染到单 TextView。

### 8.13 APK Artifact

- Artifact card：App 名、包名、version、大小、构建时间、SHA-256 简写、签名摘要。
- 状态机：未生成 / 可下载 / 下载中 / 校验中 / 已下载 / 校验失败 / 可安装 / 已安装。
- 主 CTA 随状态变化：`下载 APK` → `校验中` → `安装`。
- 次要动作：分享、构建日志、复制 SHA-256。
- 安装未知来源引导使用解释页/系统跳转，回来后自动继续或明确提示下一步。
- 校验失败禁止安装并允许重新下载。

### 8.14 Android 自适应

#### Compact `<600dp`

- Bottom Navigation。
- 单栏 list → detail navigation。
- Conversation 全屏。
- Approval 用 bottom sheet。

#### Medium `600–839dp`

- Navigation Rail。
- Projects/Activity 使用 list-detail 50/50。
- Conversation 可显示左侧 Conversation list + 右侧 thread；composer 只在 detail pane。
- 横屏且高度 `<480dp` 时回退单 pane，避免键盘后内容过窄。

#### Expanded `>=840dp`

- Rail + 320dp list pane + flexible detail。
- Approval/Task Details 可作为 30% supporting pane。
- Diff 可使用文件 list + unified diff 两 pane。
- 内容列最大宽度，不能把文本拉满 1200dp。

技术上继续 Views：

- 使用 WindowManager `WindowSizeClass.compute()` 或资源 qualifier。
- 可采用 `layout/`、`layout-w600dp/`、`layout-w840dp/`。
- 用 ViewModel/保存状态在 configuration change 后恢复项目、Conversation、Job、cursor、展开状态和输入草稿。

### 8.15 Android 关键页面/状态清单

必须设计并截图验收：

1. 首次连接。
2. 连接失败/Token 无效/HTTPS 要求。
3. Projects：空、有项目、有 active job。
4. Project Hub：无 Conversation、有 Conversation、有 APK、有待审批。
5. Activity：running/failed/recent。
6. Approval Inbox：命令、网络、高风险。
7. Conversation：空、running、streaming、completed、failed、interrupted。
8. Conversation + IME + Approval Dock。
9. Markdown：标题、列表、表格、代码块、长链接。
10. Diff：多文件、大 Diff、恢复冲突。
11. Build Log：成功、失败、超长日志。
12. APK：下载、校验、安装权限、分享、失败。
13. Light/Dark。
14. 360×800、412×915、600×960、840×900、横屏 compact height。
15. 字体 1.0x、1.3x、2.0x。

---

## 9. 跨端同步体验

### 9.1 同一状态、同一语义

- 桌面批准后 Android 审批卡原位更新为 `已在桌面处理`。
- Android steer 后桌面时间线显示来源 `来自 Android`，但不额外制造系统消息噪声。
- 同一 Conversation 的标题、状态、未读/新内容游标同步。
- 新 APK 和任务完成在 Android Activity 与项目 Hub 同时出现，但只发一次通知。

### 9.2 乐观更新边界

可以乐观：

- 重命名标题。
- 展开/折叠本地 UI。
- 输入模式切换。

不能假装已成功：

- 审批决定。
- Checkpoint 恢复。
- Stop/Pause/Resume。
- APK 校验与安装。

这些动作应显示处理中，并等待 canonical server state 回执。

### 9.3 断线

- 保留当前内容和输入草稿。
- 顶部显示 compact reconnect banner。
- 明确“本机任务可能仍在运行”。
- 恢复后按 cursor 补事件，不能重复消息或重复工具卡。
- 多端同时决定审批时，以服务端结果为准，另一端显示 handled elsewhere。

---

## 10. 可访问性与质量门槛

### 10.1 对比度与颜色

- 普通文本至少 4.5:1，大文本至少 3:1。
- Focus、边界、图标等非文本组件至少 3:1。
- 状态不得只靠颜色。
- Diff 同时使用颜色、`+/-` 和行类型说明。

参考：[WCAG 2.2](https://www.w3.org/TR/WCAG22/)

### 10.2 目标尺寸

- Desktop 所有 pointer target 至少 24×24 CSS px 或具备足够间距；高频控件至少 28–32px。
- Android 至少 48×48dp。
- 相邻危险/确认按钮保持 8dp/8px 以上间距。

参考：[WCAG 2.5.8 Target Size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)

### 10.3 键盘与读屏

Desktop：

- 所有功能可键盘到达。
- 明确 focus ring；overlay 打开后 focus trap，关闭后返回触发器。
- 时间线 `role=log` 不应每个 token 打断 screen reader；按语义块公告。
- 可调整面板有键盘替代操作。

Android：

- ImageButton 必须有 contentDescription；装饰图标标为无障碍忽略。
- 状态组合成有意义的一句 contentDescription。
- TalkBack 顺序：标题 → 意图 → 风险 → 范围 → 拒绝 → 允许。
- 动态更新使用合适 live region，不连续播报流式 token。

### 10.4 大字体与本地化

- 200% 文本缩放不丢失功能。
- 状态 pill 允许换行或变为两行 row。
- 不在 Kotlin/JS 写死界面文案；Android 使用 strings，Desktop 使用统一 copy map 或可本地化结构。
- 中文标点、空格、英文技术名格式统一。

---

## 11. 设计到代码的映射

### 11.1 Desktop

建议重点修改：

- `desktop/src/index.html`：去除重复 sidebar tabs，重组 Agent header、welcome、composer、focus mode。
- `desktop/src/styles.css`：统一 token、layout breakpoint、timeline hierarchy、accessibility。
- `desktop/src/renderer.js`：focus mode、welcome recent items、Diff review toolbar。
- `desktop/src/ai-panel.js`：header、composer modes、approval dock、connection/reconnect banner。
- `desktop/src/agent-timeline.js`：工具聚类、默认折叠、结果优先级。
- `desktop/src/timeline.js`：只在数据/归并层需要时改，不为纯视觉重写协议层。

不要：

- 引入 React/Vue 重写。
- 替换 Monaco/xterm。
- 用外部 CDN 字体或图标。
- 修改后端协议来迁就纯布局问题。

### 11.2 Android

建议新增/重构：

- 根导航容器（可使用单 Activity + Fragment，也可在现有 Activity 架构上渐进实现）。
- `MainActivity` 收敛为连接 gate + root navigation，不再承载旧 Prompt/日志/APK 全流程。
- `ProjectDetailActivity` 重构为 Project Hub。
- 新增 Activity/Fragment：Activity feed、Approval inbox；或先以 tabs/child destinations 实现。
- `ConversationActivity`：header、approval bottom sheet/dock、composer、insets。
- `DiffActivity`：文件 selector + unified diff rows。
- `BuildLogActivity`：summary/full log。
- `ApkActivity`：artifact state card。
- `res/values/colors.xml`、`values-night/colors.xml`、themes、dimens、styles：建立统一 token。
- `layout-w600dp/`、`layout-w840dp/`：渐进增加自适应。

继续使用：

- Kotlin + XML + ViewBinding。
- RecyclerView + ListAdapter/DiffUtil。
- Material Components。
- 现有 Event Normalizer/Timeline Builder/Timeline Store。

不要：

- 迁移 Jetpack Compose。
- 在 Adapter 内直接解析原始 JSON。
- 每次 event 全量刷新整个 RecyclerView。
- 把服务器技术 ID 当用户标题。
- 在主线程格式化/校验大 APK 或大 Diff。

---

## 12. 分阶段实施路线

### Phase 0：锁定基线与回归样本

- 保存当前双端截图与测试结果。
- 建立 visual fixture：空、运行、审批、成功、失败、Diff、断线。
- 不改事件协议。

### Phase 1：统一 tokens 和全局 chrome

- Desktop 颜色/字体/状态栏/导航去重。
- Android 颜色/主题/基础尺寸/edge-to-edge。
- 双端状态色与 icon mapping 对齐。

### Phase 2：核心线程体验

- Desktop Agent header、timeline hierarchy、composer、approval dock。
- Android Conversation header、timeline、composer、approval sheet。
- 完成流式、折叠、断线、IME 回归。

### Phase 3：导航与项目体验

- Desktop Conversations/Jobs sidebar。
- Android root Projects/Activity/Approvals 与 Project Hub。
- 多端同步 handled elsewhere。

### Phase 4：审阅与产物

- Desktop Diff review toolbar/changes tree。
- Android unified Diff、Build summary/log、APK artifact。

### Phase 5：自适应、无障碍与视觉收口

- Desktop 4 个 viewport。
- Android compact/medium/expanded、横屏、2.0x 字体。
- TalkBack、键盘、对比度、reduced motion。

每个 Phase 都必须：代码检查 → 单元测试 → 功能 smoke → 截图 → 人工视觉审阅，不允许只更新截图基线掩盖回归。

---

## 13. 完整提示词 A：高保真 UI/UX 设计稿生成

以下提示词适合交给具备设计稿生成能力的设计 Agent、Figma Agent 或 UI 原型工具。它只生成设计，不直接改代码。

```text
你是一名资深 Developer Tools 产品设计负责人、Design Systems 设计师和 Android Adaptive UI 专家。

请为项目“Android Agent”设计一套完整、可落地、高保真的桌面端与 Android 端 UI/UX。产品不是营销网站：它是一个本地/可信网络中的 Android 编程 Agent 控制台。

产品定位：
1. 桌面端是 Agent Workbench：代码编辑、Agent 任务、审批、Diff 审阅、终端、构建与 APK 产物在同一工作台完成。
2. Android 端是 Remote Console：用户离开电脑后查看跨项目任务、处理审批、改变 Agent 方向、阅读结果、审阅关键 Diff、下载和安装 APK。不要把手机做成完整 IDE。

视觉方向：
- 关键词：calm, trustworthy, dense but not crowded, precise, native, technical.
- 不是 VS Code 皮肤，不是聊天 App，不是满屏卡片，不是紫色 Material Demo。
- 90% 使用冷静中性色；品牌色 Signal Blue 只用于主要操作、选中和焦点；Signal Teal 只用于实时连接。
- 支持完整 light/dark themes。
- 桌面使用系统 UI 字体和 mono 字体；Android 使用系统 Roboto/中文系统字体，不用装饰性字体。
- 边框细、阴影克制、圆角分级。不要玻璃拟态、霓虹、夸张渐变、3D、插画主导或大面积品牌色。

品牌 token：
Light: canvas #F6F7F9, surface #FFFFFF, subtle #F0F2F5, border #DDE1E7, text #171A1F, secondary #606772, primary #2F66D0, primarySoft #EAF1FF, teal #087F78, success #18794E, warning #9A6700, danger #C83532.
Dark: canvas #0F1115, surface #15181D, subtle #1C2027, raised #222731, border #2C313B, text #EDF0F5, secondary #A3AAB5, primary #8EB1FF, primaryContainer #1E3768, teal #56D6C9, success #5CCB91, warning #E5B84C, danger #FF8A86.

统一语义状态：queued 排队中、running 正在运行、awaiting_approval 等待审批、paused 已暂停、cancel_requested 正在停止、succeeded 已完成、failed 执行失败、canceled 已取消、interrupted 已中断、offline 连接中断。状态不可只靠颜色，必须有图标和文字。

桌面端画板：1440×900 主画板，另做 1024×768 与 900×700 适配。
桌面结构：40px title bar；48px activity bar；240–300px primary sidebar；flexible Monaco editor；420–560px Agent secondary sidebar；22px neutral status bar；可展开 bottom panel。
Activity Bar 只保留 Explorer、Search、Conversations、Jobs、Settings。Primary Sidebar 不再重复 tabs。
Editor 支持 Welcome/Recent、code editor、Monaco side-by-side Diff、Android XML preview、Terminal/Problems/Output/Build。
Agent Pane 分为 thread header、timeline、approval dock、composer。
Thread header 用“项目 / Conversation”breadcrumb，第二行显示任务状态、耗时、模型和权限；低频操作放 overflow。
Timeline 以 Turn 为单位：用户请求 → run header → plan → grouped work → approval → Agent answer → changes/tests/build/APK results → task details。相似工具调用聚类；completed turn 默认折叠过程；failed turn 自动展开错误；awaiting approval 自动突出。
Agent answer 不要厚重卡片，使用高可读正文。代码块有语言、复制、在编辑器打开。
Approval 卡必须显示意图、精确命令/域名/路径、工作目录、影响和风险；操作为“拒绝”与“仅允许本次”。等待时在 composer 上方有 compact dock。
Composer 是单一 command surface：context chips、1–8 行 textarea、添加上下文、模型、权限模式、发送；运行中切换“引导当前任务/后续追问”，独立 Stop。
Status bar 为中性，不能整条高亮蓝。
Welcome 显示继续工作、最近项目和开始操作，不只放 Logo。

Android 画板：360×800、412×915、600×960、840×900；每个关键页面都做 light/dark。
Compact 根导航为 3 项 bottom navigation：项目、活动、待处理；Settings 在 toolbar。Medium 切 navigation rail + list-detail；Expanded 使用 rail + list pane + detail/supporting pane。
连接页：服务地址、访问 Token、测试并连接；registration token 在 Advanced；解释手机只做远程控制。
Projects：首页顶部可显示 active jobs，下面最近项目，Extended FAB 新建项目。
Project Hub：项目名、包名、最新构建；优先显示等待审批、继续最近 Conversation、新 APK；Conversation list 是主体；工作区、改动、构建/APK 是次级 list rows。不要四个同权大按钮。
Activity：跨项目 running/failed/recent jobs，awaiting approval 永远最前。
Approval Inbox：按项目分组，展示意图、风险、范围与等待时间；高风险必须进入详情确认。
Conversation：标题+状态、Recycler timeline、用户 bubble、无卡片 Agent answer、默认折叠 work group、changes/build/APK result rows、IME 上方 composer。用户离开底部后显示“3 条新内容”。
键盘打开时 approval 只显示 compact dock，完整审批在 modal bottom sheet。
Diff 使用 mobile unified diff：file selector、sticky hunk header、固定行号、红绿低饱和背景并带 +/-，大 Diff 分页；Checkpoint 恢复是次级危险操作。
Build Log 分摘要/完整日志，支持首错跳转、搜索、复制和“让 Agent 修复”。
APK 页面显示包名、version、大小、时间、SHA-256、签名摘要，以及下载→校验→安装状态机。

必须输出：
1. 双端 sitemap 和主要 user flows。
2. 完整 design tokens 与组件库。
3. 桌面端不少于 12 个关键画板/状态。
4. Android 不少于 15 个关键画板/状态，并覆盖 compact/medium/expanded。
5. 每个组件的 default/hover/focus/pressed/disabled/loading/error/success 状态。
6. 命令审批、网络审批、高风险审批的完整样例。
7. running、streaming、completed、failed、interrupted、offline 的完整样例。
8. Light/Dark、2.0x 字体、键盘打开、长标题、长路径、长命令、长 Markdown 表格等 stress cases。
9. 标注 spacing、尺寸、文字样式、颜色 token、交互行为和 responsive rules。
10. 给开发的 handoff 注释，不使用无法由 Electron/CSS 或 Kotlin/XML/Material Components 实现的效果。

禁止：
- 不生成营销首页。
- 不使用 lorem ipsum；使用真实中文 Agent、Android、Gradle、Diff、APK 文案。
- 不把工具过程每条都做成大卡片。
- 不把所有状态做成彩色 pill。
- 不依赖外部字体、付费图标或网络图片。
- 不改变既有产品能力，不虚构云账号、团队协作或手机本地执行 Agent。
```

---

## 14. 完整提示词 B：桌面端实现

```text
你是一名资深 Electron/Monaco 前端工程师、Developer Tools 产品设计师和无障碍专家。

请直接在 `/Users/sakura/Android Agent` 中实现桌面端 UI/UX 重构。目标是把现有 Electron + Monaco 产品升级为冷静、可信、信息密集但不拥挤的 Agent Workbench。不要只输出建议；完成代码、测试、截图与视觉检查。

开始前：
1. 运行 `git status --short --branch`。
2. 阅读 `docs/DESKTOP_ANDROID_UI_DESIGN_SYSTEM_AND_PROMPTS.md` 全文，并将其作为设计基线。
3. 阅读 `README.md`、`MVP_SPEC.md`、`desktop/package.json`、`desktop/src/index.html`、`styles.css`、`renderer.js`、`ai-panel.js`、`agent-timeline.js`、`timeline.js`、`state.js`、`terminal.js`、`agent-api.js`、`main.js`、`preload.js` 和现有测试。
4. 查看 `desktop/tests/screenshot-*.png` 与 `desktop/tests/smoke-*.png`。
5. 当前工作区有用户未提交改动。不得覆盖、回退、重置、格式化或删除无关改动；修改重叠文件前先理解现有差异。

技术约束：
- 继续使用现有 HTML/CSS/原生 JavaScript、Electron、Monaco、xterm。
- 不引入 React/Vue/Svelte，不重写主进程，不替换 Monaco/xterm。
- 保持 `contextIsolation`、sandbox、CSP 与安全存储。
- 不使用 CDN、远程字体、远程图标或运行时网络 UI 依赖。
- 不修改后端协议来解决纯 UI 问题。
- 所有用户输入、工具输出和 Markdown 必须继续安全渲染，不能引入 innerHTML XSS。

视觉 token：
按设计文档第 5 节实现统一 light/dark token。当前版本可先以 dark 为默认，但 CSS 结构必须允许 light theme。状态栏改为中性，品牌色只用于焦点、选中和主要动作。统一 4px grid、6/8/12px 圆角、系统字体和 mono 字体。普通文字对比度达到 WCAG AA。

必须实现：

一、全局 chrome 与导航
- 删除 Activity Bar 与 Primary Sidebar 顶部 `资源/搜索/对话/任务` 的重复 tabs；Activity Bar 是唯一一级容器切换。
- Primary Sidebar header 统一为 36px，最多 3 个上下文动作。
- Activity Bar 保留 Explorer、Search、Conversations、Jobs、Settings；图标风格统一，tooltip/aria-label/快捷键完整。
- Title Bar 中部显示项目与当前文件/Conversation；连接异常只在右侧局部提示。
- Status Bar 改为中性；局部显示 branch、diagnostics、cursor、language、encoding、Agent 状态。
- icon buttons 点击区至少 28px，高频至少 32px，focus-visible 清晰。

二、Welcome 与 Editor
- Welcome 显示“继续工作”和“开始”，包含最近项目/文件/Conversation、打开文件夹、新建项目、连接 Agent；状态对应 CTA，不能重复报错。
- 保持 tabs、breadcrumbs、Monaco、layout preview、bottom panel 全部功能。
- 改善 tab dirty/close 状态。
- Diff review toolbar 显示文件数、+/-、上/下文件、inline/side-by-side、忽略空白、关闭。
- Changes 文件列表进入专用 sidebar view 或 editor supporting view；点击打开对应 Monaco Diff。
- Diff loading 显示阶段和重试；关闭后继续正确 dispose models。

三、Agent Header
- 第一行显示 `项目 / Conversation` breadcrumb 与新线程、更多、关闭。
- 第二行显示 canonical job 状态、耗时、模型、权限；仅异常显示连接状态。
- Pause/Resume、重命名、归档、任务详情、模型、权限、设置进入合理的 overflow/menu。
- 不再常驻两个宽 select 与过多小按钮，但保留现有所有能力。

四、Agent Timeline
- Turn 是主结构：用户请求、run header、plan、grouped work、approval、answer、changes/tests/build/APK、footer。
- completed turn 默认折叠 work group；current running 展开当前 step；failed 自动展开失败 step；awaiting approval 展开审批。
- 连续 read/search/edit/command 分组，摘要使用中文动作与数量；命令保留命令、cwd、exit code、duration 和关键输出。
- 不删除原始技术详情；详情折叠或进入 Output/Build panel。
- Assistant answer 不使用厚重外卡，14px、1.6 line-height；Markdown、表格、代码块清晰。
- Streaming 同一 message 原地增长，持久事件回放不能重复内容。
- changes/build/test/APK 是结果组件，比 usage/provider/memory/rules 更突出。
- usage/provider/checkpoint 等移到 turn footer 或 Task Details，除非异常。

五、Approval
- 卡片显示风险类型、Agent 意图、精确命令/域名/路径、cwd、影响和技术详情。
- 操作为“拒绝”和“仅允许本次”；danger approval 默认焦点不能落在允许。
- pending approval 在 timeline 原位保留，并在 composer 上方显示 compact dock。
- 多审批 dock 打开队列 popover/sheet，不纵向堆叠。
- 审批提交中禁用重复提交；失败可重试；其他端已处理时显示 handled elsewhere。

六、Composer
- 设计为单一 surface：context chips、自动增长 textarea、context/model/run mode、send。
- chips 最多两行，溢出 `+N`。
- `Cmd/Ctrl+Enter` 发送，`Shift+Enter` 换行。
- 运行中提供“引导当前任务/本轮结束后追问”明确模式与独立 Stop。
- disabled/offline/无项目时 placeholder 说明原因；发送失败保留草稿。

七、响应式与焦点模式
- >=1360：48 activity + 260 sidebar + flex editor + 440 agent。
- 1100–1359：224 sidebar + 400 agent。
- 900–1099：sidebar 可收起，Agent 400 overlay。
- <900：代码/Agent/审阅单主面板模式；不能只把所有控件挤小。
- 1440×900、1024×768、900×700 和窄宽度均无重叠、裁切、不可点击控件。

八、无障碍
- 所有 icon button 有 label/tooltip；overlay focus trap 与 focus return 正确。
- `role=log` 不按 token 连续播报。
- keyboard 可到达全部功能，focus ring 不被遮挡。
- 支持 reduced motion。

测试：
- 保留并扩充现有 agent timeline、event normalization、state、AgentApi 单元测试。
- 新增/更新测试覆盖：工具分组、默认折叠、approval dock、handled elsewhere、断线重连不重复、草稿保留、focus mode。
- 运行 `npm run check`、`npm run test:unit`、`npm run test:screenshot`。
- 使用 fake server/现有 Playwright smoke，不调用真实模型和外网。
- 生成并人工查看关键截图：empty、running、streaming、approval command、approval network、completed+changes、failed、Diff、three-turn、narrow/focus mode。
- 不得只更新 screenshot baseline；截图失败先判断布局是否真的回归。
- 运行 `git diff --check`。

最终回复必须列出：
1. 改动的布局与组件。
2. 状态、审批、composer 与响应式行为。
3. 无障碍改进。
4. 测试命令和结果。
5. 截图绝对路径与人工检查结论。
6. 任何未完成项及原因。
```

---

## 15. 完整提示词 C：Android 端实现

```text
你是一名资深 Android Kotlin/XML 工程师、Material 3 产品设计师、Adaptive UI 和无障碍专家。

请直接在 `/Users/sakura/Android Agent` 中实现 Android 客户端 UI/UX 重构。目标是把现有客户端升级为面向远程 Agent 管理的原生 Remote Console：优先查看跨项目任务、处理审批、改变任务方向、阅读结果、审阅 Diff、下载和安装 APK。不要把手机做成完整 IDE。不要只输出建议；完成代码、单测、构建、截图与视觉检查。

开始前：
1. 运行 `git status --short --branch`。
2. 阅读 `docs/DESKTOP_ANDROID_UI_DESIGN_SYSTEM_AND_PROMPTS.md` 全文，并将其作为设计与验收基线。
3. 阅读 `README.md`、`MVP_SPEC.md`、所有 `android-app/app/src/main/java/com/androidagent/client/` Kotlin 文件、全部 layouts、menus、drawables、colors、themes、dimens、strings 和现有单测。
4. 查看 `android-app/screenshots/*.png`。
5. 当前工作区有用户未提交改动。不得覆盖、回退、重置、删除或格式化无关改动；尤其保留现有 Event Normalizer、Timeline Builder、Timeline Adapter、Approval binder、Timeline Store 和通知相关实现。

硬性技术约束：
- 继续 Kotlin + XML + ViewBinding + RecyclerView + Material Components。
- minSdk 保持 24。
- 不迁移 Jetpack Compose，不做全仓架构重写。
- 不在手机保存模型 API Key；Token 继续使用 Keystore 安全保存。
- Release 继续要求 HTTPS；不削弱 APK SHA-256/签名/包名校验。
- 不为 UI 修改服务端 canonical event/approval contract；如发现契约问题，只做最小兼容并增加测试。
- Adapter 不直接解析原始 JSONObject；所有事件必须经过唯一 Normalizer/Timeline Builder。
- 不调用真实模型和外网，使用 fixture/MockWebServer/fake server。

视觉 token：
按设计文档第 5 节实现 Signal Workbench light/dark palette，替换默认 Material 紫色。使用系统字体、8dp grid、12dp card radius、48dp touch targets。支持系统深浅色和字体缩放，状态不能只靠颜色。

必须实现：

一、根导航和连接
- 把 MainActivity 收敛为连接 gate + root app shell，不再在一个长页面混放连接、项目、Prompt、APK、日志。
- Compact 底部导航：项目、活动、待处理；Settings/Connection 在 top app bar。
- Medium 使用 Navigation Rail + list-detail；Expanded 使用 rail + list + detail/supporting pane。
- 连接 onboarding 只突出服务地址、访问 Token、测试并连接；registration token 放 Advanced。
- 已连接设置显示主机、用户、TLS/连接状态、最后同步；支持编辑/重连/断开。
- Token 校验失败不能清空已有安全凭证；输入错误有 field-level message。

二、Projects 和 Project Hub
- Projects 显示 active jobs、最近项目、最后任务状态/时间/APK；Extended FAB 新建项目。
- 支持项目 overflow 和二次确认删除，保留后端现有删除语义。
- Project Hub 的主体是 Conversation list。
- 顶部优先显示等待审批、继续最近 Conversation、新 APK。
- 工作区、改动与 Checkpoint、构建与 APK 使用次级 list rows，不再四个同权大按钮。
- Conversation row 显示标题、摘要、状态、更新时间、待审批 badge，技术 ID 只进详情。

三、Activity 和 Approval Inbox
- 新增跨项目 Activity destination，按进行中/最近/失败筛选。
- awaiting approval 排在最前；running 显示当前动作而非伪百分比；failed 显示可操作错误摘要。
- 新增 Approval Inbox，按项目分组；显示意图、风险、范围、等待时间。
- 命令/网络/文件/安装/破坏性审批采用统一组件和 canonical approval id。
- destructive 审批必须进详情确认；禁止批量全部允许。
- 其他端已处理时原位更新“已在其他设备处理”，不显示误导错误。

四、Conversation
- 保留并完善 RecyclerView timeline；不能退回 StringBuilder/TextView。
- App bar 显示标题和 canonical 状态/耗时；overflow 提供任务详情、Diff、构建日志、APK、重命名、归档。
- 用户消息右侧品牌 soft bubble；Assistant answer 无厚重外卡、16sp/良好行高。
- Work group 完成后默认折叠；running 只展开当前 step；failed 自动展开错误；status lines 分组。
- changes/build/APK result rows 是主要结果；provider/model/tokens 放 Task Details bottom sheet。
- Streaming 同一 Assistant row 原地更新并节流 Markdown 重排。
- 加载历史保留 scroll anchor；离底部 96dp 后停止自动跟随，显示 `↓ N 条新内容`。
- 前后台和 configuration change 后恢复 project/conversation/job/cursor/draft/scroll/expanded state。

五、Composer、IME 和 Approval Dock
- composer 固定在 IME 与 navigation bar inset 上方，输入框 52–144dp 自增长。
- 运行中显示 compact mode tabs：引导当前任务 / 后续追问；Stop 为独立 48dp danger button。
- awaiting approval 时 composer 上方只显示 40–48dp dock；完整审批进入 modal bottom sheet，避免与键盘挤压内容。
- 发送失败保留草稿；离线时说明任务可能仍在电脑运行并自动重连。
- 正确处理 edge-to-edge、system bars、display cutout、gesture navigation 和 IME animation/insets。

六、Markdown 和代码
- 正文、标题、列表、引用、链接、表格、inline code、fenced code 完整可读。
- 表格横向滚动；代码块有语言、复制、16 行以上折叠、水平滚动。
- 复制成功用 Snackbar。
- 2.0x 字体不丢控件、不截断关键文本、不依赖固定高度。

七、Diff
- 将当前单 TextView 改为 mobile unified diff：文件 selector、文件统计、hunk header、固定行号、逐行新增/删除/上下文样式。
- 使用低饱和红绿背景，同时显示 +/-，不能只靠颜色。
- 长行横向滚动，大 Diff 按文件分页/继续加载，截断有说明。
- Restore checkpoint 是次级危险操作；确认显示文件数、dirty conflict 策略；冲突显示可复制列表。

八、Build Log
- 顶部 summary 显示状态、duration、关键错误数。
- 摘要/完整日志分层；支持首错跳转、搜索、复制和让 Agent 修复。
- 大日志不能一次塞入单 TextView 导致卡顿。

九、APK
- Artifact card 显示 App、包名、version、size、time、SHA-256、签名摘要。
- 完整状态机：未生成、可下载、下载中、校验中、已下载、校验失败、可安装、已安装。
- 主按钮随状态变化；分享/日志/复制 SHA 为次要操作。
- APK 下载、hash 和 package signature 校验放后台线程；失败禁止安装。
- 未知来源权限返回后正确刷新并继续明确流程。

十、自适应与无障碍
- 使用 WindowSizeClass 或 `layout/`、`layout-w600dp/`、`layout-w840dp/`，不要用 `isTablet`。
- Compact 单 pane；Medium list-detail；Expanded list + detail/supporting pane；compact height 横屏回退单 pane。
- TalkBack 顺序正确；所有交互图标有 contentDescription；装饰图标隐藏。
- 触控目标至少 48dp；颜色对比达到 WCAG/Android 建议；支持 reduced motion/系统 animation scale。

测试与验证：
- 扩展 AgentApi/Normalizer/TimelineBuilder/TimelineStore/Approval tests。
- 覆盖 connection error、Activity ordering、approval handled elsewhere、四类审批、streaming dedupe、history anchor、draft restore、IME/insets、Diff paging、large log、APK state machine。
- 运行 `./gradlew testDebugUnitTest assembleDebug`，优先离线依赖；若环境允许，运行 connected smoke。
- 不调用真实模型和外网。
- 生成并人工检查截图：360×800、412×915、600×960、840×900；light/dark；1.0x/1.3x/2.0x font；IME；approval；running；completed；failed；Diff；APK。
- 使用 Layout Inspector/截图确认无 system bar、navigation bar、IME 遮挡。
- 运行 `git diff --check`。

最终回复必须列出：
1. 根导航与页面结构。
2. Conversation、Approval、Diff、Build、APK 的行为。
3. compact/medium/expanded 适配。
4. 无障碍和 edge-to-edge 处理。
5. 测试命令、退出结果、APK 路径。
6. 截图绝对路径与人工检查结论。
7. 任何未完成项及原因。
```

---

## 16. 完整提示词 D：视觉回归与 UX 验收

```text
你是一名严格的 UI QA、无障碍审计员和 Developer Tools 产品设计评审。

请只做验证与问题报告，不修改代码，除非用户随后明确要求修复。

项目：`/Users/sakura/Android Agent`
设计基线：`docs/DESKTOP_ANDROID_UI_DESIGN_SYSTEM_AND_PROMPTS.md`

任务：
1. 阅读设计基线和当前双端代码。
2. 运行现有测试并记录命令、退出码和失败。
3. 为桌面端生成 1440×900、1024×768、900×700 和最窄支持宽度截图。
4. 为 Android 生成 360×800、412×915、600×960、840×900，light/dark，1.0x/1.3x/2.0x 字体截图。
5. 使用 fixture 覆盖：empty、running、streaming、approval command、approval network、destructive approval、completed+changes+build、failed、interrupted、offline/reconnect、Diff、large log、APK、IME。
6. 逐张视觉检查，不只依赖像素 diff。

桌面检查：
- Activity Bar 与 sidebar 是否仍重复导航。
- Title/Status Bar 是否争夺注意力。
- editor 最小宽度、Agent pane、overlay/focus mode 是否重叠。
- timeline 是否结果优先、工具组默认折叠、失败自动展开。
- approval 是否显示真实命令/域名/路径且操作明确。
- composer controls 在窄宽度是否可达。
- Monaco Diff、preview、bottom panel 是否互相遮挡。
- 键盘 focus、tooltip、ARIA、reduced motion。

Android 检查：
- Projects/Activity/Approvals 根导航是否清晰。
- Project Hub 是否以 Conversation 为主体，而不是按钮墙。
- Conversation 在 IME、gesture navigation、system bars 下是否无遮挡。
- 用户离底部后是否停止强制滚动并显示新内容提示。
- Approval Dock 与 bottom sheet 是否避免挤压。
- Markdown 表格/代码/长路径/长标题/2.0x 字体是否可用。
- Diff 是否有文件层级、行号、+/-、分页/截断说明。
- APK 状态与校验/安装流程是否可信。
- 48dp touch targets、TalkBack 顺序、状态非纯颜色。

输出格式：
- 先给总体结论与是否达到发布门槛。
- 问题按 P0/P1/P2 排序。
- 每项包含：证据截图、页面/状态、复现步骤、期望、实际、影响、建议。
- 对代码定位只在能确定根因时给文件与行号；不要猜测。
- 列出所有通过的验收项，避免只有负面清单。
- 不允许通过更新 screenshot baseline 让失败消失。
```

---

## 17. 最终验收标准

设计成功不等于“更漂亮”，而是满足以下结果：

- 用户在 3 秒内能识别当前项目、Conversation、任务状态和是否需要自己处理。
- 桌面端打开 Diff 不丢线程上下文，Android 打开审批不丢输入草稿。
- 完成任务的时间线明显比运行过程更安静，失败与审批明显更突出。
- Android 首页不再是连接/Prompt/日志混排的长调试表单。
- 桌面端不再出现 Activity Bar 与 sidebar tabs 的重复一级导航。
- 双端共享同一状态词、颜色与审批风险语法。
- 断线后不丢内容、不重复消息、不假装操作成功。
- 桌面端 4 个 viewport、Android 5 类屏幕/字体/主题状态无关键遮挡。
- 键盘、TalkBack、2.0x 字体、低动画设置下核心流程仍可完成。
- 所有现有核心能力保留，安全边界不因视觉重构而削弱。
