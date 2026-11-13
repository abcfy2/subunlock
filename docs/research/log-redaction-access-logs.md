# 请求日志与敏感 query 脱敏调研

> 调研日期：2026-11-14。范围：subunlock 现有 GET `/{provider}?<params>` 请求链路的应用框架、Uvicorn、Vercel、Cloudflare Workers、Pydantic/FastAPI 与 HTTPX 日志。仅依据项目锁文件、官方文档及上游源码；未运行应用、未请求上游。平台行为是平台自有日志面，不等于应用 logger 配置。来源访问日期：2026-11-14。

## 结论先行：分层边界

- **代码可控：**本地 `uvicorn.run()` 的 `uvicorn.access` 输出格式，可在 Uvicorn `log_config` 的 access handler filter/formatter 中丢掉 query、保留 method/path/status；应用可自定义 FastAPI 422 错误响应并避免把输入/异常细节返回或写入自有日志；可配置进程内 `httpx` / `httpcore` logger。可为未来应用日志增加统一 filter/middleware，但它不会改写平台请求日志。
- **平台不可由当前应用代码控制：**Vercel Runtime Logs 自己记录请求详情，字段明确包括 Search Params；Cloudflare Workers invocation logs / `wrangler tail` 的 `event.request.url` 是完整 URL，Workers Logs 可开关 invocation logs 或采样，但不是对 query 的字段级删改。ASGI middleware 与 Uvicorn logger 在平台日志采集之前/之外，不能保证抹除平台已采集的请求 URL。**[INFERENCE]** 在现有平台文档中未找到应用响应头、ASGI middleware 或 `uvicorn` `log_config` 控制这些平台记录字段的入口。
- 对 subunlock，boring 的优先顺序是：本地 Uvicorn access format 去 query；校验错误对客户端返回经过收口的错误内容；不记录 query / token /完整请求 URL。若要求托管平台日志也不含 query，单靠应用改动没有已文档化的保证，需按平台日志设置/产品支持单独评估。

## 1. FastAPI / Starlette 默认是否产生 HTTP access log？

