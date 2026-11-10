import argparse
from collections.abc import Mapping
from typing import Any

from ..base import Provider, ProviderArgumentError
from . import maomaoyun_api as api
from .maomaoyun_crypto import DEFAULT_AES_KEY, decrypt_payload, parse_config


class MaomaoYunProvider(Provider):
    name = "maomaoyun"
    summary = "猫猫云(api.brfcdu.cn): 订阅 token 或 账号密码, 二选一"

    def __init__(
        self,
        *,
        user: str | None = None,
        password: str | None = None,
        subscribe_token: str | None = None,
    ) -> None:
        self.user = user
        self.password = password
        self.subscribe_token = subscribe_token
        if not self.subscribe_token and not (user and password):
            raise ProviderArgumentError("需要 --subscribe-token, 或 --user 与 --password")

    @classmethod
    def configure_args(cls, parser: argparse.ArgumentParser) -> None:
        group = parser.add_argument_group("认证方式(二选一)")
        group.add_argument("--subscribe-token", help="订阅 token(32 位十六进制)")
        group.add_argument("--user", help="面板账号(邮箱)")
        group.add_argument("--password", help="面板密码")

    @classmethod
    def create(cls, params: Mapping[str, Any]) -> "MaomaoYunProvider":
        return cls(
            user=params.get("user"),
            password=params.get("password"),
            subscribe_token=params.get("subscribe_token"),
        )

    def fetch(self) -> dict[str, Any]:
        if not self.subscribe_token:
            if not self.user or not self.password:
                raise ProviderArgumentError("需要 --subscribe-token, 或 --user 与 --password")
            self.subscribe_token = api.login(self.user, self.password).subscribe_token
        raw = api.fetch_payload(self.subscribe_token)
        return parse_config(decrypt_payload(raw, DEFAULT_AES_KEY))
