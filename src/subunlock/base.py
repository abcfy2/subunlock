import argparse
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar

SUBSCRIPTION_KEYS = ("proxies", "proxy-groups", "rules", "dns", "hosts")


class ProviderError(RuntimeError):
    """provider 运行时错误(网络/面板/解密失败), REST 侧对应 5xx."""


class ProviderArgumentError(ProviderError):
    """必选参数缺失或非法, REST 侧对应 400; CLI 打印 usage 后以 2 退出."""


class Provider(ABC):
    """订阅提取器抽象: 从机场魔改客户端还原标准 Clash 订阅配置.

    子类职责:
    - name/summary: 注册名与 --help 展示的一行说明
    - configure_args(): 声明本 provider 的专属 CLI 参数
    - create(): 从键值映射(命令行 vars(args) / REST query 字典)构造实例
    - fetch(): 执行提取, 返回完整配置 dict
    传输层(CLI/REST)只负责收集参数字典, provider 契约与传输方式无关.
    """

    name: ClassVar[str]
    summary: ClassVar[str] = ""

    @classmethod
    def configure_args(cls, parser: argparse.ArgumentParser) -> None:
        """注册 provider 专属参数, 默认无."""

    @classmethod
    @abstractmethod
    def create(cls, params: Mapping[str, Any]) -> "Provider":
        """从参数字典构造 provider 实例, 未知键应被忽略."""

    @abstractmethod
    def fetch(self) -> dict[str, Any]:
        """执行提取, 返回完整配置 dict, 交由 CLI 裁剪为订阅格式."""


def to_subscription(config: dict[str, Any]) -> dict[str, Any]:
    """剥离运行时配置, 只保留订阅四要素(保留 dns/hosts 供选用)."""
    slim = {k: config[k] for k in SUBSCRIPTION_KEYS if config.get(k)}
    if not slim.get("proxies"):
        raise ProviderError("配置中没有 proxies, 无法生成订阅")
    return slim