**框架本身默认不写 HTTP access log。** FastAPI 的默认 HTTP 异常处理器直接构造响应；默认请求校验处理器将 `exc.errors()` 编码为 422 JSON，并未调用 logger。Starlette 负责 ASGI 请求处理/中间件/路由；HTTP access log 则由运行 ASGI 的服务器（本地 Uvicorn）发出，而不是 FastAPI/Starlette 自动注册的 logger。[FastAPI 默认异常处理器源码 0.162.2](https://github.com/fastapi/fastapi/blob/0.162.2/fastapi/exception_handlers.py)；[Starlette application source 1.10.0](https://github.com/encode/starlette/blob/1.10.0/starlette/applications.py)，访问于 2026-11-14。

这不是“进程中绝不会有日志”的承诺：第三方库、部署运行时、应用自定义代码/中间件仍可写 stdout/stderr 或 logging。项目目前 `src/` 无 `logging` 调用（仓库内检查事实）；FastAPI/Starlette 默认不等于部署平台无请求日志。

## 2. 本地 Uvicorn access log 与 query 剥除

项目 `uv.lock` 锁定 **Uvicorn 0.54.0**，`serve_main()` 调用 `uvicorn.run("subunlock.server:app", host=..., port=..., log_level="info")`。[项目 server.py](../../src/subunlock/server.py)；[项目 uv.lock](../../uv.lock)，访问于 2026-11-14。

Uvicorn 的默认 logging 配置定义 `uvicorn`, `uvicorn.error`, `uvicorn.access` 三个 logger；`uvicorn.access` 默认 INFO、绑定 stdout access handler，使用 `uvicorn.logging.AccessFormatter`。`uvicorn` 默认 stderr handler，`uvicorn.error` 继承 `uvicorn` 设置；传入的 `log_config` 默认为默认配置、`access_log=True`。[Uvicorn 0.54.0 `config.py`](https://github.com/encode/uvicorn/blob/0.54.0/uvicorn/config.py)；[Uvicorn logging settings](https://www.uvicorn.org/settings/#logging)，访问于 2026-11-14。

HTTP protocol 在发出 access 记录时调用 `uvicorn.access.info('%s - "%s %s HTTP/%s" %d', ...)`，五个 `record.args` 按序是 `(client_addr, method, full_path, http_version, status_code)`；`full_path` 是 query string 未删减的 `path?query`。`AccessFormatter.formatMessage()` 将这些参数解包，创建 `client_addr`、`request_line`（method + full_path + HTTP 版本）、`status_code` 字段。因此默认格式中的 request line 会包含 secret-in-query。[Uvicorn 0.54.0 `h11_impl.py`](https://github.com/encode/uvicorn/blob/0.54.0/uvicorn/protocols/http/h11_impl.py)；[Uvicorn 0.54.0 `utils.py`](https://github.com/encode/uvicorn/blob/0.54.0/uvicorn/protocols/utils.py)；[Uvicorn 0.54.0 `logging.py`](https://github.com/encode/uvicorn/blob/0.54.0/uvicorn/logging.py)，访问于 2026-11-14。

Uvicorn 0.54.0 没有内建 query redaction 选项：其配置暴露 `log_config` / `access_log` 开关；默认 AccessFormatter 直接把 `full_path` 拼进 request line。[Uvicorn 0.54.0 config/source](https://github.com/encode/uvicorn/tree/0.54.0/uvicorn)，访问于 2026-11-14。

**可粘入 `serve_main()` 的 `log_config` 示例**（放在 `uvicorn.run()` 之前；filter 类需定义在 `subunlock.server` 模块顶层，确保 dictConfig 可导入）。Uvicorn access formatter 在 0.54.0 中已观测到五项 tuple 布局；该内部记录结构并非明确承诺的跨版本稳定 API，升级 Uvicorn 后应重新核对源码/测试日志输出。过滤器在格式化前只改写第三项 `full_path`，不依赖 formatter 私有实现：

```python
# module level in subunlock.server
import logging

class StripAccessQuery(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) == 5:
            client_addr, method, full_path, http_version, status_code = record.args
            record.args = (
                client_addr,
                method,
                str(full_path).split("?", 1)[0],
                http_version,
                status_code,
            )
        return True

# inside serve_main(), passed to uvicorn.run(..., log_config=log_config)
log_config = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"strip_access_query": {"()": "subunlock.server.StripAccessQuery"}},
    "formatters": {
        "default": {"()": "uvicorn.logging.DefaultFormatter", "fmt": "%(levelprefix)s %(message)s"},
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        },
    },
    "handlers": {
        "default": {"class": "logging.StreamHandler", "formatter": "default", "stream": "ext://sys.stderr"},
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
    },
}
```

This retains Uvicorn's usual client address + `METHOD /path HTTP/version` + status format while removing everything after `?`. To instead log only method/path/status, set access formatter format to e.g. `'%(method)s %(path)s %(status_code)s'` **only if a custom formatter creates those fields**; the stock `AccessFormatter` exposes `request_line`, not separate `method`/`path`, so the example intentionally keeps the compatible request-line shape. `access_log=False` disables all Uvicorn access entries, not just query values. Python `dictConfig` supports callable filters and logger/handler filters; the configured import string names the module-level filter class.[Python logging configuration](https://docs.python.org/3/library/logging.config.html#dictionary-schema-details)；[Uvicorn source](https://github.com/encode/uvicorn/tree/0.54.0/uvicorn)，访问于 2026-11-14。

**字段兼容性：**0.54.0 的 formatter 明确按 `record.args` 五元组解包，protocol 同版本显式提供相同五项；这是版本化源码证据，不是 Uvicorn 承诺的稳定扩展接口。不要写假设未来 tuple 任意变化的 formatter/filter；锁版本，并在升级时检查 `logging.py` 与 protocol access logger。`AccessFormatter` 可子类化/替换，但本示例不需要继承它；记录参数在 handler filter 处修剪可复用 stock formatter。[Uvicorn logging.py 0.54.0](https://github.com/encode/uvicorn/blob/0.54.0/uvicorn/logging.py)；[Uvicorn h11_impl.py 0.54.0](https://github.com/encode/uvicorn/blob/0.54.0/uvicorn/protocols/http/h11_impl.py)，访问于 2026-11-14。

## 3. Pydantic errors 与 FastAPI 422 错误响应

Pydantic v2 `ValidationError.errors(*, include_url=True, include_context=True, include_input=True)` 是官方 API 文档化的参数；设置 `include_input=False` 可不把错误输入值包含在 `ErrorDetails` 中，`include_url=False` 去掉错误文档 URL。[Pydantic `ValidationError.errors()` API](https://pydantic.dev/docs/validation/latest/api/pydantic-core/pydantic_core/#pydantic_core.ValidationError.errors)，访问于 2026-11-14。

但 FastAPI 的 `RequestValidationError.errors()` 不是原始 Pydantic `ValidationError.errors()`。FastAPI `ValidationException` 初始化时把已生成的错误序列存到 `_errors`，`errors(self)` 只返回它；默认 handler 随后执行 `jsonable_encoder(exc.errors())` 并将结果放进 `{"detail": ...}`。因此自定义 handler 收到的 `exc.errors()` 是已经算好的 errors list，不能对它补传 `include_input=False`；若要隐藏 input，应在 handler 中对 list 每项清理/白名单重建响应，或统一替换为不暴露具体输入值的错误文案。原始错误结构含 `input` 的语义见 Pydantic `ErrorDetails` 定义。[FastAPI 0.162.2 `exceptions.py`](https://github.com/fastapi/fastapi/blob/0.162.2/fastapi/exceptions.py)；[FastAPI 0.162.2 默认 handler](https://github.com/fastapi/fastapi/blob/0.162.2/fastapi/exception_handlers.py)；[Pydantic ErrorDetails](https://pydantic.dev/docs/validation/latest/api/pydantic-core/pydantic_core/#pydantic_core.ErrorDetails)，访问于 2026-11-14。

Pydantic 错误详情把输入值列为 error detail 字段；其文档示例显示默认 `errors()` 含具体 `input` 值。FastAPI 文档提供自定义 `RequestValidationError` exception handler 作为可覆盖默认响应的做法，但没有找到框架官方专门的“query secret 防日志泄漏”说明。[Pydantic error handling](https://pydantic.dev/docs/validation/latest/errors/errors/)；[FastAPI handling errors](https://fastapi.tiangolo.com/tutorial/handling-errors/#override-request-validation-exceptions)，访问于 2026-11-14。

## 4. Vercel Python Runtime 与 Request Logs

Vercel Runtime Logs 汇集 Function invocation 输出；每次写入 stdout（如 `print`）会成为独立日志项，stderr 按 error 级别处理（streaming function 场景亦保留相应级别映射）。因此 Python `print`、运行时导向 stdout/stderr 的输出和应用日志会进入运行时日志。[Vercel Runtime Logs](https://vercel.com/docs/logs/runtime)，访问于 2026-11-14。

更重要的是，Runtime Logs 请求记录自身有 Request Path、Search Params、method、status、host、request id 等字段；Search Params 是日志详情中的独立字段。Vercel 文档明确描述 `Request Path` 为实际 URL path，并单列请求的 Search Params。因此即使应用不打印 URL，平台 request logging 仍会记录 query 参数。[Vercel Runtime Logs — Log details](https://vercel.com/docs/logs/runtime#log-details)；[Vercel Runtime Logs — Request Path](https://vercel.com/docs/logs/runtime#request-path)，访问于 2026-11-14。

应用可以控制自己输出的 stdout/stderr 文本，但没有找到 Vercel Python Function API 让应用在请求处理时阻止或逐参数 redact 平台生成的 Search Params/Request Logs 字段。**[INFERENCE]** 因为该平台请求记录是 Request Log 元数据而不是应用写出的 log line，改 FastAPI handler、Uvicorn log formatter 或响应体都不能撤回平台已捕获的 Search Params；需要平台侧独立日志/隐私控制才能进一步改变保存策略，文档未证明具体可配置字段脱敏开关。

## 5. Cloudflare Workers（含 Python Workers）日志

Cloudflare Workers Logs 的配置位于 `wrangler.jsonc` `observability`：`enabled` 控制是否采集；`head_sampling_rate` 控制请求采样比例（默认 1，即全量）。Invocation logs 记录请求、响应及 metadata；Fetch invocation message 的格式是 `<Method> <URL>`。可在 `observability.logs.invocation_logs=false` 禁掉 invocation logs；这不是删掉 URL 一部分，而是关闭此类 invocation 记录。自定义 `console.log()` 也进入 Workers Logs。新 Worker 默认开启 observability。[Workers Logs — Enable/Invocation Logs/Sampling](https://developers.cloudflare.com/workers/observability/logs/workers-logs/)；[Wrangler observability configuration](https://developers.cloudflare.com/workers/observability/logs/workers-logs/#enable-workers-logs)，访问于 2026-11-14。

`wrangler tail` 的结构化输出示例明确包含 `event.request.url` 和 method；示例 URL 是完整请求 URL。Tail 输出还包含 `exceptions`、`logs` 字段，且实时日志覆盖 invocation logs、custom logs、errors、uncaught exceptions；因此 URL query 与未处理异常均可能通过平台日志面显示。[Cloudflare Real-time logs / wrangler tail](https://developers.cloudflare.com/workers/observability/logs/real-time-logs/)，访问于 2026-11-14。

Cloudflare Errors 文档说明 Workers Logs 可查看执行时发生的 uncaught exceptions；生产 Worker 若 error 阻止响应，会给客户端生成错误页；运行时错误由 logs 发现。此依据可证平台会记录未捕获异常事件，但不承诺 Python traceback/异常消息在所有日志导出形式中的确切格式。[Cloudflare Errors and exceptions](https://developers.cloudflare.com/workers/observability/errors/)；[Workers Logs](https://developers.cloudflare.com/workers/observability/logs/workers-logs/)，访问于 2026-11-14。针对 Python `asgi.entrypoint` 发生的具体未处理异常究竟以何种 Python traceback 文本出现，官方资料在此没有足够细节，故不作更强表述。

因此 `wrangler.jsonc` 可以停用 invocation logs 或降低采样率，但不能把原始 URL 中的 query 变为脱敏后的 path；运行时代码也无法通过 Python/ASGI logging 配置控制 Cloudflare 生成的 `event.request.url`。**[INFERENCE]** 若日志仍需保留请求元数据但隐藏 query，当前引用的 Cloudflare Wrangler observability/tail 文档没有 field-level URL redaction 选项；可确认的配置仅全关或采样，不是改写字段。

## 6. HTTPX / HTTPCore 请求日志

HTTPX 官方 logging 文档的 INFO 示例 logger 名为 **`httpx`**，消息形态 `HTTP Request: GET https://...`，包含请求完整 URL；网络底层细节由独立 `httpcore` logger 在 DEBUG 输出。HTTPX 文档示例需要显式 `logging.basicConfig(..., level=logging.DEBUG)` 或 `dictConfig` 设置 logger/handler 才会打印所列日志。[HTTPX logging](https://www.python-httpx.org/logging/)，访问于 2026-11-14。

在**没有其它日志配置/handler**的标准 Python logging 状态下，root 默认级别为 WARNING；`httpx` 新 logger 默认 NOTSET，会继承 root effective level，故其 INFO “HTTP Request” 不会产生日志输出。Python `logging.lastResort` 是没有 handler 时兜底的 `WARNING`-level `StreamHandler`，因此 WARNING 及以上才会从该 fallback 显示。需要注意 Uvicorn 等宿主可自定义 logging config；项目本地 Uvicorn 默认 root 无直接 handler且 `disable_existing_loggers=False`，HTTPX是否可见还取决于进程全局 logger/handler/运行环境配置。[Python logging levels/lastResort](https://docs.python.org/3/library/logging.html#logging.lastResort)；[Python logger hierarchy](https://docs.python.org/3/library/logging.html#logger-objects)；[Uvicorn logging config](https://github.com/encode/uvicorn/blob/0.54.0/uvicorn/config.py)，访问于 2026-11-14。

可移植的标准做法是用 `logging.config.dictConfig()` 按 logger 名配置阈值/handler（例如把 `httpx` 限到 WARNING 或关闭 `httpx` 的传播/输出），或使用 handler/filter 在输出前清理 URL 字段；别在 application code 中把完整 URL 拼进自有日志。HTTPX 文档也警告 DEBUG log 的确切格式可能随 httpx/httpcore 版本变化。需要完全不泄漏时把 `httpx` logger 限制在 WARNING，比依赖修改某一版具体 message 的 regex 更简单。[HTTPX logging configuration](https://www.python-httpx.org/logging/)；[Python logging filters/config](https://docs.python.org/3/library/logging.html#filter-objects)，访问于 2026-11-14。

## 7. 可移植 redaction 备选（非首选）

Python logging filter 可按记录字段或 message/args 清理，stdlib 支持把 filter 挂到 logger/handler；`contextvars` 可以在线程与 asyncio task 的上下文中传递 request-local 状态，Python Logging Cookbook 示例展示了为 async code 使用 context variables 并用 filter 增添上下文。[Python logging Filter objects](https://docs.python.org/3/library/logging.html#filter-objects)；[Logging Cookbook — contextvars](https://docs.python.org/3/howto/logging-cookbook.html#use-of-contextvars)，访问于 2026-11-14。

ASGI middleware 可观察每个 request scope（method/path/query string），并在调用应用期间设置/清理 request context；Starlette 官方 middleware 文档给出纯 ASGI middleware 的 `scope, receive, send` 入口模式。[Starlette middleware](https://www.starlette.io/middleware/)，访问于 2026-11-14。**[INFERENCE]** 若需全进程过滤，middleware 可从 query 参数登记本请求 secret 到 `ContextVar`，日志 filter 在 formatter/handler 前做替换；务必在 finally 重置 token，且避免把原始 registry/异常输入本身打印出来。它仅能影响经过 Python logging handlers 的记录，不影响 Vercel/Cloudflare 平台生成的请求事件，也不能阻止任意 `print` /第三方直接输出。

这类全局机制涉及 secret 收集生命周期、并发上下文、第三方 logger 字段变化，范围大于当前证据所需。对现状优先保持 boring 主线：本地 Uvicorn access formatter/filter 去 query；FastAPI validation error handler 收口 `input` /错误细节；httpx logger 不提高到 INFO/DEBUG。平台生成 URL 日志需认清边界，不把应用级 filter 描述成托管平台脱敏保证。

## 8. 来源索引

- Project: [server.py](../../src/subunlock/server.py), [uv.lock](../../uv.lock)。
- FastAPI 0.162.2: [exception handlers source](https://github.com/fastapi/fastapi/blob/0.162.2/fastapi/exception_handlers.py), [exceptions source](https://github.com/fastapi/fastapi/blob/0.162.2/fastapi/exceptions.py), [custom validation handler docs](https://fastapi.tiangolo.com/tutorial/handling-errors/#override-request-validation-exceptions)。
- Starlette: [1.10.0 source tree](https://github.com/encode/starlette/tree/1.10.0/starlette), [middleware docs](https://www.starlette.io/middleware/)。
- Uvicorn 0.54.0: [config.py](https://github.com/encode/uvicorn/blob/0.54.0/uvicorn/config.py), [logging.py](https://github.com/encode/uvicorn/blob/0.54.0/uvicorn/logging.py), [h11_impl.py](https://github.com/encode/uvicorn/blob/0.54.0/uvicorn/protocols/http/h11_impl.py), [utils.py](https://github.com/encode/uvicorn/blob/0.54.0/uvicorn/protocols/utils.py), [logging settings docs](https://www.uvicorn.org/settings/#logging)。
- Pydantic: [errors guide](https://pydantic.dev/docs/validation/latest/errors/errors/), [ValidationError API](https://pydantic.dev/docs/validation/latest/api/pydantic-core/pydantic_core/#pydantic_core.ValidationError.errors), [ErrorDetails API](https://pydantic.dev/docs/validation/latest/api/pydantic-core/pydantic_core/#pydantic_core.ErrorDetails)。
- Vercel: [Runtime Logs](https://vercel.com/docs/logs/runtime), [Observability](https://vercel.com/docs/observability)。
- Cloudflare: [Workers Logs](https://developers.cloudflare.com/workers/observability/logs/workers-logs/), [Real-time logs / wrangler tail](https://developers.cloudflare.com/workers/observability/logs/real-time-logs/), [Errors and exceptions](https://developers.cloudflare.com/workers/observability/errors/)。
- HTTPX: [Logging](https://www.python-httpx.org/logging/)。
- Python: [logging library](https://docs.python.org/3/library/logging.html), [logging cookbook](https://docs.python.org/3/howto/logging-cookbook.html)。
