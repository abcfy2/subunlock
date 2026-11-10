import argparse
import sys

import yaml

from . import PROVIDERS
from .base import ProviderArgumentError, ProviderError, to_subscription

_COMMON_HELP = "保留完整配置(默认剥离运行时键, 只留订阅四要素)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="subunlock",
        description="从机场魔改客户端提取标准 Clash 订阅(详细说明见 README)",
    )
    parser.add_argument("--output", help="输出文件(缺省打印到终端)")
    parser.add_argument("--full", action="store_true", help=_COMMON_HELP)
    provider_names = ", ".join(sorted(PROVIDERS))
    sub = parser.add_subparsers(
        dest="provider", required=True, metavar="provider", help=f"可用值: {provider_names}"
    )
    for name, cls in sorted(PROVIDERS.items()):
        sp = sub.add_parser(name, help=cls.summary, description=cls.summary)
        cls.configure_args(sp)
        sp.add_argument("--output", help="输出文件(缺省打印到终端)")
        sp.add_argument("--full", action="store_true", help=_COMMON_HELP)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        provider = PROVIDERS[args.provider].create(vars(args))
    except ProviderArgumentError as e:
        # 缺失必选参数: 打印 usage + 错误, 与 argparse 原生行为一致(退出码 2)
        parser.print_usage(sys.stderr)
        print(f"{parser.prog}: 错误: {e}", file=sys.stderr)
        return 2
    try:
        config = provider.fetch()
    except ProviderArgumentError as e:
        print(f"错误: {e}", file=sys.stderr)
        print(f"用法: subunlock {args.provider} --help", file=sys.stderr)
        return 2
    except ProviderError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    data = config if args.full else to_subscription(config)
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
