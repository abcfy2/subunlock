"""扩展新 provider: 声明 params_model 参数契约, 继承 Provider 实现 create()/fetch(),
在 src/subunlock/__init__.py 的 PROVIDERS 注册即可, CLI 与 REST 自动生效."""

from ..base import Provider, ProviderError, to_subscription
from .maomaoyun import MaomaoYunProvider

__all__ = ["MaomaoYunProvider", "Provider", "ProviderError", "to_subscription"]
