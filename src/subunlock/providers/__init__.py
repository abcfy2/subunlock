"""扩展新 provider: 继承 Provider, 实现 fetch() 后在 cli.py 的 PROVIDERS 注册即可."""

from ..base import Provider, ProviderError, to_subscription
from .maomaoyun import MaomaoYunProvider

__all__ = ["MaomaoYunProvider", "Provider", "ProviderError", "to_subscription"]
