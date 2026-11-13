"""订阅提取抽象: Provider 契约、错误分类与共享提取管线.

Provider 契约与传输方式无关: params_model 是参数契约的唯一声明处, cli 据此
生成 argparse 选项, server 据此生成 Query 校验与 OpenAPI schema. 传输层只
收集原始参数、调用 extract_subscription() 管线、把两个错误类型翻译为各自
的错误表示(CLI exit code / REST HTTP status).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel

SUBSCRIPTION_KEYS = ("proxies", "proxy-groups", "rules", "dns", "hosts")


class ProviderError(RuntimeError):
    """提取期运行失败: 上游网络/面板不可用、解密/解析失败等, 与调用参数无关."""


class ProviderArgumentError(RuntimeError):
    """调用参数在运行期不成立(如账密被面板拒绝、登录流程缺 token).

    有意不继承 ProviderError: 二者是并列的错误模式(参数错 vs 上游失败),
    传输层翻译互不影响, except 顺序无关.
    """


class Provider(ABC):
    """订阅提取器抽象: 从机场魔改客户端还原标准 Clash 订阅配置.

    子类职责:
    - name/summary: 注册名与帮助展示的一行说明
    - params_model: pydantic 参数模型, 参数契约的唯一声明处
    - create(): 由校验后的参数模型实例构造 provider 实例
    - fetch(): 执行提取, 返回完整配置 dict; 期间可设置 subscription_userinfo
      (上游配额头), 由管线随 Subscription 带回, 传输层不接触 provider 内部状态
    """

    name: ClassVar[str]
    summary: ClassVar[str] = ""
    params_model: ClassVar[type[BaseModel]]
    # 只在 fetch() 之后有定义; extract_subscription() 是唯一读取方.
    subscription_userinfo: str | None = None

    @classmethod
    @abstractmethod
    def create(cls, params: BaseModel) -> "Provider":
        """由校验后的参数模型实例构造 provider, 实现中窄化为具体 Params 类型."""

    @abstractmethod
    def fetch(self) -> dict[str, Any]:
        """执行提取, 返回完整配置 dict, 交由管线裁剪为订阅格式."""


def to_subscription(config: dict[str, Any]) -> dict[str, Any]:
    """剥离运行时配置, 只保留订阅四要素(保留 dns/hosts 供选用)."""
    slim = {k: config[k] for k in SUBSCRIPTION_KEYS if config.get(k)}
    if not slim.get("proxies"):
        raise ProviderError("配置中没有 proxies, 无法生成订阅")
    return slim


@dataclass(frozen=True)
class Subscription:
    """一次成功提取的产出: 可直接落盘/回传的 YAML 文本与结构摘要."""

    yaml_text: str
    subscription_userinfo: str | None
    proxies: int
    groups: int
    rules: int


def _dump_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=10000)


def extract_subscription(
    provider_cls: type[Provider], params: BaseModel, *, full: bool = False
) -> Subscription:
    """共享提取管线: create → fetch → 裁剪(非 full) → YAML 序列化.

    错误模式即 interface: ProviderArgumentError / ProviderError(二者无继承
    关系), 传输层各自翻译为 exit code 或 HTTP status.
    """
    provider = provider_cls.create(params)
    config = provider.fetch()
    data = config if full else to_subscription(config)
    return Subscription(
        yaml_text=_dump_yaml(data),
        subscription_userinfo=provider.subscription_userinfo,
        proxies=len(data.get("proxies") or []),
        groups=len(data.get("proxy-groups") or []),
        rules=len(data.get("rules") or []),
    )
