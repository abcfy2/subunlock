"""Cloudflare Python Workers 入口.

铁律: `workers` 必须最先 import——runtime-sdk 的 entropy 补丁
(_cloudflare.entropy_patches) 随 workers 包激活, cryptography._rust 等
Rust/wasm 扩展的 import 依赖其 allow_entropy 白名单; 顺序颠倒会在
worker 启动期挂死 ("Top-level await unsettled")。

tradeoff: CF 上无线程, sync 端点在事件循环上内联执行, fetch_payload
的指数退避(最长 ~126s)会冻住并发请求; 单租户订阅工具可接受,
Vercel 端 sync def 走 anyio 线程池无此问题。
"""

from workers import asgi  # noqa: I001 - workers 必须最先 import(entropy 补丁铁律); pyright: ignore[reportMissingImports] - 仅存在于 CF runtime-sdk 环境

# 先执行被 entropy 补丁注册的模块(rust_package_context), 打开 Rust 包首次
# import 的 getentropy 白名单; 否则 cryptography._rust 在 ciphers 导入期
# 触发 entropy 调用会被 workerd 拒绝(worker 起动期 wasm panic)。本地
# CPython 上此 import 为普通操作, 无副作用。
import cryptography.exceptions  # noqa: F401

from subunlock.server import app

Default = asgi.entrypoint(app)
