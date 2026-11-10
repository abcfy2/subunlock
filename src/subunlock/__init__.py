from .base import (
    SUBSCRIPTION_KEYS,
    Provider,
    ProviderArgumentError,
    ProviderError,
    to_subscription,
)
from .providers.maomaoyun import MaomaoYunProvider

PROVIDERS: dict[str, type[Provider]] = {
    MaomaoYunProvider.name: MaomaoYunProvider,
}

__all__ = [
    "PROVIDERS",
    "SUBSCRIPTION_KEYS",
    "MaomaoYunProvider",
    "Provider",
    "ProviderArgumentError",
    "ProviderError",
    "to_subscription",
]
