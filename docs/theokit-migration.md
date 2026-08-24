# TheoKit UI 组件库迁移总结

> 源仓库：`/Users/sakura/theokit-ui`（`@theokit/ui` 1.4.1，React 组件库，Violet Forge 设计系统）
> 目标：Android Agent 桌面端（Electron + vanilla JS）与 Android 端（Kotlin 原生）
> 迁移范围：**108 个组件，全量迁移**，两端各建独立实现，共享同一套设计令牌

## 一、迁移策略

| 端 | 技术路线 | 说明 |
|---|---|---|
| 桌面端 | vanilla JS 组件函数 + 官方 dist CSS | 桌面客户端为 Electron 原生 JS 项目，不引入 React；直接复用 `@theokit/ui` 的 `tokens.css`/`fonts.css`/`components.css`（npm 依赖），组件逻辑用 `h()` DOM 构建器 + `cn()` 类名工具重写 |
| Android 端 | Kotlin 自定义 View | React/CSS 无法直接复用；按组件家族实现 Kotlin 视图构建函数，设计令牌翻译为 `TheoTokens` 明/暗双调色板 |

两端组件清单**逐一对齐**：Android `TheokitCatalogTest` 内置桌面端 108 个组件名清单，单元测试强制校验两端集合完全一致。

## 二、桌面端产物（`desktop/src/`）

| 文件 | 行数 | 内容 |
|---|---|---|
| `theokit/theokit-core.js` | 152 | `h()` DOM 构建器、`cn()` 类名工具、`Theokit` 命名空间 |
| `theokit/theokit-icons.js` | 150 | 图标集（Lucide 风格 SVG path） |
| `theokit/theokit-primitives.js` | 1917 | 基础组件（Agent 状态/事件、聊天、Composer、审批等） |
| `theokit/theokit-primitives-b.js` | 2386 | 基础组件续（Diff、工具调用、计划、会话、模型用量、基础设施、规则/记忆等） |
| `theokit/theokit-composites.js` | 2542 | 复合组件（ChatMessageBranch 分支导航、CodeReviewPanel、SlideDeck 等） |
| `theokit/theokit-extra.css` | 153 | 补充 Tailwind 工具类与自定义样式（`prose-theo` 等） |
| `theokit/theokit-showcase.js` + `-init.js` | 764 | 组件总览页面（15 个分类导航 + 全组件实况演示） |
| `theokit-showcase.html` | 112 | showcase 入口页（CSP 白名单脚本加载） |
| `tests/theokit.test.js` | — | 单元测试：108 组件渲染矩阵、交互行为、CSS 类覆盖率 |
| `tests/showcase-screenshot.js` | — | Playwright 截图（明/暗 × 顶/聊天/工具 6 张） |

## 三、Android 端产物（`android-app/app/src/main/java/com/androidagent/client/`）

| 文件 | 行数 | 内容 |
|---|---|---|
| `theokit/TheoTokens.kt` | 180 | 设计令牌：明/暗调色板、字号、间距、圆角、`tint()`/`mix()` 颜色工具 |
| `theokit/TheoUi.kt` | 271 | 基础原语：`theoText`/`theoButton`/`theoCard`/`theoRow`/`roundedBg` 等 |
| `theokit/TheoIcons.kt` | 266 | SVG path 图标渲染（80+ 图标，camelCase/kebab-case 双访问） |
| `theokit/TheoAgentViews.kt` | 420 | Agent 状态与事件家族（AgentErrorCard、TaskPlan、RunStatusPill 等） |
| `theokit/TheoChatViews.kt` | 457 | 聊天与 Composer 家族（ChatMessage、ChoicePrompt、分支导航等） |
| `theokit/TheoToolViews.kt` | 519 | 工具与 Diff 家族（DiffViewer 统一 diff 解析/着色、CodeBlock 等） |
| `theokit/TheoInfraViews.kt` | 632 | 基础设施家族（McpServerList、StabilityBundleViewer、MemoryEditor、RuleCard 等） |
| `theokit/TheokitCatalog.kt` | 580 | 108 组件注册表 + 演示数据 |
| `TheokitShowcaseActivity.kt` | 118 | 组件总览页（RecyclerView 列表，入口：主界面菜单"TheoKit 组件总览"，仅 debug 构建导出） |
| `test/.../TheokitCatalogTest.kt` | — | 18 项单元测试：组件数、双端清单一致性、diff 解析、令牌校验 |

## 四、验证结果

### 桌面端
| 检查 | 结果 |
|---|---|
| `npm run check`（语法） | 通过（含全部 theokit 文件） |
| `npm run test:unit` | **ALL CHECKS PASSED**：108 组件全渲染（0 错误）、showcase 2186 节点、315 个 CSS 类全覆盖、交互行为（分支切换/折叠/主题）通过 |
| `tests/showcase-screenshot.js` | 6 张截图重新生成并逐张目检（明/暗主题、按钮/Agent/聊天/Diff 各区） |

### Android 端
| 检查 | 结果 |
|---|---|
| `compileDebugKotlin` | BUILD SUCCESSFUL |
| `testDebugUnitTest` | 68 项测试 0 失败（其中 TheokitCatalogTest 18 项） |
| 模拟器实机目检 | 明/暗双主题 5 张截图：按钮 5 变体、Agent 状态、聊天分支导航（‹ 1 of 3 › + 圆点选择器）、RunStatusPill 5 态、Diff 等均正确渲染 |

### 收尾修正
- showcase 头部计数引用了不存在的 `window.Theo` 别名且把 `RUN_STATUS_META` 常量误计入（显示 109）；修正为 `window.Theokit` + 仅统计函数导出，现与两端组件数一致为 **108**。

## 五、迁移中的关键实现点

1. **设计令牌双轨**：桌面直接消费 CSS 变量（跟随系统主题切换）；Android 用 `TheoTokens.LIGHT/DARK` 静态调色板 + `theoPalette()` 按夜间模式取值。
2. **分支导航上下文解析**：桌面用 DOM 父级查找解析分支数据源；Android 以视图树遍历复刻同一交互（‹ › 翻页 + 圆点选择 + "1 of N" 标签）。
3. **Diff 解析器双实现**：桌面 JS 与 Android Kotlin 各实现 unified diff hunk 解析（新增/删除/上下文行分类 + ±统计），Android 端有专项单测。
4. **图标体系**：两端口径一致——桌面 inline SVG，Android `TheoIcons` path 转 `VectorDrawable` 风格绘制。
5. **窄屏适配**：Android showcase 中按钮行等横向内容包 `HorizontalScrollView`；`RADIUS_FULL`（-1）映射为 999dp 实现全圆角。

## 六、入口使用方式

- **桌面端**：打开 `desktop/src/theokit-showcase.html`；组件经 `window.Theokit.<Component>(props)` 调用。
- **Android 端**：主界面菜单 → "TheoKit 组件总览"（debug 构建）；代码中经 `theokit` 包扩展函数组合视图。
