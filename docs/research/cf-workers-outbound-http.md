# Cloudflare Python Workers 出站 HTTP 调研

> 调研日期：2026-11-11。范围：Python Workers（workerd + Pyodide）中的 HTTPS 出站请求；区分标准库 socket/urllib 与已适配的客户端库。依据包括 Cloudflare/Pyodide 一手文档、运行时 SDK 源码、官方示例和上游项目资料；结论不等同于对 subunlock 当前 lockfile 的运行时验证。

## ① 结论表

| 方案 | workerd 可用性 | 维护状态 | subunlock 适配成本 |
|---|---|---|---|
| `pyodide.http.pyfetch` | **可用**：Pyodide 异步 HTTP，基于运行时 Fetch API；本项目既有实测已成功。 | Pyodide 官方 API。 | 低；符合 async 重构，响应需异步读取，使用 Pyodide 响应对象。 |
| `workers.fetch` | **可用且更贴近 Workers SDK**：运行时 SDK 对 `pyfetch` 的包装，调用 Cloudflare Workers Fetch。 | Cloudflare `workers-runtime-sdk`。 | 低；加 SDK 依赖/Workers 专用适配，能处理 SDK Request/Response 类型。 |
| `pyodide-http` | **不适合作为 workerd 方案**：[INFERENCE] 其同步请求适配依赖浏览器 `XMLHttpRequest`；仓库没有 workerd 支持声明或验证。 | PyPI 最新 0.2.2，发布于 2025-02-05；httpx 支持请求仍开放。 | 高风险；不能补上 httpx，也不能证明可在 workerd 使用。 |
| `pyodide-httpx` | **浏览器 Pyodide 可用性有演示，workerd 未验证**；不得据此视作 Workers transport。 | 独立小型社区包（约 3 stars），非 Cloudflare 官方支持。 | 中高；涉及 monkeypatch、Pyodide API 和平台兼容性维护。 |
| httpx / aiohttp 现成集成 | **有 Cloudflare 当前一手资料支持的 HTTP 客户端路由方案**；官方 2026 GA 说明上游改造令 requests/httpx 等客户端可通过 JS Fetch 工作。官方示例已测试 `requests`、`urllib3`、`httpx.Client`。 | Cloudflare 文档、运行时与上游依赖共同维护。 | 低到中；依赖当前 Workers/Pyodide 版本行为，需在目标 `pywrangler dev` 环境确认。 |
| 自建 async `httpx` transport → `pyfetch`/`workers.fetch` | 理论可行，但此轮未找到可直接依赖且维护活跃的现成 pyfetch transport。 | 自维护。 | 中高；需要映射请求/响应头、body、异常、redirect、timeout、streaming 等语义。 |
| `urllib.request` / 原始 socket | **本项目既有实测失败**：HTTPS TLS 握手失败并报 `RemoteDisconnected`。不能和官方后续支持的 `requests`/`httpx` 客户端混为一谈。 | 标准库本身维护，但其 WASM TLS 约束不由本项目解决。 | 不适用；不要沿用该出站路径。 |

## ② 分方案详述

### 1. `pyodide-http`：requests/urllib 补丁，不是 httpx transport

