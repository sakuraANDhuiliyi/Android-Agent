from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from agent.paths import (
    BUILDS_DIR,
    DEFAULT_USER_ID,
    WORKSPACES_DIR,
    validate_id,
    workspace_path,
)
from agent.project import init_project, list_projects, load_project_meta


def _resolve_cli_user_id(args: argparse.Namespace) -> str:
    user_id = getattr(args, "user_id", None) or DEFAULT_USER_ID
    return validate_id(str(user_id), kind="user_id")


def cmd_init(args: argparse.Namespace) -> int:
    user_id = _resolve_cli_user_id(args)
    try:
        project_id = init_project(
            name=args.name,
            package=args.package,
            user_id=user_id,
        )
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    print(f"用户: {user_id}")
    print(f"已创建项目: {project_id}")
    print(f"路径: {workspace_path(user_id, project_id)}")
    if args.package:
        print(f"包名: {args.package}")
    print(
        f"\n下一步:\n  python -m agent ask {project_id} \"你的需求\" --user-id {user_id}"
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    user_id = _resolve_cli_user_id(args)
    projects = list_projects(user_id)
    if not projects:
        print(f"用户 {user_id} 暂无项目。使用: python -m agent init --name demo --user-id {user_id}")
        return 0
    print(f"用户: {user_id}")
    for p in projects:
        print(f"  {p['id']:20}  {p['name']}  ({p['package']})")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from agent.config import load_settings, resolve_job_settings
    from agent.loop import run_agent

    user_id = _resolve_cli_user_id(args)
    project_id = args.project_id
    try:
        load_project_meta(user_id, project_id)
    except FileNotFoundError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    workspace = workspace_path(user_id, project_id)
    base_settings = load_settings()
    provider = args.provider
    auto_fallback = bool(args.auto_fallback or not provider or provider == "auto")
    try:
        settings = resolve_job_settings(
            base_settings,
            provider,
            auto_fallback=auto_fallback,
        )
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    print(f"用户: {user_id}")
    print(f"项目: {project_id}")
    print(f"工作区: {workspace}")
    print(f"提供商: {settings.provider}")
    print(f"主模型: {settings.model}")
    if len(settings.model_candidates) > 1:
        print(f"模型备用: {', '.join(settings.model_candidates[1:])}")
    if settings.provider_fallbacks:
        print(
            "提供商备用: "
            + " -> ".join([settings.provider] + [s.provider for s in settings.provider_fallbacks])
        )
    print(f"问题: {args.prompt}")
    print()

    try:
        answer = run_agent(
            settings,
            workspace,
            user_id,
            project_id,
            args.prompt,
        )
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    print("\n=== 完成 ===")
    if answer:
        print(answer)
    return 0


def cmd_migrate_users(_: argparse.Namespace) -> int:
    """Move flat workspaces/builds into workspaces/local and builds/local."""
    target_user = DEFAULT_USER_ID
    moved = 0

    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    BUILDS_DIR.mkdir(parents=True, exist_ok=True)
    user_ws = WORKSPACES_DIR / target_user
    user_builds = BUILDS_DIR / target_user
    user_ws.mkdir(parents=True, exist_ok=True)
    user_builds.mkdir(parents=True, exist_ok=True)

    for child in sorted(WORKSPACES_DIR.iterdir()):
        if not child.is_dir() or child.name == target_user:
            continue
        meta_file = child / ".agent-project.json"
        # Skip already-nested user dirs (no project meta at top level)
        if not meta_file.is_file():
            continue
        dest = user_ws / child.name
        if dest.exists():
            print(f"跳过已存在: {dest}")
            continue
        shutil.move(str(child), str(dest))
        meta_path = dest / ".agent-project.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["id"] = child.name
        meta["user_id"] = target_user
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"迁移项目: {child.name} -> {dest}")
        moved += 1

    for child in sorted(BUILDS_DIR.iterdir()):
        if not child.is_dir() or child.name == target_user:
            continue
        # Flat build dirs contain logs/apk; user dirs contain project subdirs
        has_project_meta_sibling = (WORKSPACES_DIR / target_user / child.name).is_dir()
        looks_like_project_build = any(child.glob("*.log")) or (child / "latest.apk").is_file()
        if not looks_like_project_build and not has_project_meta_sibling:
            # Might already be a user directory with nested projects
            if any((child / sub).is_dir() for sub in child.iterdir() if sub.is_dir()):
                continue
        dest = user_builds / child.name
        if dest.exists():
            print(f"跳过构建目录已存在: {dest}")
            continue
        shutil.move(str(child), str(dest))
        print(f"迁移构建: {child.name} -> {dest}")
        moved += 1

    print(f"完成，迁移条目数: {moved}（目标用户: {target_user}）")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("错误: 请先安装依赖: python3 -m pip install -r requirements.txt", file=sys.stderr)
        return 1

    from agent.api import create_app, _guess_lan_ip
    from agent.config import load_settings
    from agent.jobs import start_worker

    settings = load_settings()
    host = args.host or settings.server_host
    port = args.port or settings.server_port
    app = create_app(settings)
    start_worker(settings)

    lan_ip = _guess_lan_ip()
    print("Android Agent API 已启动")
    print(f"  本机:   http://127.0.0.1:{port}/docs")
    if lan_ip:
        print(f"  局域网: http://{lan_ip}:{port}/docs")
    print(f"  用户数: {len(settings.users)}")
    for user in settings.users:
        token_state = "已配置 Token" if user.token else "空 Token（仅本机调试）"
        print(f"    - {user.id}: {token_state}")
    print("  鉴权:   Authorization: Bearer <user_token>")

    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent",
        description="Android Agent — 用自然语言修改 Android 工程并编译",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="从 template 创建新工程到 workspaces/{user_id}/")
    p_init.add_argument("--name", required=True, help="项目显示名称")
    p_init.add_argument("--package", help="应用包名，默认保持模板包名")
    p_init.add_argument("--user-id", default=DEFAULT_USER_ID, help=f"用户 ID，默认 {DEFAULT_USER_ID}")
    p_init.set_defaults(func=cmd_init)

    p_list = sub.add_parser("list", help="列出当前用户的项目")
    p_list.add_argument("--user-id", default=DEFAULT_USER_ID, help=f"用户 ID，默认 {DEFAULT_USER_ID}")
    p_list.set_defaults(func=cmd_list)

    p_ask = sub.add_parser("ask", help="向 Agent 提问并修改工程")
    p_ask.add_argument("project_id", help="项目 ID（init 时输出）")
    p_ask.add_argument("prompt", help="自然语言需求")
    p_ask.add_argument("--user-id", default=DEFAULT_USER_ID, help=f"用户 ID，默认 {DEFAULT_USER_ID}")
    p_ask.add_argument(
        "--provider",
        help="指定提供商: deepseek | anthropic | auto（默认 auto）",
    )
    p_ask.add_argument(
        "--auto-fallback",
        action="store_true",
        help="启用提供商自动备用（auto 模式默认开启）",
    )
    p_ask.set_defaults(func=cmd_ask)

    p_migrate = sub.add_parser(
        "migrate-users",
        help="将旧版扁平 workspaces/builds 迁移到 local 用户目录",
    )
    p_migrate.set_defaults(func=cmd_migrate_users)

    p_serve = sub.add_parser("serve", help="启动 HTTP API 服务（供手机 App 连接）")
    p_serve.add_argument("--host", help="监听地址，默认 0.0.0.0")
    p_serve.add_argument("--port", type=int, help="端口，默认 8000")
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
