# 安全执行与凭据配置

2026-09-09 的安全修复收紧了进程、游客与凭据边界。升级前请为持久数据做好备份；账号数据库会自动新增验证码失败计数和游客每日额度表。

## 进程与 Git

Linux 安装 `bubblewrap`，以普通服务用户运行，并确保主机允许非特权用户命名空间。容器内同样需要宿主支持；不能用 root、特权容器或关闭沙箱作为降级方案。macOS 使用系统 `sandbox-exec`。没有可用执行器时，调用会失败。

以实际服务用户、服务使用的 Python 和环境执行以下无副作用检查：

```bash
python - <<'PY'
import tempfile
from pathlib import Path
from agent.processes import run_command
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    result = run_command(['/bin/echo', 'sandbox-ready'], cwd=root, workspace=root)
    assert result.ok, result
    print(result.stdout)
PY
```

只有当前工作区可写，标准运行时、服务 Python、运维配置的 SDK/JDK 可读。Git worktree 的管理操作额外开放同一用户、同一项目的管理目录。其他账号的数据、服务配置和主机文件不可读写。Git 查询使用最小环境并关闭 fsmonitor、hooks、外部 diff、textconv、凭据助手等入口；`.agent-home` 是保留的运行时缓存目录，不计入项目变更。

`JAVA_HOME`、`ANDROID_HOME`、`ANDROID_SDK_ROOT` 必须指向专用运行时目录，不要将密钥存入这些目录。MCP 参数和环境配置不能增加主机目录许可。`AGENT_CMD_SANDBOX=0` 不再生效。HOME、临时目录和 Gradle 缓存均按工作区隔离；依赖需要提前准备，子进程不允许联网。

Docker 镜像使用 UID/GID `10001`。已有挂载卷的所有权需要运维在迁移时处理，挂载前镜像中的权限不会覆盖卷内权限。

## MCP 凭据

`${NAME}` / `$NAME` 不再读取服务进程的环境变量。运维在 `AGENT_DATA_DIR/users/<user_id>/mcp-secrets.json` 按用户及服务器名配置授权，例如：

```json
{
  "servers": {
    "docs": {
      "DOCS_SERVICE_KEY": "replace-with-this-users-key"
    }
  }
}
```

该文件应只允许服务用户读写（`0600`），不可放进工作区或提交仓库。对应 MCP 配置可以使用 `"env": {"TOKEN": "${DOCS_SERVICE_KEY}", "MODE": "normal"}`。未授权的引用会拒绝启动；普通字面量环境值仍可用。项目配置仍须确认信任；本地脚本必须在工作区内，不能用命令行参数授权读取工作区外脚本。

## 游客和验证码

- `guest_sessions_enabled` / `AGENT_GUEST_SESSIONS_ENABLED` 默认 `false`。还必须开启注册且关闭强制邮箱验证，才提供游客入口。
- 游客仅可创建有限项目、进行只读问答和读取自己的结果。终端、MCP、文件修改、任务恢复、续跑与审批需要正式账号；每次问答最多 3 轮，不自动续接或构建。
- `guest_message_limit` 默认每个游客 3 次；`max_guest_turns_per_day` / `AGENT_MAX_GUEST_TURNS_PER_DAY` 默认所有游客合计 30 次，SQLite 原子计数，按 UTC 日期切换。已预留的全局次数不因任务创建失败而退还。新会话另有每小时全局与来源 IP 限流。
- 重用游客设备 ID 时必须提供原有效 Token；丢失凭据后需使用正式账号登录。
- 验证码最多错误 5 次，成功后只能消费一次。同账号同用途发送间隔至少 60 秒，HTTP 入口另有邮箱及来源 IP 限流。

HTTP 限流仍是单进程窗口；多副本部署需使用共享限流设施。游客每日问答总额度保存在账号数据库中，不因服务重启而重置。
