"""REST 传输层: 把全部 provider 暴露为 HTTP 端点.

端点由 PROVIDERS 注册表在导入期枚举: GET /{provider}?<params>&full=true,
成功返回 text/yaml; 模型校验失败 422(FastAPI 默认), fetch() 运行期参数问题
400, 上游网络/面板/解密失败 502. 参数契约见各 provider 的 params_model.
"""

import sys
from collections.abc import Callable
from typing import Annotated, Any

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

from . import PROVIDERS
from .base import Provider, ProviderArgumentError, ProviderError, to_subscription

app = FastAPI(
    title="subunlock",
    description="从机场魔改客户端提取标准 Clash 订阅(详细说明见 README)",
)


def _dump_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=10000)


def _make_endpoint(provider_cls: type[Provider]) -> Callable[..., Response]:
    params_model = provider_cls.params_model

    def extract(params: Any, full: bool = False) -> Response:
        provider = provider_cls.create(params)
        try:
            config = provider.fetch()
            data = config if full else to_subscription(config)
        except ProviderArgumentError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except ProviderError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        headers = (
            {"subscription-userinfo": provider.subscription_userinfo}
            if provider.subscription_userinfo
            else None
        )
        return Response(content=_dump_yaml(data), media_type="text/yaml", headers=headers)

    # 运行时注入动态参数模型, FastAPI 据此生成 Query 校验与 OpenAPI schema
    extract.__annotations__["params"] = Annotated[params_model, Query()]
    extract.__name__ = f"extract_{provider_cls.name}"
    extract.__doc__ = provider_cls.summary or None
    return extract


for _name, _cls in sorted(PROVIDERS.items()):
    app.add_api_route(f"/{_name}", _make_endpoint(_cls), methods=["GET"],
                      name=f"extract_{_name}")


def serve_main(*, host: str = "127.0.0.1", port: int = 8000) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "错误: REST 服务需要 server 依赖组: pip install 'subunlock[server]'",
            file=sys.stderr,
        )
        return 2
    uvicorn.run("subunlock.server:app", host=host, port=port, log_level="info")
    return 0
