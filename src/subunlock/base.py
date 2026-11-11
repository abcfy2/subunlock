from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel

SUBSCRIPTION_KEYS = ("proxies", "proxy-groups", "rules", "dns", "hosts")


class ProviderError(RuntimeError):
    """provider 运行时错误(网络/面板/解密失败), REST 侧对应 502, CLI 以 1 退出."""


class ProviderArgumentError(ProviderError):
    """fetch() 运行期参数问题(如登录流程中 token 缺失), REST 侧对应 400, CLI 以 2 退出.

    声明期参数校验(必填/二选一/格式)由 Provider.params_model 的 pydantic 模型
    承担, 不走这里.
    """


class Provider(ABC):
    """订阅提取器抽象: 从机场魔改客户端还原标准 Clash 订阅配置.

    子类职责:
    - name/summary: 注册名与帮助展示的一行说明
    - params_model: pydantic 参数模型, 参数契约的唯一声明处; CLI 据此生成
      argparse 选项与帮助, REST 据此生成路由参数与 OpenAPI schema
    - create(): 由校验后的参数模型实例构造 provider 实例
    - fetch(): 执行提取, 返回完整配置 dict
    传输层(cli.py/server.py)只负责收集原始参数并调用 params_model 校验,
    provider 契约与传输方式无关.
    """

    name: ClassVar[str]
    summary: ClassVar[str] = ""
    params_model: ClassVar[type[BaseModel]]
    # fetch() 期间捕获的上游 subscription-userinfo 头(流量/到期), 传输层透传给
    # Clash 客户端; CLI 忽略, REST 转为响应头. 无法提供时保持 None.
    subscription_userinfo: str | None = None

    @classmethod
    @abstractmethod
    def create(cls, params: BaseModel) -> "Provider":
        """由校验后的参数模型实例构造 provider, 实现中窄化为具体 Params 类型."""

    @abstractmethod
    def fetch(self) -> dict[str, Any]:
        """执行提取, 返回完整配置 dict, 交由传输层裁剪为订阅格式."""


def to_subscription(config: dict[str, Any]) -> dict[str, Any]:
    """剥离运行时配置, 只保留订阅四要素(保留 dns/hosts 供选用)."""
    slim = {k: config[k] for k in SUBSCRIPTION_KEYS if config.get(k)}
    if not slim.get("proxies"):
        raise ProviderError("配置中没有 proxies, 无法生成订阅")
    return slim
