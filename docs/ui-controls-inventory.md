# Android Agent 客户端控件与按钮清单

> 覆盖桌面端（Electron）与 Android 端全部页面的控件种类与实例盘点。
> 生成日期：2026-08-23

---

## 目录

- [一、桌面端（Electron 单窗口 · IDE 布局）](#一桌面端electron-单窗口--ide-布局)
- [二、Android 端（26 个页面）](#二android-端26-个页面)
- [三、两端对照要点](#三两端对照要点)
- [四、汇总统计](#四汇总统计)

---

## 一、桌面端（Electron 单窗口 · IDE 布局）

桌面端是一个多区域单窗口应用，无独立"页面路由"，由以下 17 个功能区域组成。

### 1. 顶部标题栏

| 控件种类 | 实例 | 用途 |
|---|---|---|
| 分段切换按钮组 | `focusSwitch`（代码/Agent/审阅 三态） | 焦点模式切换 |
| 图标按钮 | `btnCommandPalette`（⌘） | 打开命令面板 |
| 状态胶囊 | `connPill`（连接状态） | 展示型 |
| 品牌标识/窗口标题 | `brand-mark` / `windowTitle` | 展示型 |

### 2. 活动栏（左侧图标栏）

| 控件种类 | 实例 | 用途 |
|---|---|---|
| 图标按钮 ×5 | 资源管理器 / 搜索 / 对话 / 任务 / 待处理审批 | 视图切换（`aria-current` 表示激活态） |
| 图标按钮 | `btnToggleAiActivity`（AI 对话 ⌘L） | 显隐 AI 面板 |
| 图标按钮 | `btnActivitySettings` | 打开设置对话框 |
| 徽章 | `pendingApprovalBadge` | 待审批计数 |
| 分隔线 | `activity-spacer` | 分隔审批与设置按钮 |

### 3. 侧边栏（五个视图）

#### 3.1 资源管理器视图

| 控件 | 标识 | 用途 |
|---|---|---|
| 图标按钮 | `btnNewFile`（+） | 新建文件 |
| 图标按钮 | `btnRefreshTree`（↻） | 刷新文件树 |
| 图标按钮 | `btnCollapseTree`（▤） | 折叠全部文件夹 |
| 文件树 | `fileTree`（动态 `.tree-item`） | 目录项/文件项，含展开箭头 `.tree-twistie` 与文件图标 `.tree-icon` |

#### 3.2 搜索视图

| 控件 | 标识 | 用途 |
|---|---|---|
| 文本输入框 | `searchInput`（placeholder=搜索代码…） | 输入关键词 |
| 图标按钮 | `btnSearch`（↻） | 触发搜索 |
| 结果列表 | `searchResults`（动态 `.search-result`） | 命中文件路径 + 行号 |

#### 3.3 对话视图

| 控件 | 标识 | 用途 |
|---|---|---|
| 图标按钮 | `btnSidebarNewConversation`（＋） | 新建对话 |
| 图标按钮 | `btnArchiveConversation`（▤） | 归档当前对话 |
| 动态图标按钮 | `data-action="rename"`（✎）/ `data-action="archive"` | 行内重命名/归档 |
| 对话列表 | `conversationList`（动态 `.conversation-item`） | 标题 + 操作区 + 空状态 |

#### 3.4 任务视图

| 控件 | 标识 | 用途 |
|---|---|---|
| 图标按钮 | `btnRefreshJobs`（↻） | 刷新任务列表 |
| 任务列表 | `jobList`（动态 `.job-item`） | 状态标识 + ID + 状态文字 + 空状态 |

#### 3.5 审批收件箱视图

| 控件 | 标识 | 用途 |
|---|---|---|
| 图标按钮 | `btnRefreshApprovals`（↻） | 刷新待处理审批 |
| 幽灵按钮 | 「拒绝」（`.ghost-btn.sm`） | 拒绝审批 |
| 主按钮 | 「允许本次」（`.primary-btn.sm`） | 允许审批 |
| 幽灵按钮 | 「查看」 | 打开审批详情弹窗 |
| 高风险标签 | `.approval-risk`（高风险 · 需查看详情） | 风险提示 |
| 计数/空状态 | `approvalInboxCount` / `.sidebar-empty` | 审批数量与空态 |

### 4. 编辑器面板

| 区域 | 控件 |
|---|---|
| 标签页 | 动态标签按钮（`.tab`，active/dirty 状态）+ 关闭图标 `×`（支持中键关闭） |
| 面包屑 | 路径片段 `.crumb` + 分隔符 `›` + 预览切换幽灵按钮（`btn-preview-toggle`） |
| Monaco 编辑器 | `monacoHost`（minimap、括号着色、缩进辅助线、平滑滚动，主题跟随亮/暗） |
| Diff 视图 | Monaco DiffEditor + 幽灵按钮 ×4：`btnDiffLayout` 并排切换、`btnDiffWhitespace` 忽略空白、`btnAcceptDiff` 应用、`btnRejectDiff` 拒绝 + 关闭图标按钮 `btnCloseDiff` + 文件切换下拉 `diffFileSwitcher` + 截断警告 `diffTruncatedWarn` + 二进制提示 `diffNotice` |
| 布局预览 | 手机外壳（`previewStage` / `.phone-frame` / `.phone-notch` / `previewScreen`）+ 刷新幽灵按钮 `btnRefreshPreview` + 关闭图标按钮 `btnClosePreview` + 精度徽章「近似」+ 元信息 `previewMeta` |
| 欢迎页 | 链接按钮 ×3：`btnWelcomeOpen` 打开文件夹、`btnWelcomeNewProject` 新建项目、`btnWelcomeConnect` 连接 Agent + 动态最近项目按钮 `.welcome-recent-item` + 快捷键列表 |
| 底部面板 | 底部标签 ×4（终端/问题/输出/构建）+ 新建终端图标按钮 `btnNewTerminal` + 动态终端标签 `.terminal-tab` + 问题列表 `problemList` + 输出/构建日志 `<pre>` + 拖拽调整条 `bottomResize` |

### 5. 状态栏

| 控件 | 标识 | 用途 |
|---|---|---|
| 展示型 ×6 | `statusBranch` 分支、`statusErrors` 错误、`statusCursor` 光标位置、`statusLang` 语言、`statusEncoding` 编码、`statusEol` 换行符 | 编辑器状态 |
| 可点击按钮 | `statusConn`（Agent · 未连接） | 点击打开连接设置 |

### 6. AI 面板（核心交互区）

#### 6.1 头部

| 控件种类 | 标识 | 用途 |
|---|---|---|
| 下拉选择 | `projectSelect` / `conversationSelect` | 选择项目/对话 |
| 图标按钮 | `btnNewChat`（＋）、`btnAiMore`（⋯）、`btnCloseAi`（×） | 新任务/更多/关闭面板 |
| 任务控制按钮 | `btnPauseJob` 暂停、`btnResumeJob` 继续、`btnHeaderStop` 停止（危险样式） | 任务生命周期控制 |
| 状态指示 | `aiStatusDot` 状态点 + `aiStatusText` 状态文字 + `aiMetaExtra` | 连接与任务状态 |

#### 6.2 时间线

| 控件 | 用途 |
|---|---|
| 「加载更早记录」按钮 | 分页加载历史 |
| 复制按钮 ×2 | 复制用户消息 / 复制工具输出 |
| 展开/收起头 ×3 | 工具 `tl-tool-head` / 工作组 `tl-work-head` / 计划 `tl-plan-head` |
| 「打开文件」幽灵按钮 | 打开工具关联文件 |
| 「恢复到此检查点」幽灵按钮 | 检查点回滚 |
| 文件列表展开切换 `tl-changes-toggle` + 动态文件项 `tl-change-file` | 变更文件浏览 |
| 审批操作按钮（允许/拒绝）+ 审批栏 `approvalDock` | 审批处理 |
| 上下文芯片 `.composer-chip`（含移除 ×） | 附加上下文展示 |
| 流式光标 `.tl-stream-caret` | 流式输出指示 |

#### 6.3 输入栏（Composer）

| 控件种类 | 标识 | 用途 |
|---|---|---|
| 多行文本域 | `promptInput`（placeholder=描述要完成的任务） | 任务描述输入 |
| 下拉选择 ×2 | `modelSelect` 模型、`runModeSelect` 权限模式 | 模型与权限配置 |
| 幽灵按钮 | `btnAddContext`（＋ 上下文） | 打开上下文菜单 |
| 上下文菜单项 ×3 | 当前文件 / 编辑器选区 / 工作区文件夹 | 添加上下文 |
| 模式按钮 ×2 | `btnSteer` 引导当前任务、`btnFollowUp` 本轮结束后追问 | 发送模式 |
| 危险按钮 | `btnStop`（停止） | 停止任务 |
| 主按钮 | `btnSend`（发送） | 发送任务 |

#### 6.4 更多设置菜单

| 控件 | 用途 |
|---|---|
| 历史任务下拉 `jobHistory` + 自动降级复选框 `autoFallback` | 任务配置 |
| 启动服务幽灵按钮 `btnStartServer` / 停止服务危险按钮 `btnStopServer` | 服务管理 |
| 复制手机 URL 图标按钮 `btnCopyPhoneUrl` + 连接设置幽灵按钮 `btnOpenSettings` | 连接辅助 |

### 7. 全局浮层与对话框

| 控件 | 内容 |
|---|---|
| 命令面板 | 模态遮罩 + 输入框 `paletteInput` + 动态命令/文件列表 + 键盘导航（Enter/Esc/↑↓） |
| 设置对话框 | 主题下拉（跟随系统/浅色/深色）+ 输入 ×5（服务地址 url、邮箱、密码 ×2、配对密钥）+ 按钮 ×4（登录账号/配对/连接/取消） |
| 审批详情对话框 | 拒绝幽灵按钮 + 允许主按钮（高风险变危险样式）+ `details/summary` 技术详情折叠 |
| 新建项目对话框 | 输入 ×2（项目名、包名）+ 创建/取消 |
| 重命名对话对话框 | 输入 ×1 + 保存/取消 |
| Toast 通知 | `toast` 全局通知 |
| 分割线 Sash ×3 | 侧边栏/AI 面板/预览宽度拖拽 |
| xterm 终端 | 多实例 + 多标签 + Fit/WebLinks 插件，主题跟随 |

### 桌面端控件种类汇总

- **按钮类**：图标按钮、幽灵按钮、主按钮、危险按钮、分段切换组、标签页按钮、链接按钮、菜单项
- **输入类**：单行输入、密码输入、多行文本域、搜索框、url/email 专用输入、隐藏字段
- **选择类**：下拉 select ×6（项目/对话/模型/权限/主题/Diff 文件切换）、复选框
- **展示类**：文件树、面包屑、卡片、徽章、状态胶囊、空状态、Toast、模态对话框、菜单、进度指示、流式光标、分割线
- **专业控件**：Monaco 编辑器、Monaco DiffEditor、xterm 终端、命令面板、布局预览画布

---

## 二、Android 端（26 个页面）

### A. 认证流程（4 页）

#### 登录页 `activity_main.xml`

| 类别 | 控件 |
|---|---|
| 按钮 | MaterialButton ×5：filled「登录」「测试并连接」、outlined「注册」「注册用户」、text「高级设置」；可点 TextView「忘记密码」 |
| 输入 | TextInputEditText ×5：邮箱、密码（password_toggle）、服务器 URL、API Token、注册密钥 |
| 展示 | NestedScrollView、TextInputLayout ×5（含 startIcon）、状态 TextView、可折叠高级面板 |
| 对话框 | AlertDialog 注册确认 |

#### 注册页 `activity_register.xml`

| 类别 | 控件 |
|---|---|
| 按钮 | filled「注册」、text「已有账号去登录」 |
| 输入 | ×3：邮箱、密码、确认密码（均 password_toggle） |
| 选择 | MaterialCheckBox 条款勾选 |
| 展示 | MaterialToolbar、LinearProgressIndicator 密码强度（max=3）、强度文字 |

#### 邮箱验证页 `activity_verify_email.xml`

| 类别 | 控件 |
|---|---|
| 按钮 | outlined「重新发送」、filled「验证并继续」 |
| 输入 | 6 位数字验证码（居中 22sp，digits 限定） |
| 展示 | Toolbar、说明文字（含邮箱）、邮件图标、信息 Banner |

#### 忘记密码页 `activity_forgot_password.xml`

| 类别 | 控件 |
|---|---|
| 按钮 | outlined「发送重置码」、filled「重置密码」 |
| 输入 | ×3：邮箱、验证码、新密码 |
| 展示 | Toolbar、TextInputLayout ×3 |

### B. 主框架与列表（4 页）

#### 主容器 `activity_main_nav.xml`

| 类别 | 控件 |
|---|---|
| 导航 | BottomNavigationView（4 tab：项目/动态/待处理/我的）、≥600dp 切换 NavigationRailView |
| 菜单 | Toolbar 菜单 ×2：刷新 `action_refresh`、设置 `action_settings` |
| 展示 | FrameLayout 内容区（Fragment 替换）、BadgeDrawable 待审批角标、侧栏头（应用图标） |

#### 项目列表 `fragment_projects.xml`

| 类别 | 控件 |
|---|---|
| 按钮 | ExtendedFloatingActionButton「新建项目」、filled「新建项目」（空态）、outlined「配置模型 API」、可点 TextView ×2（配置 API / Banner 设置） |
| 输入 | 搜索框（imeOptions=actionSearch） |
| 展示 | RecyclerView、MaterialCardView 项目卡（名称/APK 角标/状态徽章/包名/APK 信息）、空状态图、连接丢失 Banner、API 未配置 Banner、月度用量文字（可点击跳转） |
| 对话框 | AlertDialog 创建项目（dialog_create_project：项目名 + 包名输入）、长按删除确认 |

#### 动态流 `fragment_feed.xml`

| 类别 | 控件 |
|---|---|
| 展示 | RecyclerView、分区标题（Active/Recent/Failed）、任务卡（状态圆点/标题/时间/徽章/摘要/LinearProgressIndicator 不定态）、空状态 |

#### 审批页 `fragment_approvals.xml`

| 类别 | 控件 |
|---|---|
| 选择 | ChipGroup 单选筛选 ×6：全部/命令/网络/文件/安装/破坏性（Filter Chip） |
| 展示 | RecyclerView、Inbox 计数、空状态 |
| 浮层 | BottomSheetDialog 审批详情（含动态复制 payload 按钮）+ Toast |

### C. 核心工作页（3 页）

#### 项目详情 `activity_project_detail.xml`

| 类别 | 控件 |
|---|---|
| 按钮 | tonal「查看 APK」、FAB 新对话（ic_send）、入口卡片 ×3（工作区文件/改动/构建 APK） |
| 展示 | Toolbar、项目名/包名/状态、待审批黄卡（默认隐藏）、继续对话卡、APK 卡、对话 RecyclerView、无对话提示 |

#### 对话页 `activity_conversation.xml`

| 类别 | 控件 |
|---|---|
| 按钮 | FAB ×2（发送 `ic_send` / 停止 `ic_stop`）、tonal「跳到最新」、Toolbar 菜单 ×7（任务详情/构建日志/改动对比/APK/暂停/恢复/停止）、可点 TextView ×2（连接详情/清除草稿） |
| 输入 | 多行输入 `editPrompt`（maxLines=6，草稿自动保存恢复） |
| 选择 | ChipGroup 单选：引导/追问模式 Chip ×2；建议 Chip ×3（深色登录/生物识别/性能） |
| 展示 | RecyclerView 时间线、断连 Banner、审批等待栏（点击弹 BottomSheet）、草稿提示行、空状态（含建议 Chip） |
| 浮层 | BottomSheetDialog 任务详情（view_job_details：Job ID/Provider/Model/Tokens/耗时/Prompt）、BottomSheetDialog 审批详情 |

#### 文件浏览 `activity_file_browser.xml`

| 类别 | 控件 |
|---|---|
| 按钮 | tonal「文件」、保存按钮（脏状态启用）、outlined「上级目录」 |
| 输入 | 多行编辑器 `editFileContent`（textMultiLine\|textNoSuggestions） |
| 展示 | DrawerLayout 抽屉、文件 RecyclerView、当前路径、编辑器状态（只读/可编辑/脏） |
| 对话框 | AlertDialog 未保存确认（保存/放弃/取消） |

### D. 设置类页面（9 页）

| 页面 | 按钮 | 输入 | 选择 | 展示 |
|---|---|---|---|---|
| **连接设置** | ×3：重新连接、outlined 编辑连接、text 红字断开 | — | — | 连接信息卡（地址/用户/协议/同步时间）、安全提示 Banner、断开确认 AlertDialog |
| **模型 API** | 「添加/更改密钥」 | — | — | 就绪状态、默认配置、Provider 状态、自定义 Base URL 设置行、云存储 Banner |
| **模型密钥** | 「保存」、outlined「测试连接」「删除密钥(红字)」 | 密钥输入（password_toggle） | — | 云存储提示 |
| **通知设置** | 「启用通知」、text「稍后」 | — | MaterialSwitch ×4（任务完成/失败/审批/配额） | 各开关描述文字 |
| **设备管理** | outlined「登出其他设备」+ 动态每设备登出按钮 | — | — | 当前设备卡、「本机」徽章、在线状态、动态设备卡列表、登出提示 Banner |
| **修改密码** | 「保存新密码」 | ×3：旧/新/确认密码 | — | 密码强度进度条 + 文字、不存储提示 Banner |
| **账号安全**（动态构建） | 设置行 ×6：编辑昵称/修改密码/绑定邮箱/登出/删除账号（危险区） | 对话框内：昵称输入、密码输入 | 对话框内：MaterialCheckBox 删除确认 | 信息卡（昵称/邮箱/可复制账号 ID）、危险区红色标题 |
| **Token 用量** | outlined ×2「查看详情」「管理 API」 | — | ChipGroup 时间范围 ×4：今天/7 天/30 天/本月 | 用量数字、LinearProgressIndicator、按 Provider 用量、延迟提示 |
| **用量详情**（动态构建） | — | — | — | 每任务 MaterialCardView + Provider/Model/输入/输出/合计明细 |

### E. 构建与产物页（3 页）

| 页面 | 按钮 | 输入 | 展示 |
|---|---|---|---|
| **APK 页** | 「下载并验证」、tonal「安装 APK」、outlined「分享 APK」「更多操作」 | — | 验证 Banner、APK 信息卡（名称/包名/版本/大小）、SHA-256 卡、签名信息、安装确认 AlertDialog |
| **构建日志** | 「让 Agent 修复」、text「复制路径」 | — | 失败红卡（首个错误）、TabLayout（关键错误/完整日志）、动态错误列表、日志全文、日志路径 |
| **Diff 页** | 「恢复到快照」 | — | 文件列表 RecyclerView、着色 Diff 文本（可选中）、新增/删除/上下文行数统计、快照卡、恢复确认/冲突/错误三种 AlertDialog |

### F. 列表条目控件（时间线复用）

| 条目 | 控件 |
|---|---|
| 用户消息 `item_user_message` | 右对齐气泡 TextView |
| AI 消息 `item_assistant_message` | 子段容器 + 流式提示（默认隐藏） |
| 工具步骤 `item_tool_step` | 图标 + 摘要 + 状态耗时 + 展开箭头 + 元数据/输出代码块 + text「复制」按钮 |
| 工作组 `item_work_group` | 状态点 + 标题 + 摘要 + 展开箭头 + 子步骤容器 |
| 代码块 `item_code_block` | 语言标签 + text 复制按钮 + 内容 + 展开/折叠按钮 |
| 改动摘要 `item_changes_summary` | 数量 + 文件列表 + text「查看改动」 |
| 状态行 `item_status_line` | 展开箭头 + 状态文字 + 计数 |
| 回合结果 `item_turn_result` | 结果状态 + 耗时 |
| 空状态 `item_empty_state` | 图标 + 文字 |
| 错误消息 `item_error_message` | 标题 + 详情 + outlined「查看错误详情」+「让 Agent 修复」 |
| 加载历史 `item_loading_history` | ProgressBar + 「加载更早」 |
| 分区标题 `item_section_header` | 标题文字 |
| 对话卡 `item_conversation` | 标题 + ImageButton 更多(⋮) + 摘要 + 状态点 + 等待中徽章 + 时间（长按/菜单重命名归档） |
| 审批卡片 `item_approval` | 图标 + 标题 + 风险标签(红) + 字段 + 展开详情 + 按钮 ×3：outlined「拒绝」、filled「仅允许本次」、text「始终允许」 |
| 收件箱审批条目 `item_inbox_approval` | 类型/风险/等待时间/来源/意图 + text「拒绝」+ tonal「通过」 |
| 设置行 `item_settings_row` | 标题 + 副标题 + 右箭头图标，整行可点 |
| 项目卡 `item_project` | 名称 + APK 角标 + 状态徽章 + 包名 + APK 信息，整卡可点 |
| 动态流任务卡 `item_activity_job` | 状态点 + 标题 + 时间 + 徽章 + 摘要 + 不定态进度条 |
| Diff 文件条目 `item_diff_file` | 文件路径 |
| 文件条目 `item_file_entry` | 文件/文件夹行 |

### G. 全局浮层

- **AlertDialog** ×10+：注册确认、删除项目、断开连接、安装确认、恢复快照（确认/冲突/错误）、未保存确认（保存/放弃/取消）、编辑昵称、删除账号、创建项目
- **BottomSheetDialog** ×2：任务详情（view_job_details）、审批详情
- **Toast**：全端错误与结果提示
- **BadgeDrawable**：底部导航待审批角标

### Android 端控件种类汇总

- **按钮类**：MaterialButton 四变体（filled / outlined / text / tonal）、FloatingActionButton、ExtendedFloatingActionButton、ImageButton、可点击 TextView / CardView / 设置行
- **输入类**：TextInputEditText（textEmailAddress / textPassword / textUri / number / textMultiLine 变体，均配 TextInputLayout + password_toggle）
- **选择类**：MaterialCheckBox、MaterialSwitch、Chip 两变体（Filter 筛选 / Assist 建议）、ChipGroup（单选/多选）、TabLayout
- **展示类**：RecyclerView、MaterialCardView、MaterialToolbar、BottomNavigationView、NavigationRailView、DrawerLayout、LinearProgressIndicator、ProgressBar、状态点、徽章 TextView、Banner、空状态、NestedScrollView、CoordinatorLayout
- **浮层**：AlertDialog、BottomSheetDialog、Toast、BadgeDrawable
- **动态构建**：AccountSecurityActivity、TokenUsageDetailActivity、AboutActivity、PermissionsActivity、DevicesActivity 使用代码构建 UI（MaterialCardView + TextView + 设置行 include + 对话框）

---

## 三、两端对照要点

| 维度 | 桌面端 | Android 端 |
|---|---|---|
| 主按钮体系 | 主按钮 / 幽灵按钮 / 危险按钮 / 图标按钮（CSS 自绘） | Material filled / outlined / text / tonal + FAB |
| 任务控制 | 暂停 / 继续 / 停止（头部 + 输入栏双入口） | 菜单暂停 / 恢复 / 停止 + FAB 停止 |
| 审批操作 | 拒绝（幽灵）/ 允许本次（主）/ 详情弹窗 | 拒绝（outlined）/ 仅允许本次（filled）/ 始终允许（text） |
| Diff | Monaco DiffEditor（并排/内联切换、应用/拒绝） | 着色文本 + 文件列表 + 恢复快照 |
| 终端 | xterm 多实例多标签 | 无（远程执行为主） |
| 模式切换 | 引导 / 追问 按钮组 | 引导 / 追问 Filter Chip |
| 导航 | 活动栏 + 侧边栏视图 + 底部面板标签 | BottomNav（4 tab）/ NavigationRail + Fragment |
| 上下文附加 | ＋上下文菜单（文件/选区/文件夹）+ 芯片 | 无（输入即上下文） |
| 主题切换 | 主题下拉（系统/浅色/深色）+ `dataset.theme` | 系统跟随（Material You） |
| 密码强度 | 无 | LinearProgressIndicator + 文字（注册/改密页） |

---

## 四、汇总统计

| 端 | 功能区域/页面 | 布局文件 | 控件实例（约） |
|---|---|---|---|
| 桌面端 | 17 个功能区域 | 1 个 index.html + 13 个 JS 模块 | 90+ |
| Android 端 | 26 个页面 | 36 个布局 XML | 230+（含动态构建） |

### Android 端控件计数明细

| 类别 | 数量 |
|---|---|
| MaterialButton（四变体合计） | 40+ |
| FloatingActionButton / ExtendedFAB | 3 |
| ImageButton | 1+ |
| 可点击 TextView | 10+ |
| TextInputEditText | 20+ |
| MaterialCheckBox | 2 |
| MaterialSwitch | 4 |
| Chip（Filter/Assist） | 15 |
| ChipGroup | 4 |
| RecyclerView | 7 |
| LinearProgressIndicator / ProgressBar | 6 |
| MaterialCardView | 25+ |
| MaterialToolbar | 18 |
| BottomNavigationView / NavigationRailView | 各 1 |
| DrawerLayout | 1 |
| TabLayout | 1 |
| AlertDialog（动态） | 10+ |
| BottomSheetDialog（动态） | 2 |
| BadgeDrawable（动态） | 1 |
| Toast | 多处 |
