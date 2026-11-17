"""Generative media CLI group: ``ms gen image|video|storyboard|quota|providers``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from media_studio.gen import (
    GenError,
    GenRequest,
    generate_asset,
    generate_storyboard,
)
from media_studio.gen.flow_quota import DAILY_LIMIT, FlowAccountManager, FlowCreditStore
from media_studio.gen.providers import default_providers
from media_studio.gen.quota import QuotaStore
from media_studio.manifest import load_project

from .common import EXIT_FAILURE, EXIT_OK


def register_gen_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``gen`` group parser."""

    parser = subparsers.add_parser("gen", help="pluggable free-quota generative pipeline")
    sub = parser.add_subparsers(dest="gen_command", required=True)

    image_parser = sub.add_parser("image", help="generate an image from a text prompt")
    image_parser.add_argument("prompt", help="image generation prompt")
    image_parser.add_argument("--out", type=Path, required=True, help="output image path")
    image_parser.add_argument(
        "--provider", default=None, help="preferred provider name (aistudio, huggingface, keyless)"
    )
    image_parser.add_argument(
        "--max-gen-calls", type=int, default=6, help="maximum successful generative calls"
    )
    image_parser.add_argument("--project", default=None, help="project directory or slug")
    image_parser.set_defaults(func=cmd_gen_image)

    video_parser = sub.add_parser("video", help="generate a video from a text prompt")
    video_parser.add_argument("prompt", help="video generation prompt")
    video_parser.add_argument("--out", type=Path, required=True, help="output video path")
    video_parser.add_argument("--provider", default=None, help="preferred provider name")
    video_parser.add_argument(
        "--max-gen-calls", type=int, default=6, help="maximum successful generative calls"
    )
    video_parser.add_argument("--project", default=None, help="project directory or slug")
    video_parser.add_argument(
        "--duration", type=float, default=5.0, help="video duration in seconds"
    )
    video_parser.add_argument("--fps", type=int, default=30, help="video frame rate")
    video_parser.add_argument("--resolution", default="1920x1080", help="video resolution WxH")
    video_parser.set_defaults(func=cmd_gen_video)

    storyboard_parser = sub.add_parser("storyboard", help="generate one image per scene")
    storyboard_parser.add_argument("scenes", help="comma-separated scene prompts")
    storyboard_parser.add_argument(
        "--out-dir", type=Path, required=True, help="output directory for storyboard frames"
    )
    storyboard_parser.add_argument(
        "--max-gen-calls", type=int, default=6, help="maximum successful generative calls"
    )
    storyboard_parser.add_argument("--project", default=None, help="project directory or slug")
    storyboard_parser.set_defaults(func=cmd_gen_storyboard)

    quota_parser = sub.add_parser("quota", help="show or reset the daily quota store")
    quota_parser.add_argument(
        "--path", type=Path, default=None, help="quota store path (overrides env/default)"
    )
    quota_parser.add_argument("--reset", action="store_true", help="reset all provider quotas")
    quota_parser.add_argument("--provider", default=None, help="reset one provider's quota")
    quota_parser.set_defaults(func=cmd_gen_quota)

    providers_parser = sub.add_parser("providers", help="list providers and availability")
    providers_parser.add_argument(
        "--path", type=Path, default=None, help="quota store path for spend snapshot"
    )
    providers_parser.set_defaults(func=cmd_gen_providers)

    flow_status_parser = sub.add_parser(
        "flow-status", help="show Google Flow account credit status"
    )
    flow_status_parser.add_argument(
        "--path", type=Path, default=None, help="credits store path (overrides env/default)"
    )
    flow_status_parser.add_argument(
        "--numbered", action="store_true", help="add a leading # column to the table"
    )
    flow_status_parser.set_defaults(func=cmd_gen_flow_status)

    flow_account_parser = sub.add_parser("flow-account", help="manage Google Flow accounts")
    flow_account_parser.add_argument(
        "--path", type=Path, default=None, help="accounts file path (overrides env/default)"
    )
    flow_account_sub = flow_account_parser.add_subparsers(
        dest="flow_account_command", required=True
    )

    account_list_parser = flow_account_sub.add_parser("list", help="list configured accounts")
    account_list_parser.add_argument(
        "--numbered", action="store_true", help="add a leading # column to the table"
    )
    account_list_parser.set_defaults(func=cmd_flow_account_list)

    account_add_parser = flow_account_sub.add_parser("add", help="add a Google Flow account")
    account_add_parser.add_argument("--id", required=True, help="account id")
    account_add_parser.add_argument("--email", required=True, help="account email")
    account_add_parser.add_argument("--tier", default="free", help="account tier (free|pro)")
    account_add_parser.add_argument("--token", required=True, help="account OAuth token")
    account_add_parser.set_defaults(func=cmd_flow_account_add)

    account_remove_parser = flow_account_sub.add_parser(
        "remove", help="remove a Google Flow account"
    )
    account_remove_parser.add_argument("--id", required=True, help="account id to remove")
    account_remove_parser.set_defaults(func=cmd_flow_account_remove)


