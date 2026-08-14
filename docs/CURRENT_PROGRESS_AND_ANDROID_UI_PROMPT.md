# 当前开发进度与 Android 端 Agent 对话 UI 优化提示词

> 更新时间：2026-08-14
>
> 基线提交：`754bda9 feat: redesign desktop agent timeline and approvals`
>
> 远程分支：`origin/main`

## 1. 当前进度快照

### 1.1 已完成并推送

桌面端 Agent 时间线重构、命令审批展示以及相关后端协议改动已经提交并推送到远程 `main` 分支。

主要完成项：

- 桌面端从分散的聊天气泡、Plan、Tool、Approval 展示重构为连续任务时间线。
- 新增事件 Normalizer 和统一时间线渲染模块。
- 支持多轮 Turn、流式回答、工具调用与结果配对、审批状态归并、文件改动摘要。
- 改进 Markdown、表格、代码块和长工具输出展示。
- 完善 `run_mode` 透传以及命令、网络、文件等审批风险字段。
- 修复“审查改动”入口及 Diff 页面被欢迎页遮挡的问题。
- 增加桌面端单元测试、截图测试、Electron 冒烟测试脚本和后端审批测试。

已在本会话实际完成的验证：

| 验证项 | 结果 |
|---|---|
| `npm run check` | 通过 |
| `npm run test:unit` | 通过 |
| `npm run test:screenshot` | 通过 |
| 相关后端测试 | `77 passed` |
| 暂存内容检查 `git diff --cached --check` | 通过 |
| 推送状态 | `754bda9` 已同步到 `origin/main` |

本地 `.workbuddy/` 为代理工作记忆，没有提交或推送。

### 1.2 当前正在进行

当前后续任务为：

> 真实启动 Agent 服务和 Electron 桌面端，执行端到端冒烟验证并生成截图。

根据当前任务截图记录：

- 桌面端 check、单元测试、截图测试已经通过。
- 截图视觉检查发现并修复了 Diff 被欢迎页遮挡的问题。
- 截图所处任务还报告 Python 测试共 109 项通过；本文件上方的 77 项是本次提交前另行执行的相关测试子集，两者口径不同。
- Electron 冒烟测试进程当时仍在后台运行。
- 当时尚未发现 `desktop/tests/smoke-*.png` 产物。
- 终端中的 `zsh: no matches found: .../smoke-*.png` 来自 zsh 对空 glob 的处理，只能说明检查时尚无匹配截图，不能单独证明冒烟测试失败。

### 1.3 下一步

1. 等待真实 Agent 服务和 Electron 冒烟测试进程结束。
2. 检查进程退出码、服务日志、Electron 日志和未处理异常。
3. 检查所有 `smoke-*.png` 截图，重点验证：
   - 多轮对话压缩和展开。
   - 流式回答只显示一份最终内容。
   - 命令审批和网络审批。
   - “审查改动”打开 Monaco Diff 的完整链路。
   - 窄屏、长输出、失败状态和空状态。
4. 若冒烟测试失败，保留失败日志并修复根因，不得只更新截图基线掩盖错误。
5. 冒烟验证通过后，补充实际命令、退出码和截图路径，并提交新的验证结果。

## 2. Android 端现状与根因

原生手机端位于 `android-app/`，不是 `agent/web/`。

重点现状：

- `ConversationActivity.kt` 使用一个 `StringBuilder` 拼接全部事件。
- `activity_conversation.xml` 使用 `NestedScrollView + TextView` 显示事件和最终结果。
- 每次新事件都会调用 `fullScroll(View.FOCUS_DOWN)`，用户阅读历史时会被强制拉到底部。
- Job 实时事件多使用 `type + 扁平字段`，Conversation 历史事件使用 `event_type + payload`，Android 端尚未建立统一标准化层。
- `formatEvent()` 主要读取 `type`、`content` 和 `message`，无法完整读取历史事件中的 `payload.text_blocks`、`payload.content`、`turn_id` 等字段。
- 工具调用、工具结果、审批和最终回答没有按照 Turn 和稳定 ID 聚合。
- 审批卡默认展示截断后的原始 JSON。
- 暂停、恢复、停止、Diff、日志和 APK 操作集中在横向滚动按钮条中，不符合移动端 Agent 会话的操作层级。
- 会话列表只显示状态和 Conversation ID 前缀，缺少摘要、更新时间、未处理审批和运行状态的清晰展示。

