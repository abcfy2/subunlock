import argparse
import sys
import time

from pydantic import BaseModel, ValidationError

from . import PROVIDERS
from .base import ProviderArgumentError, ProviderError, extract_subscription

_RESERVED_SUBCOMMANDS = {"serve"}
_COMMON_HELP = "保留完整配置(默认剥离运行时键, 只留订阅四要素)"
_INFO_HELP = "打印订阅摘要(流量/到期/节点数)"


def _add_provider_args(parser: argparse.ArgumentParser, params_model: type[BaseModel]) -> None:
    for field_name, field in params_model.model_fields.items():
        parser.add_argument(
            f"--{field_name.replace('_', '-')}", help=field.description, default=None
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="subunlock",
        description="从机场魔改客户端提取标准 Clash 订阅(详细说明见 README)",
    )
    parser.add_argument("--output", help="输出文件(缺省打印到终端)")
    parser.add_argument("--full", action="store_true", help=_COMMON_HELP)
    parser.add_argument("--info", action="store_true", help=_INFO_HELP)
    provider_names = ", ".join(sorted(PROVIDERS))
    sub = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="command",
        help=f"provider 或 serve; 可用 provider: {provider_names}",
    )
    serve = sub.add_parser("serve", help="启动 REST 服务, 暴露全部 provider")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址(默认 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8000, help="监听端口(默认 8000)")
    for name, cls in sorted(PROVIDERS.items()):
        if name in _RESERVED_SUBCOMMANDS:
            raise SystemExit(f"provider 名与保留子命令冲突: {name}")
        sp = sub.add_parser(name, help=cls.summary, description=cls.summary)
        _add_provider_args(sp, cls.params_model)
        sp.add_argument("--output", help="输出文件(缺省打印到终端)")
        sp.add_argument("--full", action="store_true", help=_COMMON_HELP)
        sp.add_argument("--info", action="store_true", help=_INFO_HELP)
    return parser


def _human_size(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PiB"


def _print_userinfo(userinfo: str | None) -> None:
    """解析上游 subscription-userinfo 头, 打印流量与到期摘要."""
    kv: dict[str, str] = {}
    for part in (userinfo or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key:
            kv[key] = value

    def _int(field: str) -> int | None:
        raw = kv.get(field, "")
        return int(raw) if raw.isdigit() else None

    used = sum(v for v in (_int("upload"), _int("download")) if v is not None)
    total = _int("total")
    if used and total:
        print(f"流量: 已用 {_human_size(used)} / {_human_size(total)}"
              f"(剩 {(total - used) / total:.0%})")
    elif used:
        print(f"流量: 已用 {_human_size(used)}")
    else:
        print("流量: 上游未提供")
    expire = _int("expire")
    if expire:
        days = (expire - int(time.time())) // 86400
        when = time.strftime("%Y-%m-%d", time.localtime(expire))
        print(f"到期: {when}({max(0, days)} 天后)" if days > 0 else f"到期: {when}(已过期)")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        # 延迟导入: 仅 CLI 安装(无 server 依赖组)时仍可用提取功能
        try:
            from .server import serve_main
        except ImportError:
            print(
                "错误: REST 服务需要 server 依赖组: pip install 'subunlock[server]'",
                file=sys.stderr,
            )
            return 2
        return serve_main(host=args.host, port=args.port)
    provider_cls = PROVIDERS[args.command]
    raw = {k: v for k, v in vars(args).items() if k in provider_cls.params_model.model_fields}
    try:
        params = provider_cls.params_model(**raw)
    except ValidationError as e:
        parser.print_usage(sys.stderr)
        print(f"{parser.prog} {args.command}: 参数错误:\n{e}", file=sys.stderr)
        return 2
    try:
        sub = extract_subscription(provider_cls, params, full=args.full)
    except ProviderArgumentError as e:
        print(f"错误: {e}", file=sys.stderr)
        print(f"用法: subunlock {args.command} --help", file=sys.stderr)
        return 2
    except ProviderError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    if args.info:
        _print_userinfo(sub.subscription_userinfo)
    summary = f"{sub.proxies} 节点, {sub.groups} 策略组, {sub.rules} 规则"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(sub.yaml_text)
        print(f"OK: {summary} -> {args.output}")
    elif args.info:
        print(f"OK: {summary}")
    else:
        sys.stdout.write(sub.yaml_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
