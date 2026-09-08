# Mobile Code Explorer 与 Build / Test / Problems V2

## Files

Project → Files 默认进入可展开的 Android 逻辑目录。`manifests`、`kotlin`、`res` 是展示分组；读取、选区上下文和保存始终使用真实仓库路径。搜索匹配文件名或完整路径；Filter 支持 Code、Resources、Modified only、Open files。结果最多返回 1,000 个文件，达到上限时提示缩小查询。忽略构建产物、隐藏目录和符号链接。

文件默认只读且可选择文本。选区操作包括 Copy、Explain、Fix、Refactor、Add tests、Ask Agent。Agent 操作创建 Conversation 草稿，并带入结构化的文件或选区 Chip（路径、首尾行号、选区文本）。未保存选区会明确标为本地修改。单个选区上限 24,000 字符。

Quick Edit 提供 Edit、Save、Undo、Redo；连续输入按停顿分组合并，最多保留 40 个撤销步骤。旋转屏幕保存当前草稿、基线版本、选区、已打开文件和目录展开状态。切换文件或退出时提示保存/放弃；HTTP 409 冲突不会清除草稿。客户端以 SHA-256 版本提交保存，服务端拒绝覆盖加载后已经变化的文件，并阻止 Agent 活动期间的手工保存。

## Build / Test

Project → Build（原 BuildLog 入口同样先进入摘要）显示真实 Gradle 状态、耗时、执行/缓存/up-to-date 任务数、警告、测试计数及 APK。View raw log 才打开原始日志。测试失败时保留成功构建的 APK；再次构建失败会清除该任务的旧产物引用。

测试任务为 `testDebugUnitTest`，使用本次运行新生成的 JUnit XML，统计 passed、failed、skipped。未运行和未生成报告有独立状态，不显示为零失败的成功测试。当前不包含设备 instrumentation tests。

## Problems

统一展示 Compiler、Lint、Tests、Gradle、Runtime、Agent diagnostics。编译位置从日志解析；Lint 从 `lintDebug` 的 XML 报告读取；测试错误从 JUnit 获取，并在可定位时关联测试源文件。运行时问题通过“添加 Runtime / 崩溃日志”导入（不自动连接目标 App 的 logcat）。Agent 诊断来自已有 DiagnosticStore 及最近任务错误。

问题菜单提供 Open file（有安全的仓库路径时）、Ask Agent to fix、Copy。Open file 定位并选中对应行。Fix with Agent 创建并启动新的 Conversation，自动附带 Current diff、问题摘要和构建日志 Context。它仍受项目串行任务限制。

## 自动反馈循环

三个项目级开关分别控制 Build after Agent changes、Run tests after build、Ask Agent to fix failures。默认沿用服务端原自动构建配置，测试及自动修复默认关闭。设置保存到服务端，并在任务入队时保存快照。

流程为 Agent 编辑 → assembleDebug → testDebugUnitTest（启用时）→ 失败修复 → 重建/重测。Build now 保存当前开关并启动显式构建任务，不先调用模型；仅修复失败需要模型。现有 ask API 仍要求配置有效的模型服务。

自动修复最多两次，计数保存在 Task Context，暂停恢复不会重置修复次数。每次修复的消息 ID 独立，事件仍归属同一个 Conversation Turn。构建和修复沿用原工具权限、审批、取消、暂停及租约检查；拒绝审批不会触发自动修复重试。失败达到上限后任务失败，保留日志和 Problems。

较新的成功构建替换旧编译错误；旧构建轮次的测试结果不会被误算为当前测试。任务报告保留在 `context.feedback_runs` 中。

## API 与验证

- `GET /api/projects/{id}/files/search?q=&kind=all|code|resources&modified_only=`
- 文件读取新增 `revision`；文件写入新增可选 `expected_revision`。
- `GET /api/projects/{id}/feedback?job_id=`
- `PUT /api/projects/{id}/feedback/settings`
- `POST /api/projects/{id}/feedback/runtime`
- 两个 ask API 新增 `feedback_requested`，默认 false。

重点回归为 `tests/test_feedback_v2.py`、`CodeExplorerTest.kt`，覆盖文件冲突/活动任务拒绝保存、筛选与路径隔离、JUnit 新鲜度、Gradle 统计、错误失效、重试/取消边界、真实任务的两次修复事件链，以及撤销重做和行号计算。