def _providers_by_name() -> dict:
    return {provider.name: provider for provider in default_providers()}


def _resolve_project(location: str | None):
    if not location:
        return None
    try:
        return load_project(location)
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(EXIT_FAILURE) from error


def _run_generate(args, kind: str) -> int:
    try:
        project = _resolve_project(getattr(args, "project", None))
        output: Path = args.out
        request = GenRequest(
            kind=kind,
            prompt=args.prompt,
            size=getattr(args, "size", "1024x1024"),
            duration=getattr(args, "duration", 5.0),
            fps=getattr(args, "fps", 30),
            resolution=getattr(args, "resolution", "1920x1080"),
            motion=getattr(args, "motion", "push_in"),
        )
        quota_store = QuotaStore(path=args.path) if getattr(args, "path", None) else None
        result = generate_asset(
            request,
            project=project,
            output=output,
            max_gen_calls=args.max_gen_calls,
            quota_store=quota_store,
        )
    except GenError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    if result.fallback_used:
        print(
            f"gen {kind}: {result.provider}/{result.model} -> {result.output} "
            f"(fallback: {result.fallback_reason})"
        )
    else:
        print(f"gen {kind}: {result.provider}/{result.model} -> {result.output}")
    return EXIT_OK


def cmd_gen_image(args: argparse.Namespace) -> int:
    return _run_generate(args, "image")


def cmd_gen_video(args: argparse.Namespace) -> int:
    return _run_generate(args, "video")


def cmd_gen_storyboard(args: argparse.Namespace) -> int:
    try:
        project = _resolve_project(getattr(args, "project", None))
        scenes = [scene.strip() for scene in args.scenes.split(",") if scene.strip()]
        out_dir: Path = args.out_dir
        results = generate_storyboard(
            scenes,
            project=project,
            out_dir=out_dir,
            max_gen_calls=args.max_gen_calls,
        )
    except GenError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    for result in results:
        tag = f" (fallback: {result.fallback_reason})" if result.fallback_used else ""
        print(f"gen storyboard: {result.provider} -> {result.output}{tag}")
    return EXIT_OK


def cmd_gen_quota(args: argparse.Namespace) -> int:
    quota_store = QuotaStore(path=args.path)
    if args.reset:
        quota_store.reset(args.provider)
        target = args.provider or "all providers"
        print(f"quota: reset {target}")
        return EXIT_OK
    status = quota_store.status()
    if not status:
        print("quota: no spend recorded yet")
        return EXIT_OK
    print(f"quota store: {quota_store.path}")
    for name in sorted(status):
        state = status[name]
        marker = " (cooling down)" if state["cooling_down"] else ""
        print(f"  {name}: spent={state['spent']}{marker}")
    return EXIT_OK


def cmd_gen_providers(args: argparse.Namespace) -> int:
    quota_store = QuotaStore(path=args.path) if getattr(args, "path", None) else None
    status = quota_store.status() if quota_store else {}
    for provider in default_providers():
        available = provider.available()
        kinds = ", ".join(kind for kind in ("image", "video") if provider.generates(kind))
        key_name = {
            "aistudio": "GEMINI_API_KEY",
            "huggingface": "HF_TOKEN",
            "keyless": "none (Pollinations)",
        }.get(provider.name, "n/a")
        spent = status.get(provider.name, {}).get("spent", 0)
        cooling = status.get(provider.name, {}).get("cooling_down", False)
        print(
            f"  {provider.name:14s} available={available} keys={key_name} "
            f"kinds={kinds} spent={spent}{' cooling-down' if cooling else ''}"
        )
    return EXIT_OK