`pyodide-http` README 明确说明：普通（非流式）请求被替换为 `XMLHttpRequest`；流式 requests 使用 browser Web Worker + `fetch`，需要 cross-origin isolation，不能满足条件时会退化为先获取完整内容再包装成内存流。其支持清单是 requests、urllib；README 没有列出 httpx。来源：[项目 README](https://github.com/koenvo/pyodide-http)、[核心实现](https://github.com/koenvo/pyodide-http/blob/main/pyodide_http/_core.py)。

PyPI 当前列出的最新版本为 **0.2.2**，发布于 **2025-02-05**；[PyPI 项目页](https://pypi.org/project/pyodide-http/)、[PyPI release feed](https://pypi.org/rss/project/pyodide-http/releases.xml)。上游 [Issue #37](https://github.com/koenvo/pyodide-http/issues/37)（仍 open，最近更新 2025-05-31）仍是讨论 httpx 支持的 issue；maintainer 表示尚待回头实现。它不是 `httpx.AsyncTransport`，也没有对 httpx 的官方 monkeypatch 集成。

Pyodide 官方 `pyodide.http` 文档把 `pyxhr` 描述为基于 `XMLHttpRequest` 的同步客户端；Pyodide 的 WebAssembly 约束与 browser API 模型不构成 workerd 中存在 XHR 的证据。[Pyodide HTTP API](https://pyodide.org/en/stable/usage/api/python-api/http.html)；[Pyodide WASM constraints](https://pyodide.org/en/stable/usage/wasm-constraints.html)。**[INFERENCE]** workerd 并非浏览器 Window 环境，因此针对 XHR 的同步补丁不能直接认为可用；本轮未找到该包的 workerd 适配声明或 Cloudflare Workers 使用案例。特别是 subunlock 当前已有 urllib HTTPS 失败实测，不能把 pyodide-http 当成对该 TLS 问题的已验证修复。

### 2. `workers.fetch` 与 `pyfetch` 的关系

Cloudflare 官方 Fetch API 文档直接给出 Python 用法：`from workers import ... fetch`，在 handler 内 `await fetch(url, headers=...)`。Fetch 必须在 handler/request 上下文中调用，不能在 module global scope 发请求。[Workers Fetch 文档](https://developers.cloudflare.com/workers/runtime-apis/fetch/)。

`workers.fetch` 源码会将 SDK `Request` 解包为 JS Request，把 `HTTPMethod` 枚举转为字符串，然后 `await _pyfetch_patched(...)`；除对早期 Pyodide 版本的兼容分支外，`_pyfetch_patched` 就是 `pyodide.http.pyfetch`。返回值再包成 SDK 的 `workers.Response`。[`fetch.py`](https://github.com/cloudflare/workers-py/blob/main/packages/runtime-sdk/src/workers/fetch.py)。换言之：**它不是第二套网络栈，而是对 Pyodide Fetch 及 Cloudflare Request/Response 对象的类型友好封装。**

目前 SDK `FetchKwargs` 声明的选项为 `headers`、`body`、`method`、`redirect`、Cloudflare `cf` 属性和 `fetcher`；可接受字符串/SDK Request/JS Request，body 支持字符串、SDK FormData 及若干 JS body 类型。[`types.py`](https://github.com/cloudflare/workers-py/blob/main/packages/runtime-sdk/src/workers/types.py)、[`request.py`](https://github.com/cloudflare/workers-py/blob/main/packages/runtime-sdk/src/workers/request.py)。SDK Response/Pyodide Fetch 响应的正文读取为 async（如 `await response.text()` / `.bytes()`）。[`response.py`](https://github.com/cloudflare/workers-py/blob/main/packages/runtime-sdk/src/workers/response.py)。

Cloudflare 没有在引用页面声称“只准用 workers.fetch”；它是官方 SDK 对 Workers Fetch 的公开 Python API，而 `pyfetch` 是底层 Pyodide API。新写仅面向 Python Workers 的代码优先 `workers.fetch`，可使用官方封装和类型；如需最小 Pyodide 直接调用则 `pyfetch` 同样是官方支持的 Pyodide API。不要直接用裸 `js.fetch` 来绕过 SDK 对类型和 Request/Response 的包装，除非确有 FFI 特定需求。

### 3. 社区及官方真实出网案例

以下均为可见源码/官方公开材料中的实现形态；只有第一项是专门的外部 HTTP 客户端 smoke example，其他项是官方披露的应用模式，不能推断它们验证了 subunlock 面板或其全部语义。

1. **官方 `sync-http-clients` 示例：requests** — `/sync` 调用 `requests.get(TARGET_URL, timeout=10)`，普通同步客户端；仓库 README 称这些客户端在当前 runtime 可用。[示例 README](https://github.com/cloudflare/python-workers-examples/blob/main/sync-http-clients/README.md)、[entry.py](https://github.com/cloudflare/python-workers-examples/blob/main/sync-http-clients/src/entry.py)。
2. **同一官方示例：urllib3** — 用 `urllib3.PoolManager().request("GET", TARGET_URL, timeout=10.0)` 发起请求并读取 response body。[entry.py](https://github.com/cloudflare/python-workers-examples/blob/main/sync-http-clients/src/entry.py)。
3. **同一官方示例：httpx 同步客户端** — `with httpx.Client() as client: client.get(TARGET_URL, timeout=10.0)`；示例的 `pyproject.toml` pin 下限为 `httpx>=0.28.0`。[entry.py](https://github.com/cloudflare/python-workers-examples/blob/main/sync-http-clients/src/entry.py)、[pyproject.toml](https://github.com/cloudflare/python-workers-examples/blob/main/sync-http-clients/pyproject.toml)。
4. **Cloudflare GA 公布的 AI/HTTP 客户端模式** — Cloudflare 说明 `openai`、`langchain`、`mcp` 等依赖 `requests`/`httpx` 的库现可经 JS Fetch 在 Python Workers 发出请求；博客给出 LangChain async 调用例子。该材料是 Cloudflare 对其平台能力的声明，不是本调研独立部署验证。[Cloudflare Python Workers GA](https://blog.cloudflare.com/python-workers-ga/)。

官方示例的第一至三项是一个 repo 的三种 client，并非三个不同第三方项目。这个具体 repo 对 subunlock 很有价值：当前 Workers runtime 官方主动展示同步 requests/httpx/urllib3，说明“所有同步 requests/httpx 都会因无线程而失败”已不是当前官方结论；但它**不推翻**本项目既有 `urllib.request`/TLS 失败实测，因为示例明确排除 `urllib.request`、`http.client` 这种标准库原始 socket 路径。运行时改变/版本漂移应以锁定的 `workers-py` + `workers-runtime-sdk` + `compatibility_date` 为准。

### 4. `httpx` transport / monkeypatch 现成方案

没有发现 `pyodide-http` 已支持 httpx：其 Issue #37 仍开放。确有独立包 [`pyodide-httpx`](https://github.com/CNSeniorious000/pyodide-httpx)，API 是 `patch_httpx()`；上游讨论者的实现把 `httpx.AsyncClient._send_single_request` monkeypatch 为 `pyfetch` 请求，并把 JS ReadableStream 包成 async byte stream。[源码 `_async.py`](https://github.com/CNSeniorious000/pyodide-httpx/blob/main/src/pyodide_httpx/_async.py)、[包 README](https://github.com/CNSeniorious000/pyodide-httpx)、[Pyodide Discussion #4999](https://github.com/pyodide/pyodide/discussions/4999)。这不是一个普适的公开 `httpx.AsyncBaseTransport` 实现，而是侵入 `httpx` 私有实现的 monkeypatch；同步接口实现还依赖 `run_sync` / JSPI 能力。仓库当前 metadata 是 0.2.0、依赖 `httpx>=0.28`，没有 Cloudflare/workerd 兼容声明。[pyproject.toml](https://github.com/CNSeniorious000/pyodide-httpx/blob/main/pyproject.toml)。

另外，`httpx` 上游讨论过基于 urllib3 的自定义 transport。该讨论明确背景是 Pyodide 环境下原生 httpx 连接失败、用自定义 `httpx.BaseTransport` 接入 urllib3；示例需额外安装 wasm urllib3 wheel，且讨论指出需要随 httpcore 重构更新。[HTTPX Discussion #2994](https://github.com/encode/httpx/discussions/2994)。这不是 pyfetch transport，也不是维护中的现成依赖。Cloudflare 当前官方 runtime 已提供 `workers.fetch`，并且官方示例中 `httpx.Client` 可出网；因此新建 transport 既重复适配又有语义维护成本，只有必须通过 `httpx.AsyncClient` 接口统一业务代码时才值得评估。

### 5. aiohttp、socket 与 TLS 的状态

Cloudflare GA 博客解释了 Python Workers 网络机制：WASM 内常规 POSIX socket syscall 原本是 stub；Cloudflare 为 Hyperdrive 实现了通过 Workers `connect()` 的 socket syscall bridge，同时为 `requests`/`httpx` 一类 HTTP 客户端 upstream 改造，使其在 WebAssembly 环境走 JS Fetch。它明确区分原始 TCP socket 连接与 HTTP client fetch 路径。[Cloudflare Python Workers GA](https://blog.cloudflare.com/python-workers-ga/)、[TCP sockets API](https://developers.cloudflare.com/workers/runtime-apis/tcp-sockets/)。

因此当前不能简单断言“Python Workers 完全无 socket”；官方 2026 资料指出 socket bridge 已用于数据库/TCP 情形。反之也不能据此推断任意 `ssl`/stdlib `urllib` 全栈兼容：项目既有 urllib HTTPS 实测仍是 TLS 失败基线；官方的 HTTP 客户端支持路径是专门被适配/上游改造的客户端，不能替代本项目在实际 lockfile 与面板 HTTPS 下的复核。**[INFERENCE]** 对 subunlock 而言，应把这视为版本依赖的兼容性边界而不是静态保证。

**aiohttp：** Cloudflare 的官方 Python 包生态政策是只承诺可部署的纯 Python wheel、PyEmscripten wheel 与 Pyodide package；每个依赖仍需逐项确认。[Python package support](https://developers.cloudflare.com/workers/languages/python/packages/)。本轮未找到 Cloudflare 官方 Python Workers 示例使用 `aiohttp`，亦未找到 `workers-runtime-sdk` 对 aiohttp 的专门 adapter。aiohttp 是带原生扩展/平台适配细节的包；不能因 async API 或 PyPI 源码包存在就断言适配当前 Python 3.14 PyEmscripten/workerd。**[INFERENCE]** 对本项目视为未证实，不推荐比 `workers.fetch` 更先选它。Cloudflare GA 所说 HTTP clients 路由 JS Fetch 的一手确认足以支持其明示的 requests/httpx，但不等价于 aiohttp 的明确支持承诺。

**Roadmap/issue 线索：** Cloudflare GA 博客不是计划开放传统任意 TCP socket 的路线声明，而是描述 `connect()` API + 运行时适配层；Workers TCP Sockets 文档规定特定 TCP socket API。[官方 TCP Sockets API](https://developers.cloudflare.com/workers/runtime-apis/tcp-sockets/)。本轮检索未找到明确承诺“为 Python Workers 提供标准 OpenSSL/任意 socket 兼容”的路线图/issue；工作方向只能按已公布的 `connect()`/Fetch 运行时 API判断，不能推测 future TLS 或 socket roadmap。

### 6. 本地开发及 remote dev

官方 Python 项目指南的标准命令是 `uv run pywrangler dev`；官方 FastAPI 文档也是这样运行，并且 Python Workers Examples README 说明 pywrangler dev 启动的本地开发服务由开源 `workerd` 驱动。[Python Workers](https://developers.cloudflare.com/workers/languages/python/)、[FastAPI](https://developers.cloudflare.com/workers/languages/python/packages/fastapi/)、[examples README](https://github.com/cloudflare/python-workers-examples)。本地 Worker 路径和生产 runtime 同为 workerd/Pyodide，适合作为出站请求回归环境；不能用 CPython 本地 FastAPI server 代替该验证。

Wrangler 常规本地开发支持本地模拟与逐 binding 配置 remote bindings；后者只把绑定指向云端资源，Worker 代码本身仍在本地执行。[Local development](https://developers.cloudflare.com/workers/local-development/)。文档称旧式 `wrangler dev --remote` 会把代码放到 Cloudflare 执行，但 Python Workers 页面明确推荐 pywrangler dev，没有给出 Python `--remote` 用法/兼容承诺。pywrangler 将 Wrangler CLI 包装为 Python 项目的工具，并可转发 Wrangler 命令；但仅凭该转发不应推断 remote execution 与 Python bootstrap 必然受支持。[workers-py](https://github.com/cloudflare/workers-py)、[Wrangler local dev](https://developers.cloudflare.com/workers/local-development/)。所以 remote dev 对 Python 出站验证结论为**未证实**；安全稳妥路径仍是 pywrangler dev 本地 workerd。

本轮没有查到 Cloudflare Python Workers 特定的出站 HTTP mock 官方 recipe。可行验证要点（建议，不是已检验方案）：`pywrangler dev` 下用隔离测试 Worker/可控 HTTPS echo endpoint 验证 TLS、请求头、状态、重定向、正文和超时；单元测试可将上游请求依赖通过现有 provider seam 注入假响应，但这种 fake 不能替代 workerd 的真实出网探针。避免把“remote binding”误当作任意外网请求的 mock。

## ③ 对 subunlock 的推荐

按当前已知约束比较：

| 选项 | 优点 | 代价/风险 | 判断 |
|---|---|---|---|
| **直接 `pyfetch` 分支** | 项目已在 Workers 上实测成功；最小依赖、异步可用；与该项目已有的 async transport 分支相符。 | API/response 和 CPython httpx 不同；需要用平台判断选传输实现。 | **首选保留/完成**：稳定且证据最直接。 |
| `workers.fetch` | Cloudflare 官方 SDK 公开 API；内部包装 pyfetch；Workers Request/Response/header/body 类型更原生。 | Workers SDK 依赖；可移植性低于底层 Pyodide API。 | **若现有分支需用官方 SDK，则优先替代裸 pyfetch**；不是另一套网络栈，性能/能力不应想当然不同。 |
| `pyodide-http` | 浏览器上为 requests/urllib 提供较熟悉的同步接口。 | XHR/browser 假设；httpx 不支持；未证实 workerd 可用，且最新版本发布时间较早。 | **不采用**。 |
| 自建 pyfetch/httpx async transport | 可以保留业务侧 httpx AsyncClient 习惯与模型。 | 需要实现并持续维护 transport；社区方案 monkeypatch 私有 API，无 workerd 证明。 | 暂不做；除非取消现有传输抽象、明确需要 httpx 生态功能。 |
| Workers 当前原生适配的 httpx 客户端 | Cloudflare 现行材料和官方 sync example 证明客户端可使用 Fetch 通路；改动可能最少。 | 要复核当前 pywrangler / sdk 版本的 exact compatibility；官方 example 是 sync `httpx.Client`，不是本项目目标版本全部 API 的担保。 | **作为候选对照测试，不足以取代已经成功的 pyfetch 分支。** |

**推荐结论：** 保持既定的双实现设计（Pyodide/Workers 走 Fetch，普通 CPython 走 httpx async）。在 Workers 分支优先采用官方 `workers.fetch`（如果当前锁定 SDK 可用且接口适合 provider），否则直接用已经实测成功的 `pyfetch`；不引入 `pyodide-http`，也不为此临时构造自定义 httpx transport。Cloudflare 现在确实支持额外的客户端库路径，未来可以作为依赖/代码复杂度比较，但要先对当前 Workers SDK 版本做面板端端到端测试。无论采用哪种都保留 provider seam 与统一 async 抽象，避免 `urllib.request` 回退。

## ④ 未决问题

1. 官方 `sync-http-clients` 当前使用的 workers-py/runtime-sdk 版本和其测试执行矩阵，是否与 subunlock 当前锁定版本及 Pyodide 3.14.2 一致？示例兼容日期是 2025-11-02，需查看示例仓库历史或实际执行版本；本调研未执行部署验证。
2. 官方 SDK Fetch 与 `pyfetch` 针对请求 timeout、取消、重定向、连接复用、TLS 错误映射、响应流读取的差异，是否满足 subunlock `fetch_payload` 的 retry/UA 轮换语义？SDK 当前类型声明没有显式 `timeout` 字段；仍需在目标 SDK 版本确认运行时参数限制。
3. Cloudflare GA 博客说 `requests`/`httpx` 等已上游适配；但该 statements 未专门列 aiohttp，也未给出版本矩阵。若要使用 aiohttp，需要从 PyEmscripten wheel/Cloudflare 官方当前 docs 找出版本级支持证据并在 pywrangler dev 验证。
4. Cloudflare 是否会为 Python Workers 标准库 `ssl`/`urllib` 提供正式支持承诺，及 pywrangler remote execution 是否支持 Python entrypoint，本轮没有可引用的一手承诺。
5. 当前示例里同步 requests/httpx 可运行，但本项目原来的 `urllib.request` HTTPS 曾失败。需在 subunlock 具体目标 runtime 做单独短探针来区分是标准库 SSL 根因、Workers runtime 升级后的行为变化，还是 SDK 的特定 HTTP client shim；不能用示例推论覆盖实测基线。
