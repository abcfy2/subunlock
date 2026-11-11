import argparse
import sys

import yaml
from pydantic import BaseModel, ValidationError

from . import PROVIDERS
from .base import ProviderArgumentError, ProviderError, to_subscription

_RESERVED_SUBCOMMANDS = {"serve"}
_COMMON_HELP = "保留完整配置(默认剥离运行时键, 只留订阅四要素)"


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
    return parser


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
    provider = provider_cls.create(params)
    try:
        config = provider.fetch()
        data = config if args.full else to_subscription(config)
    except ProviderArgumentError as e:
        print(f"错误: {e}", file=sys.stderr)
        print(f"用法: subunlock {args.command} --help", file=sys.stderr)
        return 2
    except ProviderError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=10000)
    proxies = len(data.get("proxies") or [])
    groups = len(data.get("proxy-groups") or [])
    rules = len(data.get("rules") or [])
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"OK: {proxies} 节点, {groups} 策略组, {rules} 规则 -> {args.output}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
