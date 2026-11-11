from typing import Any

from pydantic import BaseModel, Field, SecretStr, model_validator

from ..base import Provider, ProviderError
from . import maomaoyun_api as api
from .maomaoyun_api import PanelError
from .maomaoyun_crypto import DEFAULT_AES_KEY, decrypt_payload, parse_config


def _reveal(v: SecretStr | None) -> str | None:
    return v.get_secret_value() if v is not None else None


class MaomaoYunParams(BaseModel):
    """猫猫云认证参数: 订阅 token 与账号密码二选一."""

    subscribe_token: SecretStr | None = Field(None, description="订阅 token(32 位十六进制)")
    user: str | None = Field(None, description="面板账号(邮箱)")
    password: SecretStr | None = Field(None, description="面板密码")

    @staticmethod
    def _filled(v: SecretStr | None) -> bool:
        return v is not None and bool(v.get_secret_value())

    @model_validator(mode="after")
    def _auth_xor(self) -> "MaomaoYunParams":
        if self._filled(self.subscribe_token):
            return self  # token 优先, user/password 不需要
        if self.user and self._filled(self.password):
            return self  # 账号密码完整
        if self.user or self._filled(self.password):
            raise ValueError("账号密码登录需要 --user 与 --password 同时提供")
        raise ValueError("需要 --subscribe-token, 或 --user 与 --password")


class MaomaoYunProvider(Provider):
    name = "maomaoyun"
    summary = "猫猫云(api.brfcdu.cn): 订阅 token 或 账号密码, 二选一"
    params_model = MaomaoYunParams

    def __init__(self, params: MaomaoYunParams) -> None:
        self.subscribe_token = _reveal(params.subscribe_token)
        self.user = params.user
        self.password = _reveal(params.password)

    @classmethod
    def create(cls, params: BaseModel) -> "MaomaoYunProvider":
        if not isinstance(params, MaomaoYunParams):
            raise TypeError(f"{cls.name} 需要 MaomaoYunParams, 得到 {type(params).__name__}")
        return cls(params)

    def fetch(self) -> dict[str, Any]:
        try:
            if not self.subscribe_token:
                assert self.user and self.password  # MaomaoYunParams 已校验二选一
                self.subscribe_token = api.login(self.user, self.password).subscribe_token
            raw = api.fetch_payload(self.subscribe_token)
            return parse_config(decrypt_payload(raw, DEFAULT_AES_KEY))
        except PanelError as e:
            raise ProviderError(str(e)) from e