## 3. 可直接执行的 Android 端优化提示词

```text
你是一名资深 Android 工程师、移动端产品设计师和 Agent 对话系统工程师。

请直接检查并修改当前项目的 android-app 原生 Android 客户端，重点优化 Agent 对话页面、回复展示、工具过程、审批交互和历史会话体验，使其在信息层级、阅读体验和交互方式上接近 Codex、Cursor 等成熟 Agent 产品。

这不是 Web 页面改造。

不要修改 agent/web/index.html、agent/web/app.js、agent/web/app.css，除非经过检查确认它们与 Android 客户端共享了必须修复的后端协议；即使如此，也不要修改无关 Web UI。

不要只输出建议。请完成代码实现、测试、构建和截图验证。

━━━━━━━━━━━━━━━━━━
一、正确的修改范围
━━━━━━━━━━━━━━━━━━

重点检查并修改：

Android 会话页面：

- android-app/app/src/main/java/com/androidagent/client/ConversationActivity.kt
- android-app/app/src/main/res/layout/activity_conversation.xml

事件和会话展示：

- android-app/app/src/main/java/com/androidagent/client/JobWatcher.kt
- android-app/app/src/main/java/com/androidagent/client/AgentApi.kt
- 新增必要的 Conversation UI Model
- 新增事件标准化和 Turn 聚合模块
- 新增 RecyclerView Adapter、ViewHolder、DiffUtil

建议新增或重构为：

- ConversationEventNormalizer.kt
- ConversationTimelineBuilder.kt
- ConversationTimelineAdapter.kt
- ConversationUiModel.kt
- item_user_message.xml
- item_assistant_message.xml
- item_work_group.xml
- item_tool_step.xml
- item_turn_result.xml
- item_error_message.xml
- item_changes_summary.xml
- item_loading_history.xml

审批相关：

- android-app/app/src/main/res/layout/item_approval.xml
- ConversationActivity 中的审批刷新和提交逻辑

历史会话相关：

- ProjectDetailActivity.kt
- ConversationAdapter.kt
- activity_project_detail.xml
- item_conversation.xml

主题和资源：

- res/values/themes.xml
- res/values-night/themes.xml
- res/values/colors.xml，如不存在则新增
- res/values/dimens.xml，如不存在则新增
- res/values/strings.xml
- 必要的 drawable、selector 和图标资源

测试：

- android-app/app/src/test
- 必要时新增 android-app/app/src/androidTest

不要把项目整体迁移到 Jetpack Compose。

当前项目已经使用 XML、ViewBinding、RecyclerView 和 Material Components，应在现有技术栈上完成高质量重构，避免为了 UI 优化引入大规模架构迁移。

开始修改前运行：

git status --short --branch

当前工作区可能存在用户未提交改动。不得覆盖、回退或格式化无关修改。

━━━━━━━━━━━━━━━━━━
二、首先修复事件协议问题
━━━━━━━━━━━━━━━━━━

当前 Android 客户端同时接收两种结构：

1. Job 实时事件，通常接近：

{
  "id": 123,
  "type": "tool_call",
  "name": "read_file"
}

2. Conversation 历史事件，通常接近：

{
  "id": "...",
  "seq": 12,
  "turn_id": "...",
  "event_type": "assistant_message",
  "role": "assistant",
  "payload": {
    "text_blocks": [
      {
        "type": "text",
        "text": "最终回答"
      }
    ],
    "is_final": true
  }
}

现有 formatEvent() 主要读取 type、content 和 message，无法可靠展示 event_type、payload、text_blocks 和 content block。

必须建立唯一的标准化层。

定义类似：

NormalizedConversationEvent(
    stableId,
    sequence,
    turnId,
    jobId,
    type,
    role,
    createdAt,
    payload,
    toolCallId,
    approvalId
)

标准化规则：

- type 优先读取 event_type，缺失时读取 type。
- payload 优先读取 payload 对象；Job 扁平事件则将其余字段标准化为 payload。
- stableId 优先使用事件 ID。
- 没有 ID 时使用 conversationId、turnId、seq、type 等生成稳定键。
- tool_call 和 tool_result 按 tool_call_id 关联。
- approval_required 和 approval_resolved 按 approval_id 关联。
- 所有 Conversation 历史和 Job 实时事件进入同一条标准化管道。
- UI 层禁止继续直接解析 JSONObject。
- 不得只按事件数组长度做增量判断。
- 不得依赖文案内容去重。

必须支持以下事件：

- user_message
- turn_started
- text_delta
- text
- assistant_message
- plan
- tool_call
- tool_result
- approval_required
- approval_resolved
- changes
- checkpoint
- usage
- provider_switch
- model_switch
- turn_completed
- completed
- failed
- canceled
- interrupted
- paused
- resumed
- recovery_note

未知事件保留在调试详情中，但不要默认显示原始 JSON 卡片。

━━━━━━━━━━━━━━━━━━
三、正确聚合用户问题和 AI 回答
━━━━━━━━━━━━━━━━━━

现有 StringBuilder + textEvents + textResult 方案必须移除。

不要继续把所有内容拼接到一个 TextView。

改为：

RecyclerView
  ├─ UserMessageItem
  ├─ WorkGroupItem
  │    ├─ ToolStep
  │    ├─ ToolStep
  │    └─ ApprovalStep
  ├─ AssistantMessageItem
  ├─ ChangesSummaryItem
  └─ ErrorItem

按照 turn_id 聚合多轮对话。

每个 Turn 包含：

- 用户问题。
- Agent 工作过程。
- Agent 最终回答。
- 文件改动摘要。
- 构建、错误或停止状态。

历史 Conversation 打开时，恢复全部 Turn，而不是仅恢复最后一个 Job 的文本日志。

回复聚合规则：

1. text_delta

增量追加到当前 Turn 的流式回答缓冲区。

2. text

如果协议中代表完整快照，则替换当前缓冲区，不能无条件追加。

3. assistant_message

读取以下可能结构：

- payload.text_blocks
- payload.content
- payload.text
- 扁平事件的 content
- 扁平事件的 message

如果 is_final=true，则作为本轮权威最终回答。

最终回答到达后，不能同时保留一份 delta 文本、一份 text 快照和一份 job.result，导致回答重复。

4. job.result

仅在没有 canonical assistant_message 时作为兼容回退。

5. user_message

支持从以下结构提取：

- payload.content 字符串
- payload.content block 数组
- 扁平 prompt
- JobInfo.prompt

同一轮用户问题不得重复显示。

━━━━━━━━━━━━━━━━━━
四、重新设计会话页面
━━━━━━━━━━━━━━━━━━

页面结构调整为：

┌────────────────────────┐
│ ←  对话标题       状态  ⋮ │
├────────────────────────┤
│                        │
│ 用户问题                │
│                        │
│ Worked for 2m 18s   ﹀  │
│                        │
│ Agent 最终回答           │
│                        │
│ 3 files changed    查看 │
│ Build passed            │
│                        │
├────────────────────────┤
│ 等待审批区域（如有）      │
├────────────────────────┤
│ ＋  输入任务……     发送/停止│
└────────────────────────┘

实现要求：

- Toolbar 固定顶部。
- RecyclerView 占据主要空间。
- Composer 固定底部。
- 审批请求出现在 Composer 上方，不得被软键盘遮挡。
- 使用 WindowInsets 处理状态栏、导航栏和 IME。
- 不要继续使用 fitsSystemWindows 作为唯一适配方式。
- 键盘弹出时输入框和发送按钮保持可见。
- 页面旋转或 Activity 重建时恢复当前 Conversation、Job、输入草稿、展开状态和滚动位置。

顶部只显示：

- 返回。
- 对话标题。
- 简短状态。
- 更多菜单。

Provider、模型、Job ID、Token 数量、耗时、构建日志、APK 等放入“任务详情”BottomSheet，不持续占用页面。

移除当前横向滚动的操作按钮条。

把操作整理为：

运行中：

- 主按钮：停止。
- 更多菜单：暂停、任务详情、构建日志、Diff、APK。

暂停时：

- 主按钮：继续。
- 更多菜单：停止、任务详情。

完成时：

- Composer 恢复发送。
- Diff、构建日志、APK 以结果卡片和更多菜单提供。

━━━━━━━━━━━━━━━━━━
五、用户消息和最终回复视觉设计
━━━━━━━━━━━━━━━━━━

用户问题：

- 使用低对比度圆角表面。
- 圆角约 16dp。
- 内边距 12～16dp。
- 不使用覆盖全屏宽度的巨大气泡。
- 长中文自然换行。
- 与下一部分保持 16～20dp 距离。

Agent 最终回答：

- 使用平面文档式排版。
- 不使用厚重聊天气泡。
- 正文字号至少 16sp。
- 行高约 1.5～1.65。
- 主文字使用高对比度颜色。
- 段落、列表和代码之间有清晰间距。
- 正文可选择和复制。

支持安全的原生 Markdown 渲染：

- 标题。
- 段落。
- 粗体和斜体。
- 有序、无序列表。
- 引用。
- 行内代码。
- 代码块。
- 链接。

优先使用兼容 minSdk 24、维护状态良好的原生 Markdown 方案。

不要使用 WebView 展示普通回答。

不要通过 Html.fromHtml 直接渲染未经处理的模型输出。

代码块要求：

- 使用等宽字体。
- 独立背景和圆角。
- 代码块内部横向滚动。
- 提供复制按钮。
- 超长代码块支持展开和收起。
- 长代码行不能撑破页面。
- 不允许整个 RecyclerView 横向滚动。

流式回答更新：

- 不要每收到一个字符就 notifyDataSetChanged。
- 对 delta 更新做约 50～100ms 批处理。
- 只更新当前 AssistantMessage ViewHolder。
- 最终回答到达后再执行完整 Markdown 渲染。
- 流式期间可显示低调光标或“正在回复”。

━━━━━━━━━━━━━━━━━━
六、Agent 工作过程折叠
━━━━━━━━━━━━━━━━━━

参考 Codex/Cursor 的执行摘要形式：

Worked for 2m 18s     ﹀

展开后显示：

- Thought briefly
- Explored 3 files
- Searched for “ConversationActivity”
- Ran 2 commands
- Edited 4 files
- Build passed

不得展示隐藏思维链、系统提示词或模型内部完整推理。

工作过程只显示可观察操作。

每个 Tool Step 显示：

- 图标。
- 人类可读摘要。
- 运行、成功、失败、等待审批状态。
- 耗时。
- 展开箭头。

展开后可以显示：

- 工具名称。
- 命令。
- 文件路径。
- 工作目录。
- 输入参数。
- 截断后的输出。
- 错误信息。
- 复制按钮。

默认不要显示完整 JSON。

tool_call 和 tool_result 必须按 tool_call_id 合并为同一个步骤。

当前 Turn 的工作过程默认展开。

已经完成的历史 Turn 默认收起工作过程，只显示：

- 用户问题。
- Worked for 摘要。
- 最终回答。

用户展开状态需要在 RecyclerView 更新时保持稳定。

━━━━━━━━━━━━━━━━━━
七、滚动行为
━━━━━━━━━━━━━━━━━━

删除当前每次 appendEvent 后调用 fullScroll(FOCUS_DOWN) 的实现。

使用 RecyclerView 滚动策略：

- 用户位于列表底部附近时，才自动跟随流式回答。
- 用户向上阅读历史时，不改变其滚动位置。
- 新事件到达时显示“新消息”悬浮按钮。
- 点击后滚动到底部。
- 加载更早历史时保持首个可见条目的位置。
- 展开 Tool Step 或 Work Group 时不产生突然跳动。
- 发送用户问题后可以滚动到新 Turn。
- 最终回答到达时不能强制打断用户阅读。

使用 ListAdapter + DiffUtil，而不是不断 notifyDataSetChanged。

stable ID 必须稳定，避免流式更新导致条目闪烁。

━━━━━━━━━━━━━━━━━━
八、审批卡优化
━━━━━━━━━━━━━━━━━━

保留真实审批功能，但重新设计 item_approval.xml。

卡片必须显示人类可读内容：

- “需要运行命令”。
- 命令内容。
- 工作目录。
- 文件访问范围。
- 网络地址。
- 风险等级。
- 风险说明。

禁止默认直接显示 payload.toString()。

根据 kind 和 payload 解析：

- command/process
- filesystem
- network
- installation
- destructive operation
- 其他未知类型

无法识别的字段放进“查看技术详情”折叠区。

按钮：

- 拒绝
- 仅允许本次

只有后端真实支持持久授权时才显示“始终允许”。

交互要求：

- 拒绝使用 OutlinedButton。
- 普通允许使用主按钮。
- 高危允许使用明确的警示样式。
- 两个按钮点击区域至少 48dp 高。
- 360dp 宽度不足时上下排列。
- 点击后立即进入 loading。
- 禁止重复点击。
- 成功后显示已批准或已拒绝状态。
- 失败后恢复按钮并显示可理解错误。
- 审批已经被其他客户端处理时同步更新状态。

审批请求既要出现在所属 Tool Step 中，也要固定显示在 Composer 上方，确保用户能发现。

━━━━━━━━━━━━━━━━━━
九、Composer 优化
━━━━━━━━━━━━━━━━━━

Composer 使用 Material 风格底部输入区域：

- 最小高度 56dp。
- 输入框最小高度 48dp。
- 最大高度约 144dp。
- 自动增长。
- 发送按钮至少 48×48dp。
- 支持多行。
- 使用 IME action send，但多行换行仍可操作。
- 保留输入草稿。

空闲状态：

- 输入框提示“描述要完成的任务”。
- 右侧显示发送按钮。

运行状态：

- 发送按钮切换为停止按钮。
- 输入内容可选择“引导当前任务”或“排队追问”。

不要同时常驻显示“发送、引导、追问、暂停、恢复、停止”六个按钮。

可以通过发送按钮菜单或输入框上方的小型模式选择实现：

- 新任务
- 引导当前任务
- 后续追问

当前状态不支持的操作不得显示为可用。

发送失败时不得清空输入内容。

只有服务端确认接收后才清空输入框。

━━━━━━━━━━━━━━━━━━
十、历史会话列表
━━━━━━━━━━━━━━━━━━

优化：

- ProjectDetailActivity.kt
- ConversationAdapter.kt
- item_conversation.xml

会话列表每项显示：

- 对话标题。
- 最近一次用户问题或最终回答摘要。
- 更新时间。
- 运行、等待审批、失败、完成状态。
- 未处理审批标记。
- 更多菜单。

不要继续默认显示 conversation ID 前 8 位。

重命名和归档放入每项右侧的更多菜单，不要为每个会话长期展示两个文字按钮。

使用 ListAdapter + DiffUtil，替换 notifyDataSetChanged。

历史会话按 updatedAt 倒序。

打开会话后：

- 使用 before_seq 从最新事件向前分页加载。
- 初次加载最近若干 Turn。
- 上滑到顶部时加载更早内容。
- 不要使用保存的 after_seq 导致重新打开页面只读取“上次游标以后”的事件，从而丢失旧对话。
- 本地游标只应用于实时增量同步，不能代替历史首屏加载。

历史 Turn 默认压缩工作过程，但保留用户问题和最终回答。

━━━━━━━━━━━━━━━━━━
十一、任务状态和结果卡
━━━━━━━━━━━━━━━━━━

状态使用人类可读文案：

- 正在排队
- 正在运行
- 等待审批
- 已暂停
- 正在停止
- 已完成
- 执行失败
- 已取消
- 已中断

不能只显示原始状态字符串。

任务结束后，在最终回答下方显示结果卡：

- 改动文件数量。
- 构建成功或失败。
- 是否生成 APK。
- 总耗时。
- 查看改动。
- 查看构建日志。
- 下载、安装、分享 APK。

“查看改动”必须绑定真实的 DiffActivity，并确认：

- projectId 正确。
- 必要时传递 turnId 或 jobId。
- 没有改动时按钮禁用并显示原因。
- Activity 启动失败时给出错误提示。

不要让这些操作长期占据会话顶部。

━━━━━━━━━━━━━━━━━━
十二、Material 3 视觉规范
━━━━━━━━━━━━━━━━━━

沿用现有 Material XML 技术栈，统一设计 Token。

建议：

- 页面水平边距：16dp。
- 紧凑间距：4dp、8dp。
- 常规间距：12dp、16dp。
- 区块间距：20dp、24dp。
- 小圆角：8dp。
- 消息圆角：16dp。
- 卡片圆角：14～16dp。
- 最小触控区域：48dp。
- 正文：16sp。
- 次级文字：13～14sp。
- Toolbar 标题：18～20sp。

减少：

- 大量 MaterialCardView 嵌套。
- 每条事件都有边框。
- 过多 TonalButton。
- 横向滚动操作条。
- 大面积全大写英文风险标签。
- 原始 JSON 和内部 ID。

亮色和暗色主题均需要验证。

禁止写死：

- 纯白背景。
- 纯黑文字。
- 固定灰色色值。
- 只适配亮色主题的图标。

颜色应来自主题属性或 colors.xml，并达到足够对比度。

━━━━━━━━━━━━━━━━━━
十三、生命周期和性能
━━━━━━━━━━━━━━━━━━

修复或验证：

- Activity 重复 onStart 时不得重复追加相同历史事件。
- 页面停止时关闭 WebSocket。
- 页面恢复时从正确游标继续。
- 切换 Job 时清理旧 watcher 和旧回调。
- Conversation 历史事件和 Job 实时事件去重。
- 旧网络响应不能污染新的 Conversation。
- 审批轮询不会重复创建相同卡片。
- RecyclerView 长列表保持流畅。
- 工具长输出延迟展开。
- 流式 Markdown 不进行高频完整解析。
- 配置变化后恢复当前 UI 状态。

不要把完整 Conversation 保存进 SharedPreferences。

SharedPreferences 只保存轻量 ID、游标、草稿和界面状态。

━━━━━━━━━━━━━━━━━━
十四、无障碍
━━━━━━━━━━━━━━━━━━

实现：

- 图标按钮 contentDescription。
- 展开和收起状态描述。
- 运行状态不能只依赖颜色。
- 重要状态可被 TalkBack 识别。
- 不要让每个 text_delta 都触发语音朗读。
- 等待审批、失败和完成状态可以进行一次简短播报。
- 点击区域至少 48dp。
- 支持系统字体缩放。
- 字体放大到 1.3～1.5 倍时主要按钮不能被裁剪。
- 横屏和小屏不能出现无法操作的控件。

━━━━━━━━━━━━━━━━━━
十五、测试要求
━━━━━━━━━━━━━━━━━━

为事件标准化和时间线聚合增加单元测试。

至少覆盖：

1. Conversation event_type + payload。
2. Job type 扁平事件。
3. user_message content block。
4. text_delta 连续追加。
5. text 快照替换。
6. assistant_message text_blocks。
7. assistant_message is_final。
8. job.result 兼容回退。
9. 最终回答不重复。
10. tool_call/tool_result 按 tool_call_id 合并。
11. approval_required/approval_resolved 合并。
12. 多个 turn_id 正确分轮。
13. 缺少 turn_id 时的兼容分组。
14. 重复事件去重。
15. 历史事件和实时事件合并。
16. 未知事件安全降级。
17. 超长工具输出截断。
18. Conversation 切换不串数据。

增加必要的 Android UI 测试，验证：

- 360dp 左右宽度。
- 普通中文长回答。
- Markdown 列表和代码块。
- 长命令审批。
- 软键盘弹出后 Composer 可见。
- 用户向上滚动后不被拉到底部。
- 新消息按钮可以返回底部。
- 历史工作过程可以展开和收起。
- 暗色模式。
- 字体放大。
- 横屏。
- 查看改动按钮真实可用。

━━━━━━━━━━━━━━━━━━
十六、构建验证
━━━━━━━━━━━━━━━━━━

至少运行：

cd android-app
./gradlew testDebugUnitTest
./gradlew assembleDebug

如果加入 Android 仪器测试且环境允许，再运行：

./gradlew connectedDebugAndroidTest

不得因为系统安全限制、网络或依赖下载失败而声称测试通过。

如果测试无法运行，必须报告：

- 实际执行的命令。
- 完整失败阶段。
- 已经完成的静态验证。
- 用户需要补充的环境条件。

生成 Debug APK 后报告绝对路径。

使用模拟器或真实设备至少检查：

- 360dp 宽手机。
- 主流 390～412dp 宽手机。
- 暗色模式。
- 长回答。
- 等待审批。
- 多轮历史。

保存关键页面截图：

- 空白新对话。
- 流式回答。
- 展开的工具过程。
- 收起的历史 Turn。
- 等待审批。
- 任务完成及改动卡。
- 暗色模式。

━━━━━━━━━━━━━━━━━━
十七、验收标准
━━━━━━━━━━━━━━━━━━

只有满足以下条件才算完成：

- 不再使用单个 TextView 拼接全部事件。
- 不再把 Job 事件和 Conversation 事件分别随意解析。
- 历史对话可以正确显示用户问题和最终回答。
- text_delta 不会丢失、重复或覆盖错误。
- 同一轮最终回答只显示一份。
- 工具调用和结果合并展示。
- 工作过程可以压缩和展开。
- 用户阅读历史时不会被强制滚到底部。
- Composer 固定底部且不被键盘遮挡。
- 等待审批始终容易发现并能真实操作。
- 审批不再默认展示原始 JSON。
- 横向滚动操作按钮条被移除。
- Diff、构建日志和 APK 入口真实有效。
- 亮色、暗色和字体缩放可用。
- 360dp 宽度不存在页面级横向滚动。
- Android 单元测试和 Debug 构建通过。
- 没有破坏 agent/web 和 desktop 端。

━━━━━━━━━━━━━━━━━━
十八、最终报告
━━━━━━━━━━━━━━━━━━

完成后输出：

1. Android 回复展示问题的根因。
2. 修改过的文件。
3. 新增的数据模型和事件标准化规则。
4. Conversation 历史和 Job 实时事件如何合并。
5. RecyclerView 包含哪些 View Type。
6. Markdown 渲染方案。
7. 工具过程压缩与展开实现。
8. 审批交互实现。
9. 滚动和软键盘适配。
10. 测试命令与实际结果。
11. Debug APK 绝对路径。
12. 截图绝对路径。
13. 尚未解决的限制。

不要只回复“已优化”，必须提供代码、测试、构建和截图证据。
```

## 4. 使用说明

- 上述提示词只针对 `android-app/` 原生 Android 客户端。
- 如果目标是手机浏览器访问的远程操作台，应另行针对 `agent/web/` 编写任务，不要将两者混为一谈。
- 执行 Android 优化前，应先完成或明确终止当前桌面端真实冒烟测试，避免多个任务同时修改共享后端协议或测试基线。
- 如果 Android 端需要后端新增字段，优先复用桌面端现有 canonical Conversation Event 协议，不要创建 Android 专属事件格式。
