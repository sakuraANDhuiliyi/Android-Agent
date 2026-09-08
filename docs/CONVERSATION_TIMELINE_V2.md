# Conversation Timeline V2

Desktop 与 Android 使用同一套展示语义。服务端事件只描述事实，客户端布局只决定字体、颜色和断点，不得重新发明事件身份。

## Turn 结构

```text
Turn
├── UserPrompt
├── WorkSession
│   ├── StatusGroup
│   ├── ToolCluster
│   ├── Plan
│   ├── Approval
│   └── Error
├── FinalAnswer
└── Outcome
    ├── Changes
    ├── Tests / Build
    └── Artifact
```

Assistant 流从第一段 delta 起就属于 `FinalAnswer`。`assistant_message` 到达时原地把同一节点升级为最终内容，不能先放进 WorkSession、再删除并插入到 FinalAnswer。

## 稳定身份

| 节点 | 身份 |
| --- | --- |
| Turn | `turn_id`，实时阶段可由 `job_id` 暂代并在权威事件到达后收养 |
| UserPrompt / FinalAnswer | `message_id` |
| Tool | `tool_call_id` |
| Approval | `approval_id` |
| Changes | `turn_id` |
| StatusGroup | `turn_id + group sequence` |

实时事件和权威事件命中同一身份时只 patch 原节点。文本、JSON 内容或 DOM 位置都不能作为去重身份。

## 聚合规则

连续且同类别的两个及以上工具组成 `ToolCluster`。Status、Approval、Plan、Error 或其它非工具节点会中断聚合：

- `read`: `read_file`, `list_files`, `git_status`, `git_diff`
- `search`: `search_code`, `search_files`, `web_search`
- `write`: `write_file`, `str_replace`, `apply_patch`, `download_file`
- `command`: `run_command`, `run_gradle`

聚合行只展示类别、数量、状态和总耗时；完整路径、命令、参数和输出只在详情中展示。失败聚合默认展开，其它聚合默认折叠，用户选择在流式刷新后必须保留。

## 文本与布局契约

- 用户与 Assistant 正文：完整多行、自然断行、不使用 ellipsis。
- Work 摘要与 Tool 主行：单行 ellipsis，不显示完整 JSON。
- 展开的 Status：`maxLines` 不限且不使用 ellipsis。
- 代码与命令：等宽；代码块保持原行，工具输出在 Android 可软换行。
- Desktop Docked Agent：400–960px 可调。
- Desktop Focus Agent：同一 Timeline renderer，阅读区最大 920px。
- Android：Compact 窗口使用全宽减 16dp 边距，宽屏阅读区最大 760dp 并居中。

## 流式与滚动

流式刷新以 50–80ms 合并。用户距底部阈值内才自动跟随；用户上滚后只累计“新活动”，不能抢占阅读位置。最终事件只进行一次权威 Markdown 更新，不改变 Answer 行身份。

## 回归门禁

- live → canonical 不产生第二条消息。
- streaming → final 的 Row/DOM identity 不变。
- Android 与 Desktop 的 ToolCluster 边界一致。
- 展开 Status 后所有文本可见。
- Tool 主行不超过一行，完整参数保留在详情。
- 200% 字体下标题、状态和操作按钮不互相覆盖。