def _format_table(headers: list[str], rows: list[list], numbered: bool = False) -> str:
    """Render a simple aligned text table; prepends a ``#`` column when ``numbered``."""
    if numbered:
        headers = ["#", *headers]
        rows = [[str(i + 1)] + [str(c) for c in row] for i, row in enumerate(rows)]
    else:
        rows = [[str(c) for c in row] for row in rows]
    widths = [
        max(len(str(h)), max((len(str(row[j])) for row in rows), default=0))
        for j, h in enumerate(headers)
    ]

    def line(row: list) -> str:
        return "  ".join(str(cell).ljust(widths[idx]) for idx, cell in enumerate(row))

    out = [line(headers), line(["-" * w for w in widths])]
    out.extend(line(row) for row in rows)
    return "\n".join(out)


def _account_rows(manager: FlowAccountManager, store: FlowCreditStore) -> list[list]:
    rows: list[list] = []
    for acct in manager.accounts():
        state = store._read(account_id=acct.id) or {}
        tier = state.get("tier", acct.tier)
        daily_limit = int(state.get("daily_limit", DAILY_LIMIT))
        daily_spent = int(state.get("daily_spent", 0))
        monthly_bank = int(state.get("monthly_bank", 0))
        monthly_spent = int(state.get("monthly_spent", 0))
        monthly_locked = bool(state.get("monthly_locked", True))
        daily_remaining = daily_limit - daily_spent
        monthly_remaining = monthly_bank - monthly_spent
        if daily_remaining > 0:
            status = "active (daily)"
        elif monthly_remaining > 0 and not monthly_locked:
            status = "active (monthly)"
        else:
            status = "exhausted"
        rows.append(
            [
                acct.id,
                tier,
                f"{daily_spent}/{daily_limit}",
                f"{monthly_spent}/{monthly_bank}" if monthly_bank else "0/0",
                status,
            ]
        )
    return rows


def cmd_gen_flow_status(args: argparse.Namespace) -> int:
    store = FlowCreditStore(path=args.path) if getattr(args, "path", None) else FlowCreditStore()
    manager = FlowAccountManager()
    rows = _account_rows(manager, store)
    headers = ["Account", "Tier", "Daily Used/Limit", "Monthly Bank", "Status"]
    if not rows:
        print("flow-status: no configured Google Flow accounts")
        return EXIT_OK
    print(f"credits store: {store.credits_path}")
    print(_format_table(headers, rows, numbered=getattr(args, "numbered", False)))
    return EXIT_OK


def _load_accounts(manager: FlowAccountManager) -> list[dict]:
    import json

    path = manager.accounts_path
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _save_accounts(manager: FlowAccountManager, accounts: list[dict]) -> None:
    import json

    path = manager.accounts_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(accounts, indent=2), encoding="utf-8")


def cmd_flow_account_list(args: argparse.Namespace) -> int:
    # Allow a path override for the accounts file via the manager.
    manager = FlowAccountManager()
    if getattr(args, "path", None):
        manager = FlowAccountManager(accounts_path=args.path)
    accounts = _load_accounts(manager)
    rows = [
        [a.get("id", ""), a.get("tier", ""), a.get("email", ""), bool(a.get("enabled", True))]
        for a in accounts
    ]
    headers = ["Id", "Tier", "Email", "Enabled"]
    if not rows:
        print("flow-account: no configured Google Flow accounts")
        return EXIT_OK
    print(_format_table(headers, rows, numbered=getattr(args, "numbered", False)))
    return EXIT_OK


def cmd_flow_account_add(args: argparse.Namespace) -> int:
    manager = FlowAccountManager()
    if getattr(args, "path", None):
        manager = FlowAccountManager(accounts_path=args.path)
    accounts = _load_accounts(manager)
    if any(a.get("id") == args.id for a in accounts):
        print(f"error: account {args.id!r} already exists", file=sys.stderr)
        return EXIT_FAILURE
    accounts.append(
        {
            "id": args.id,
            "email": args.email,
            "tier": args.tier,
            "enabled": True,
            "token": args.token,
        }
    )
    _save_accounts(manager, accounts)
    print(f"flow-account: added {args.id}")
    return EXIT_OK


def cmd_flow_account_remove(args: argparse.Namespace) -> int:
    manager = FlowAccountManager()
    if getattr(args, "path", None):
        manager = FlowAccountManager(accounts_path=args.path)
    accounts = _load_accounts(manager)
    remaining = [a for a in accounts if a.get("id") != args.id]
    if len(remaining) == len(accounts):
        print(f"error: account {args.id!r} not found", file=sys.stderr)
        return EXIT_FAILURE
    _save_accounts(manager, remaining)
    print(f"flow-account: removed {args.id}")
    return EXIT_OK
