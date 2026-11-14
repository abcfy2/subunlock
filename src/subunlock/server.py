"""REST 传输层: 把全部 provider 暴露为 HTTP 端点.

端点由 PROVIDERS 注册表在导入期枚举: GET /{provider}?<params>&full=true,
成功返回 text/yaml; 模型校验失败 422(错误体剥 input/url, 不回显参数值),
参数/凭证问题 400
(token 缺失、账密被拒), 上游网络/面板/解密失败 502. 参数契约见各 provider
的 params_model.

缓存: 机场内容分钟级不变, 成功响应带 `public, s-maxage` 供部署平台 CDN
(Vercel Edge Network / Cloudflare Workers Cache)或前置反代做短缓存;
已注册错误路径(422/400/404/502)统一 `no-store`, 防止失败详情毒化缓存窗口.
"""

import logging
import sys
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import PROVIDERS
from .base import Provider, ProviderArgumentError, ProviderError, extract_subscription

app = FastAPI(
    title="subunlock",
    description="从机场魔改客户端提取标准 Clash 订阅(详细说明见 README)",
)

_CACHE_TTL_SECONDS = 300
# s-maxage 给 CDN 且是 Vercel 函数 CDN 缓存的唯一开关(缺失=不缓存,每次执行函数);
# Vercel proxy 消费掉 s-maxage 后客户端只见 max-age(浏览器忽略 s-maxage).
# public 不写: RFC 9111 默认即可共享缓存, 其唯一作用是放行 Authorization 请求
# 的响应, 本端点凭证在 query 永不带该头.
_CACHE_CONTROL_SUCCESS = (f"max-age={_CACHE_TTL_SECONDS}, "
                          f"s-maxage={_CACHE_TTL_SECONDS}")
_CACHE_CONTROL_ERROR = "no-store"


class StripAccessQuery(logging.Filter):
    """access 行默认含完整 query(凭证随 URL 明文), formatter 前把
    record.args 第三项截到 path. 五元组布局是 uvicorn 0.54.0 的源码行为
    (非承诺稳定接口), 升级 uvicorn 需重核对."""

    def filter(self, record: logging.LogRecord) -> bool:
        if (
            record.name == "uvicorn.access"
            and isinstance(record.args, tuple)
            and len(record.args) == 5
        ):
            client_addr, method, full_path, http_version, status_code = record.args
            record.args = (
                client_addr,
                method,
                str(full_path).split("?", 1)[0],
                http_version,
                status_code,
            )
        return True


# dictConfig 可导入性要求 filter 类定义在模块顶层; httpx/httpcore 的
# INFO/DEBUG 请求行含完整上游 URL(带 token), 一并钉死在 WARNING.
_UVICORN_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"strip_access_query": {"()": "subunlock.server.StripAccessQuery"}},
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(levelprefix)s %(message)s",
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        },
    },
    "handlers": {
        "default": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "stream": "ext://sys.stderr",
        },
        "access": {
            "class": "logging.StreamHandler",
            "formatter": "access",
            "filters": ["strip_access_query"],
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
        "httpx": {"handlers": ["default"], "level": "WARNING", "propagate": False},
        "httpcore": {"handlers": ["default"], "level": "WARNING", "propagate": False},
    },
}


# 错误响应统一禁止缓存: 4xx/5xx 带请求上下文与失败详情, 若被平台/反代缓存
# 会把单次失败传播给后续同 URL 请求直到 TTL 过期. 两个 handler 复刻 FastAPI
# 默认错误体格式({"detail": ...}), 仅追加 no-store 头.
@app.exception_handler(StarletteHTTPException)
def _no_store_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": jsonable_encoder(exc.detail)},
        headers={**(exc.headers or {}), "Cache-Control": _CACHE_CONTROL_ERROR},
    )


@app.exception_handler(RequestValidationError)
def _no_store_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # pydantic 默认把原始输入回显在每条错误的 input 里, query 中的凭证
    # (password/token)会逐字进入 422 响应体; url 是 pydantic 文档链接,
    # 无调试价值一并剥掉. FastAPI 的 errors() 已预计算, 只能事后清洗.
    errors = [
        {k: v for k, v in err.items() if k not in ("input", "url")}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(errors)},
        headers={"Cache-Control": _CACHE_CONTROL_ERROR},
    )


def _make_endpoint(provider_cls: type[Provider]) -> Callable[..., Response]:
    params_model = provider_cls.params_model

    def extract(params: Any, full: bool = False) -> Response:
        try:
            sub = extract_subscription(provider_cls, params, full=full)
        except ProviderArgumentError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except ProviderError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        headers = {"Cache-Control": _CACHE_CONTROL_SUCCESS}
        if sub.subscription_userinfo:
            headers["subscription-userinfo"] = sub.subscription_userinfo
        return Response(content=sub.yaml_text, media_type="text/yaml", headers=headers)

    # 运行时注入动态参数模型, FastAPI 据此生成 Query 校验与 OpenAPI schema
    extract.__annotations__["params"] = Annotated[params_model, Query()]
    extract.__name__ = f"extract_{provider_cls.name}"
    extract.__doc__ = provider_cls.summary or None
    return extract


for _name, _cls in sorted(PROVIDERS.items()):
    app.add_api_route(f"/{_name}", _make_endpoint(_cls), methods=["GET"],
                      name=f"extract_{_name}")


def serve_main(*, host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "错误: REST 服务需要 server 依赖组: pip install 'subunlock[server]'",
            file=sys.stderr,
        )
        return 2
    # 自定义 log_config 时 log_level 参数失效, 级别已在 _UVICORN_LOG_CONFIG 声明
    uvicorn.run(
        "subunlock.server:app", host=host, port=port, reload=reload, log_config=_UVICORN_LOG_CONFIG
    )
    return 0
