# 任务验证记录

Android 聊天页、验证详情页和桌面两个助手入口读取同一份任务验证记录。构建、单测、APK 与设备安装分别显示，模型回复中的“已完成”不作为验证依据。

## 时间与范围

每种检查保留该任务最近一次执行，先测后构建也保留单测结果。卡片展示命令、执行时间、耗时与单测数量。`source_match` 单独说明输入关系，不覆盖历史执行结果：例如“构建通过 · 输入已变更”。

执行前后采集工作区输入摘要；任务结束时再与最终快照比较。暂停期间不冻结最终快照，继续后补齐。本记录不随其他任务或外部编辑实时更新；打开任务列表不会扫描工作区。尚在执行的任务通过执行前后快照表达最近已完成检查的输入关系。

输入范围为 `workspace_inputs_v1`：工作区内普通文件，包括根配置、Gradle wrapper、版本目录、所有模块源码/资源、`buildSrc` 与工作区内 included builds。排除 `.git`、`.gradle`、`.kotlin`、`.idea`、`.cxx`、`.externalNativeBuild`、`build`、`node_modules`、`__pycache__`、`.artifacts` 目录及 `.DS_Store` 文件；位于 `src` 源码集内的同名包或资源目录仍计入输入（Git 元数据除外）。该摘要不证明 SDK、远程依赖或工作区之外的输入未变。

文件增加、删除、改名、内容或执行权限变化都会改变摘要。仅修改时间不改变内容匹配。遇到符号链接、不可读或特殊文件、扫描过程中变化，返回未知；名为 `build` 的排除目录如果自带 Gradle 项目配置，也保守返回未知。默认上限为 10,000 个文件、单文件 32 MiB、总量 128 MiB，并限制目录项数量。结果仅保存摘要与统计，不保存源码内容。

## 单测证据

`testDebugUnitTest` 执行前后对相同 variant 的 JUnit XML 做快照。只有本次新增或变化且可完整解析的报告计入统计；旧文件、其他 variant 或不完整报告不能证明通过。进程退出成功但报告中有失败用例仍显示失败；零用例与全部跳过分别展示。执行取消或超时保留对应状态，已有统计也不代表整套测试通过。

APK 产物存在与设备安装成功是不同事实。客户端不从下载、打开安装器或解析 APK 推断安装完成；没有设备回执时安装状态保持未知。

## API

`GET /api/jobs`、`GET /api/jobs/{job_id}` 的 `verification` 与 `GET /api/projects/{project_id}/feedback?job_id={job_id}` 顶层 `verification` 使用相同结构。接口验证账号与项目归属。

```json
{
  "schema_version": 1,
  "job_id": "task-id",
  "scope": "job",
  "build": {
    "state": "passed",
    "task": "assembleDebug",
    "run_id": "run-id",
    "evidence_time": 1791560000,
    "duration_ms": 1200,
    "source_match": "match",
    "reason": null
  },
  "unit_tests": {
    "state": "not_run",
    "task": "testDebugUnitTest",
    "run_id": null,
    "evidence_time": null,
    "duration_ms": null,
    "source_match": "unknown",
    "reason": "not_run",
    "counts": null
  },
  "installation": {
    "state": "unknown",
    "evidence_time": null,
    "source_match": "unknown",
    "reason": "no_device_receipt"
  }
}
```

`evidence_time` 为 Unix 秒，`duration_ms` 为毫秒。执行状态为 `not_run`、`passed`、`failed`、`canceled`、`interrupted` 或 `unknown`；单测还包括 `no_tests` 与 `skipped`。`source_match` 为 `match`、`changed` 或 `unknown`。有效单测数量满足 `total = passed + failed + skipped`，均为非负整数。

旧服务、未知版本、任务不匹配、缺失执行身份/时间或无效统计均保守展示未知；不能将旧 `build.status=success` 或任务完成状态转换为新协议的验证通过。
