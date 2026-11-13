# CDN 缓存调研：Vercel Functions 与 Cloudflare Workers

> 调研日期：2026-11-13。范围：动态 GET API 的完整响应缓存、Worker 内部子请求缓存，以及当前 subunlock Python Workers/ASGI 架构可用的缓存路径。仅依据平台官方文档、官方示例和 Pyodide 官方 API 文档；未在生产环境部署验证。所有来源访问日期：2026-11-13。

## ① Cloudflare Workers

### 1. Worker 返回的 Response 是否自动进入 zone CDN cache？

**不能仅靠设置 `Cache-Control` 让 zone Cache Rules 缓存 Worker 直接返回的响应。** 传统 zone cache 交互模型中，Worker 在请求阶段先运行；只有 Worker 发起 `fetch()` 的子请求才继续经过 zone cache，响应回 Worker 后再发给访客。Cache Rule 必须匹配该子请求 URL 的属性，而不是原始访客 URL；Worker 直接合成的响应绕过这条 origin-fetch 缓存路径。[Workers 与 zone cache 交互](https://developers.cloudflare.com/cache/interaction-cloudflare-products/workers/)；[How the Cache works](https://developers.cloudflare.com/workers/reference/how-the-cache-works/)，访问于 2026-11-13。

Cloudflare 另有 **Workers Cache**（与传统 Cache API/zone cache 不同）：启用后在运行 Worker 前查缓存，Worker 返回的标准 `Cache-Control` 控制缓存性与 TTL。它是 Worker 自己的缓存，不受 zone Cache Rules、Page Rules 或 zone cache 设置控制。新文档描述的是 Wrangler `cache.enabled` 的 Workers Cache 配置；不能把它和旧机制混为一谈。[Workers Cache](https://developers.cloudflare.com/workers/cache/)；[Workers Cache configuration](https://developers.cloudflare.com/workers/cache/configuration/)，访问于 2026-11-13。

### 2. Cache API：workers.dev、自定义域、key、TTL、额度与调用成本

- **workers.dev 与 Cache API：官方现行 Cache API 页面明确承诺“部署到 custom domains”的 Worker 有可用 Cache API 操作，但该段没有明确确认或否认 `workers.dev`。** 因此不能把“workers.dev 上 `cache.put()` 是 no-op”说成现行文档已证实；此问题在现行文档中仍未明确。另一个较新的独立机制 Workers Cache 文档明确演示了对 `workers.dev` URL 请求的 `HIT`，所以不能将旧 Cache API 的平台限制误套到 Workers Cache。[Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/)；[Workers Cache quickstart](https://developers.cloudflare.com/workers/cache/)，访问于 2026-11-13。
- **自定义域：** Cache API 页面明确说明 Workers 自定义域可用；但是 Worker 若由 Cloudflare Access 保护，Cache API 当前不可用。`caches.default` 与 `caches.open(name)` 都是可用入口；官方 Python 示例也直接使用 `from js import ... caches`。[Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/)；[Python Cache API example](https://developers.cloudflare.com/workers/examples/cache-api/)；[Python FFI](https://developers.cloudflare.com/workers/languages/python/ffi/)，访问于 2026-11-13。
- **key 与 query：** Cache API 的 `Request`/URL 是 key，且 Cache API 页面说明 `.workers.dev` 默认 key 包含 query string（在 301/302 query redirect 的特例说明中）；Workers Cache 明确以 path + query 为 key，参数顺序不同会生成不同 key。Workers Cache 的默认 key 不含 hostname，GET 和 HEAD 共用 entry；也默认包含 Worker version。Cache API 本身也以传入 Request/URL 作为 key。不要删减或规范化含不同用户凭证的 query 参数后共用同一 key。[Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/)；[Workers Cache keys](https://developers.cloudflare.com/workers/cache/cache-keys/)，访问于 2026-11-13。
- **TTL：** Cache API 尊重传给 `put()` 的响应 `Cache-Control`、`Expires`、`ETag`、`Last-Modified` 等；官方示例用 `s-maxage=10`。Cache API 不支持 `stale-while-revalidate` / `stale-if-error`。返回 `Set-Cookie` 的响应默认不缓存；`Vary: *` 不允许 `put()`。Workers Cache 则尊重 Worker 返回的 `Cache-Control`；没有 Cache-Control/Expires 时对 200/203/204 默认 heuristic TTL 为 7200 秒，404 为 180 秒等，故应显式设 TTL 或 `private`/`no-store`。本轮引用的 Cache API 文档没有列出适用于所有 plan 的独立最大 TTL 数值；不推断无限期 TTL。[Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/)；[Workers Cache configuration](https://developers.cloudflare.com/workers/cache/configuration/)，访问于 2026-11-13。
- **对象大小与套餐：** 当前 Workers limits 页面说明缓存响应体适用 CDN cache limits：Free/Pro/Business 最大 512 MB，Enterprise 5 GB；Cache API 对过大或 Cache-Control 禁止存储的响应以 413 拒绝。这个是缓存对象大小限制，不能和 Worker 的入站 request body 限制混淆。Limits 页面没有显示 Cache API 专属套餐开通门槛；Cache API 有功能限制（例如 Access 保护与 tiered cache），不应将 `cf.cacheKey` 等其他功能的 Enterprise 限制误套给 Cache API。[Workers limits](https://developers.cloudflare.com/workers/platform/limits/)；[Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/)；[Cache using fetch](https://developers.cloudflare.com/workers/examples/cache-using-fetch/)，访问于 2026-11-13。
- **缓存命中是否仍执行 Worker：** Cache API 的 `cache.match()` 是由 Worker 主动调用的，因此每次请求仍必须进入 Worker；它只能让 Worker 在命中时省去后续工作。Workers Cache 则在 Worker 之前检查，hit 时直接回缓存响应，Worker 不执行；官方说明仍按正常 Workers 请求计费，但 hit 不消耗 CPU。[How the Cache works](https://developers.cloudflare.com/workers/reference/how-the-cache-works/)；[Workers Cache](https://developers.cloudflare.com/workers/cache/)，访问于 2026-11-13。

### 3. `fetch()` 的 `cf.cacheEverything` / `cf.cacheTtl` / `cf.cacheTtlByStatus`

这些选项控制 **Worker 的 `fetch()` 子请求**经过 Cloudflare cache 的行为，不是缓存 Worker 对用户的最终响应。Cloudflare 文档说：`fetch()` 可以使用自身 zone 的缓存，即使被请求的 URL 是非 Cloudflare 主机；第三方机场面板作为 subrequest target 时，因此可用该 zone-cache 路径。`cacheEverything: true` 让 GET/HEAD 无论文件类型均可缓存，但仍尊重 origin cache headers；`cacheTtl` 对 GET/HEAD 强制所设 TTL 并覆盖响应头；`cacheTtlByStatus` 依据状态码设置/覆盖 origin 指令，0 表示立即过期、负值表示不缓存。[Request `cf` options](https://developers.cloudflare.com/workers/runtime-apis/request/)；[How the Cache works](https://developers.cloudflare.com/workers/reference/how-the-cache-works/)；[Cache using fetch](https://developers.cloudflare.com/workers/examples/cache-using-fetch/)，访问于 2026-11-13。

`fetch()` 缓存属于发起子请求时经由的 cache；`cf` 对象在跨 account 请求时会被丢弃，某些选项只在同账户请求有效。外部上游走自己的 zone/cache 不可由当前 Worker 覆写；本题的 airport panel 属于第三方、非 Cloudflare origin，所以应把选项限于 Cloudflare 为该 subrequest 使用的缓存规则/缓存路径。Cache API 是单 colo 本地缓存；传统 fetch cache 支持 zone Tiered Cache（如该 zone 配置启用），因此可能跨 colo 复用，但官方资料没有保证任意第三方 URL 在任意配置下均是全局单副本。[Configuration](https://developers.cloudflare.com/workers/cache/configuration/)；[How the Cache works](https://developers.cloudflare.com/workers/reference/how-the-cache-works/)；[Request `cf` options](https://developers.cloudflare.com/workers/runtime-apis/request/)，访问于 2026-11-13。

对 `workers.dev` 上的 Worker 发起外部 `fetch()` 时，是否与挂在自有 zone 上的 Worker 具有相同的 zone-cache subrequest 行为，现行 cited 文档没有明确区分。Workers Cache 本身明确能用于 `workers.dev` 请求；不可据此推断传统 zone fetch cache 的 exact behavior。[Workers Cache](https://developers.cloudflare.com/workers/cache/)；[How the Cache works](https://developers.cloudflare.com/workers/reference/how-the-cache-works/)，访问于 2026-11-13。

**重要：**即使上游 subrequest 命中缓存，传统 flow 仍先执行 Worker，故只节省上游回源等待/数据处理；它不会像 Worker-response cache 那样跳过 Python ASGI app。`cf.cacheTtlByStatus` 支持明确对某些 status `0`/负数来规避错误缓存。[Request `cf` options](https://developers.cloudflare.com/workers/runtime-apis/request/)；[Workers 与 zone cache 交互](https://developers.cloudflare.com/cache/interaction-cloudflare-products/workers/)，访问于 2026-11-13。

### 4. Worker 响应的 Cache-Control / CDN-Cache-Control，及 Cache Rules

传统 zone Cache Rules 匹配的是 Worker `fetch()` 子请求而非原始用户请求；直接由 Worker 返回的内容不因 dashboard Cache Rule 自动变成 zone-cache entry。[Workers 与 zone cache 交互](https://developers.cloudflare.com/cache/interaction-cloudflare-products/workers/)，访问于 2026-11-13。

新的 Workers Cache 使用 `Cache-Control`（配置文档也列出 `cdn-cache-control`、`cloudflare-cdn-cache-control`）作为 Worker cache 控制面，但明确说明该 cache 不读取 zone Cache Rules。Cache API 则只能由 Worker 显式执行 `cache.put()` 才存入响应；这也是另一套机制。Workers Cache 首次请求 miss 后可直接缓存完整 Worker 输出，后续命中跳过 Worker；只设置 header 是否生效取决于 Workers Cache 已启用。[Workers Cache](https://developers.cloudflare.com/workers/cache/)；[Workers Cache configuration](https://developers.cloudflare.com/workers/cache/configuration/)；[Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/)，访问于 2026-11-13。

### 5. Custom Domain 是否必然 Orange-cloud / proxied？

Custom Domain 需要活跃 Cloudflare zone；Cloudflare 自动创建 DNS record 并让 record 指向 Worker，Worker 即该 hostname 的 origin。官方页没有使用“灰云不可用”这句直接表述；不过由于请求路由与 Worker 托管由 Cloudflare 提供，**[INFERENCE]** Worker Custom Domain 是 Cloudflare 代理入口，而非一个可单独改成 DNS-only 后仍直接抵达 Worker 的 origin 地址。不可把这条推断扩展为所有 Worker Routes 的 DNS 配置规则。[Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/)；[DNS proxy status](https://developers.cloudflare.com/dns/proxy-status/)，访问于 2026-11-13。

### 6. Vary、Authorization 与 cache key

Workers Cache 按 `Vary` 响应头为所列请求头保存变体，`Vary: *` 不缓存。Cache API 的 `put()` 不接受 `Vary: *`，其 request headers 不会像浏览器 Cache API 那样自然形成变体；要按请求头分区，需自行把差异纳入 key，或让响应/Cache API 配置明确支持的 Vary 行为。Workers Cache 默认 key 不包括一般 headers（包括 Cookie、Authorization），但会按文档所列的标准自动 bypass 条件处理 `Authorization` 请求头以及响应 `Set-Cookie`。因此不得以 Authorization 自动 bypass 推导 URL query 上的凭证会 bypass。[Workers Cache keys](https://developers.cloudflare.com/workers/cache/cache-keys/)；[Workers Cache](https://developers.cloudflare.com/workers/cache/)；[Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/)，访问于 2026-11-13。

### 7. 私有/已认证响应与 secret-in-query 风险

Cloudflare Workers Cache 官方将敏感用户数据、每请求变化的响应列为不适合共享 CDN cache 的场景；需要不缓存时显式 `Cache-Control: private` 或 `no-store`。如果缓存私有响应，Cache key 必须区分每个用户；Cloudflare 在 Workers Cache 对 service binding 的建议是用 key 中的 `ctx.props` 表示 caller authorization context。普通 GET 的敏感 query string 本身属于 URL key，但 URL 可进入日志、历史记录、分析等，且没有 Cloudflare 文档承诺把 secret query 当作安全鉴权边界；使用 private/no-store 更安全。[Workers Cache](https://developers.cloudflare.com/workers/cache/)；[Workers Cache keys](https://developers.cloudflare.com/workers/cache/cache-keys/)；[Cache Deception Armor](https://developers.cloudflare.com/cache/cache-security/cache-deception-armor/)，访问于 2026-11-13。

## ② Vercel Functions（Python / FastAPI）

### 8. 函数响应如何缓存，精确 header 与默认值

Vercel 明确支持对 Vercel Functions 响应使用 CDN cache，框架不限；Python/FastAPI 属于 Vercel Function，但文档示例没有针对 FastAPI 的差异规则。要启用 dynamic Function response caching，response 必须包括 `Cache-Control` 的 `s-maxage=N`，可选追加 `stale-while-revalidate=Z` 和/或 `stale-if-error=Z`。默认 `Cache-Control: public, max-age=0, must-revalidate` 意味着 CDN 与浏览器不缓存。`private` 只允许 client cache、不允许 Vercel CDN cache；敏感内容用 `no-store` 禁止缓存。[Vercel CDN Cache](https://vercel.com/docs/caching/cdn-cache)；[Cache-Control headers](https://vercel.com/docs/caching/cache-control-headers)，访问于 2026-11-13。

单独 `Cache-Control` 下，Vercel proxy 消费 `s-maxage`、`stale-while-revalidate` 等用于 CDN 行为，并从客户端返回的 Cache-Control 中剥离这些指令；`CDN-Cache-Control` 覆盖 Cache-Control，`Vercel-CDN-Cache-Control` 优先级最高且仅控制 Vercel cache。Function response header 覆盖 `vercel.json` / `next.config.js` 同一路由头；因此对该 FastAPI endpoint，直接在 FastAPI response 设 header 就足够，不需要 dashboard 设置。[Cache-Control headers](https://vercel.com/docs/caching/cache-control-headers)；[Vercel CDN Cache](https://vercel.com/docs/caching/cdn-cache)，访问于 2026-11-13。

### 9. Vercel cache key、query string 与 method

官方 CDN purge 文档的 key 组成包括 request method、request URL、host、deployment URL、scheme；仅对**静态文件**说明 query strings 被忽略。基于此，Function URL 的 query 部分应是 key 的一部分，故相同路由但机场账户/token query 不同应是不同 cache entry。**[INFERENCE]** 文档没有逐字说明 Function key 对 query string 的规范化/排序规则，也没有指出会为 Function cache 特殊剥除哪些 query 参数；不能宣称一切 query 字节序/参数顺序的精确规范。该 key 不可配置。缓存 GET/HEAD 限制也未在这些 Vercel CDN 文档中明确写成统一规则；本项目路由自身仅注册 GET，答案只覆盖其 GET 请求。[Purging Vercel CDN Cache — Cache keys](https://vercel.com/docs/caching/cdn-cache/purge)；[subunlock server.py](../../src/subunlock/server.py)，访问于 2026-11-13。

### 10. Cache HIT 是否跳过函数调用和 Fluid execution？

是。Vercel `x-vercel-cache: HIT` 定义为直接从缓存提供响应，**无需 Function invocation 或 origin fetch**。对 Function 来说，这意味着 cache hit 不执行 Python/FastAPI/Fluid 请求处理；Function invocation 和 Function 的执行不会发生。缓存 hit 依然是 CDN 请求，不等同于“所有平台费用为零”。[Cache Status and Reasons](https://vercel.com/docs/caching/cache-status)，访问于 2026-11-13。

### 11. SWR、TTL 上限、4xx/5xx

- `s-maxage=N` 指示 CDN fresh TTL；官方给出的允许范围是 1 秒至 31,536,000 秒（一年）。超过 freshness 后默认下一请求同步调用 Function；追加 `stale-while-revalidate=Z` 可先返回旧响应，并在后台 revalidate。SWR 的具体延长窗口按 Z 指定；文档没有声明一个所有 plan 通用的总 TTL ceiling 高于 s-maxage 的上限。[Cache-Control headers](https://vercel.com/docs/caching/cache-control-headers)，访问于 2026-11-13。
- `stale-if-error` 可在上游/Function 遇到 500、网络失败、DNS 失败时继续给客户端上一份 stale 响应；它不是“缓存新错误”。[Cache-Control headers](https://vercel.com/docs/caching/cache-control-headers)，访问于 2026-11-13。
- Vercel 引用的 CDN cache 文档要求响应含缓存指令，却没有在通用 Function 文档中明确承诺哪些非 2xx status 可写入 CDN cache，也没有列出通用“4xx/5xx 永不缓存”规则。故不可推断错误响应一定会被缓存或一定不会被缓存。对 subunlock，应在错误分支明确返回 `Cache-Control: no-store`（或 `private`），并只给预期成功响应设置 `s-maxage`，以防平台具体状态缓存规则差异。[Vercel CDN Cache](https://vercel.com/docs/caching/cdn-cache)；[Cache-Control headers](https://vercel.com/docs/caching/cache-control-headers)，访问于 2026-11-13。

### 12. Dashboard 配置、计划层级

动态 Function response 缓存可以直接由函数返回的 Cache-Control header 配置，无需 dashboard Cache Rule；也可在 route config 定义 headers，但 Function 自己的 response header 优先。Vercel CDN caching 文档说明各计划和域都可用，CDN cache available for all plans；Hobby 不因计划本身失去 function response cache 资格。[Vercel CDN Cache](https://vercel.com/docs/caching/cdn-cache)；[Cache-Control headers](https://vercel.com/docs/caching/cache-control-headers)，访问于 2026-11-13。

### 13. subscription-userinfo 等自定义响应头是否会原样缓存重放？

Vercel 将响应作为完整 HTTP response 缓存，cache HIT 是“response came from cache”；**[INFERENCE]** 因而在未被平台保留头/安全处理改写的情况下，自定义响应头（包括 `subscription-userinfo`）应随缓存响应一起返回。不过 cited 文档没有逐一保证任意自定义 header 的原字节/大小写/重复头原样回放，也说明某些系统头为保留头、不可修改。应以实际响应头测试确认，不把“verbatim”当文档保证。[Vercel CDN Cache](https://vercel.com/docs/caching/cdn-cache)；[Cache Status and Reasons](https://vercel.com/docs/caching/cache-status)；[Response headers](https://vercel.com/docs/headers/response-headers)，访问于 2026-11-13。

### 14. 通过 query key 缓存 authenticated/private content

Vercel 指出 CDN cache 不适用于敏感用户数据；针对 personalized/per-user content 推荐 `private`，可阻止 CDN cache。`Vary: Cookie` 也被禁止缓存。Vercel 对 query-string credentials 并未提供特殊自动私有化或访问控制说明；就 key 组成而言 URL 是 cache key 的一部分（静态资源除外 query 忽略），但“不同 secret query 分 entry”不是安全性承诺，不应把密钥作为安全边界。当前 subunlock 实际**没有 API auth layer**；query 中是机场 panel 用户/token 凭证，endpoint 未鉴权且输出订阅内容。因此共享缓存即使一份 key 对应一份用户，也会延长该凭证可用数据在边缘的存留；应采用短时 TTL 并评估是否根本应缓存。[Vercel CDN Cache](https://vercel.com/docs/caching/cdn-cache)；[Cache-Control headers](https://vercel.com/docs/caching/cache-control-headers)；[Purging Vercel CDN Cache — Cache keys](https://vercel.com/docs/caching/cdn-cache/purge)，访问于 2026-11-13。

## ③ Cloudflare Python Workers（Pyodide）可行性

### P1. Python Worker 是否能访问 Cache API？Response 互转及示例

**可以访问。** Cloudflare Python FFI 文档说 Python Workers 可用 JavaScript globals；官方 first-party Python Cache API example 明确从 `js` 导入 `Response, Request, URL, caches, fetch`，调用 `caches.default.match()`、用 `js.Response.new(...)` 构造 Response、`response.clone()` 后 `cache.put()`，使用 `pyodide.ffi.create_proxy()` 传 Promise 给 `waitUntil()`。这足以证明 Python Worker 能调用 Cache API，且至少示例中的 JS Response 适配方式是官方支持路径。[Python FFI](https://developers.cloudflare.com/workers/languages/python/ffi/)；[Python Cache API example](https://developers.cloudflare.com/workers/examples/cache-api/)，访问于 2026-11-13。

但官方示例的 response 是 JS Fetch 返回的 JS Response，再由 `js.Response.new` clone；它没有演示把 FastAPI/Starlette 的 `fastapi.responses.Response`（Python object）直接交给 JS `cache.put()`。所以 “Python-side ASGI body+headers 可直接隐式转为 JS Response”没有文档依据。若要缓存本项目完整生成响应，需要显式把 Python response body/status/headers 映射为 `js.Response.new(...)`，或在 ASGI 边界外构造 JS Response；**[INFERENCE]** 此集成还需检查 ASGI middleware/Workers SDK wrapper 的响应适配与流式 body，不宜假设 Python Response 对象可直接传入 Cache API。[Python Cache API example](https://developers.cloudflare.com/workers/examples/cache-api/)；[Python FFI](https://developers.cloudflare.com/workers/languages/python/ffi/)；[FastAPI on Workers](https://developers.cloudflare.com/workers/languages/python/packages/fastapi/)，访问于 2026-11-13。

### P2. Python SDK `fetch()` 与 Pyodide `pyfetch` 是否可传 cf options？

是。Cloudflare 官方 Python `Cache using fetch` 示例使用 `from workers import ... fetch` 并传入 `cf={"cacheTtl": 5, "cacheEverything": True, ...}`；这证明 Python Workers SDK fetch 能将 cf cache options 用于子请求。[Python Cache using fetch example](https://developers.cloudflare.com/workers/examples/cache-using-fetch/)；[Request `cf` options](https://developers.cloudflare.com/workers/runtime-apis/request/)，访问于 2026-11-13。

但 Pyodide 314.0.7 的 `pyodide.http.pyfetch` 公共签名是 `pyfetch(request, *, signal, fetcher)`，没有任意 `cf` kwargs 参数；Pyodide API 并未称可传 Cloudflare `cf` property。不要把 workers-py 的 `fetch(..., cf=...)` 能力泛化到 `pyfetch(..., cf=...)`。[Pyodide HTTP API](https://pyodide.org/en/stable/usage/api/python-api/http.html)；[Python Cache using fetch example](https://developers.cloudflare.com/workers/examples/cache-using-fetch/)，访问于 2026-11-13。

### P3. httpx → Cloudflare 适配 Fetch 能否传递 cf options？

Cloudflare 的官方 Python Workers GA/HTTP client 材料说明 `requests`/`httpx` 这类客户端可由 Workers runtime 适配到 JS Fetch；但本轮未找到官方文档说明 httpx request extensions、transport config 或 client option 可以映射到 Fetch `cf.cacheTtl` / `cf.cacheEverything`。因此，针对本项目现有 **httpx → adapted transport** 路径，不能声称可以从 httpx 传入 Cloudflare `cf` options；以当前文档证据看该通路**没有已记录的 cf-option API**。若需要这些选项，改用 Python `workers.fetch(..., cf=...)` 显式执行上游请求；若坚持 httpx，需单独验证是否存在 SDK/transport 扩展，不应假定可用。[Python Workers GA](https://blog.cloudflare.com/python-workers-ga/)；[Python Cache using fetch example](https://developers.cloudflare.com/workers/examples/cache-using-fetch/)；[Request `cf` options](https://developers.cloudflare.com/workers/runtime-apis/request/)，访问于 2026-11-13。

### P4. 本项目的 Python Worker CF 缓存路径结论

Python 运行时可触达 Cache API，也有官方 Python Cache API 用法；所以无需把“内存 cache 或 Vercel-only”当作唯一退路。不过我们的 worker.py 目前只把 FastAPI `app` 交给 `asgi.entrypoint(app)`，未见缓存 middleware/显式 Cache API calls（见 [worker.py](../../src/worker.py)）；**[INFERENCE]** 在当前代码状态下，不能期待 Cache API 自动缓存完整 ASGI response。可选路径： (a) 在 Python Workers 入口/ASGI middleware 显式读写 Cache API并正确转换 status/body/headers（Cache API hit仍调用 Worker，local colo；workers.dev 可用性仍待平台文档/实际验证）；(b) 上游请求改用 `workers.fetch(..., cf=...)`，只缓存外部 fetch、Worker 每次仍执行；(c) 若目标是跳过 Python/ASGI 执行，评估新 Workers Cache 的 `cache.enabled` + `Cache-Control`，但新配置能力是否与当前 Python Workers/pywrangler 版本兼容需要实测；(d) 无法安全做边缘缓存时只在同 isolate 内存缓存 TTL（会因 isolate 重启/扩缩容/colocation 导致非持久、非共享），或仅启用 Vercel CDN Cache。[Python Cache API example](https://developers.cloudflare.com/workers/examples/cache-api/)；[Workers Cache](https://developers.cloudflare.com/workers/cache/)；[Workers Cache configuration](https://developers.cloudflare.com/workers/cache/configuration/)，访问于 2026-11-13。

## ④ 对 subunlock 的影响

- **当前代码事实：** API 没有 API auth layer；机场账户标识、token 等作为 GET query 参数传递，FastAPI 生成每用户 Clash YAML，成功响应还含 `subscription-userinfo`。所以缓存凭证对应的完整响应属于敏感用户数据，不能忽略 cache key 隔离与 TTL。[server.py](../../src/subunlock/server.py)；[worker.py](../../src/worker.py)，访问于 2026-11-13。
- **Vercel：** 对功能完整的 Function response cache，响应 header 是最直接方案：只对成功结果短 TTL 设置 `Cache-Control: public, s-maxage=N`（或 `Vercel-CDN-Cache-Control` 专用于 Vercel），错误一律 `no-store`；认证/敏感数据官方建议用 `private` / `no-store`，因此是否共享缓存应先经产品/安全决策。query 参数按 URL key 的推断隔离缓存项，但不形成 authorization。HIT 可跳过函数执行；应检查 `x-vercel-cache` 与缓存后的 `subscription-userinfo` 实际回放行为。
- **Cloudflare：** 不要把 Worker 自身 response 的 `Cache-Control` 等同于传统 zone Cache Rules。Python 有 Cache API 调用能力，但目前 ASGI wrapper 没接线；显式 Cache API 会在每个 colo 本地读写，且 Python object → JS Response 转换需实现。若改成 Cloudflare Python `workers.fetch` 调 airport panel，可使用 `cf.cacheEverything/cacheTtl/cacheTtlByStatus` 缓存 subrequest，但这**不会**跳过 Worker 执行；当前 httpx transport 没有已文档化的 cf-options 转发。Workers Cache 是另一个可在 Worker 运行前命中、可跳过 Worker 的机制，使用 Worker Cache-Control，但 pywrangler/Python 版本兼容性和 Workers.dev 上的 Cache API 行为需要在对应部署实测。Cache API 对 custom domain 文档明确支持；Workers Cache 自身明确支持 workers.dev。[Workers Cache](https://developers.cloudflare.com/workers/cache/)；[Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/)；[Python Cache API example](https://developers.cloudflare.com/workers/examples/cache-api/)；[Python Cache using fetch example](https://developers.cloudflare.com/workers/examples/cache-using-fetch/)，访问于 2026-11-13。

## ⑤ 一手来源索引

下列均为 Cloudflare、Vercel 或 Pyodide 官方文档/官方博客；访问日期统一为 2026-11-13。

- Cloudflare：[Workers Cache](https://developers.cloudflare.com/workers/cache/)；[Configuration](https://developers.cloudflare.com/workers/cache/configuration/)；[Cache keys](https://developers.cloudflare.com/workers/cache/cache-keys/)；[Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/)；[How the Cache works](https://developers.cloudflare.com/workers/reference/how-the-cache-works/)；[Workers and zone cache](https://developers.cloudflare.com/cache/interaction-cloudflare-products/workers/)；[Request cf options](https://developers.cloudflare.com/workers/runtime-apis/request/)；[Fetch](https://developers.cloudflare.com/workers/runtime-apis/fetch/)；[Cache API limits](https://developers.cloudflare.com/workers/platform/limits/)；[Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/)；[Cache Deception Armor](https://developers.cloudflare.com/cache/cache-security/cache-deception-armor/)；[Python FFI](https://developers.cloudflare.com/workers/languages/python/ffi/)；[Python Cache API example](https://developers.cloudflare.com/workers/examples/cache-api/)；[Python Cache using fetch](https://developers.cloudflare.com/workers/examples/cache-using-fetch/)；[Python Workers GA](https://blog.cloudflare.com/python-workers-ga/).
- Vercel：[CDN Cache](https://vercel.com/docs/caching/cdn-cache)；[Cache-Control headers](https://vercel.com/docs/caching/cache-control-headers)；[Purging CDN Cache/cache keys](https://vercel.com/docs/caching/cdn-cache/purge)；[Cache Status](https://vercel.com/docs/caching/cache-status)；[Response headers](https://vercel.com/docs/headers/response-headers)；[Functions](https://vercel.com/docs/functions).
- Pyodide：[HTTP API / pyfetch signature](https://pyodide.org/en/stable/usage/api/python-api/http.html).
