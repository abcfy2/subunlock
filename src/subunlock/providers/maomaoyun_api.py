"""猫猫云面板 API 传输层: 登录与加密订阅拉取.

本地/Vercel/Cloudflare Workers 三端统一走 httpx(Cloudflare 官方适配过
JS Fetch 通道; stdlib socket 的 TLS 在 workerd 不可用). JSON 响应小,
不做连接复用.

错误模式: PanelError = 上游失败(网络/HTTP 5xx/异常响应体);
PanelRejected = 面板明确拒绝本次凭证(HTTP 4xx/语义失败), 语义上是调用
参数问题, provider 层翻译为 ProviderArgumentError.
"""

import base64
import json
import time

import httpx

_API_HOST = "https://api.brfcdu.cn"
_LOGIN_PATH = "/api/v1/passport/auth/login"
_SUBSCRIBE_PATH = "/api/v100/client/subscribe"
_API_TIMEOUT = 30  # JSON API 单次请求
_FETCH_TIMEOUT = 20  # 订阅端点单次尝试; 重试整体耗时另计
_RETRIES = 7
_RETRYABLE_STATUS = frozenset((403, 404, 500, 502, 503))
_UA_ROTATION = (
    "clash-verge",
    "curl/8.14.1",
    "clash.meta",
    "ClashforWindows/0.20.39",
    "clash-verge/v2.4.3",
)


class PanelError(RuntimeError):
    """面板侧失败: 网络、HTTP 5xx、异常响应体."""


class PanelRejected(PanelError):
    """面板明确拒绝本次请求凭证(HTTP 4xx / 语义失败)."""


def _send(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
) -> httpx.Response:
    """执行请求, 网络层失败统一翻译为 PanelError; HTTP 状态码由调用方判定."""
    try:
        return client.request(method, url, headers=headers, content=content)
    except httpx.HTTPError as e:
        raise PanelError(f"{url} 请求失败: {e}") from e


def login(email: str, password: str) -> str:
    """账密换取 subscribe_token; 凭证被面板拒绝时抛 PanelRejected."""
    body = json.dumps({"email": email, "password": password}).encode()
    url = f"{_API_HOST}{_LOGIN_PATH}"
    with httpx.Client(timeout=_API_TIMEOUT, follow_redirects=True) as client:
        resp = _send(client, "POST", url,
                     headers={"Content-Type": "application/json"}, content=body)
    if resp.status_code >= 400:
        detail = f"HTTP {resp.status_code}"
        if resp.status_code < 500:
            raise PanelRejected(f"登录被拒绝({detail}), 检查账号密码")
        raise PanelError(f"登录失败: {detail}")
    payload = resp.json()
    if payload.get("status") != "success":
        raise PanelRejected(f"登录被拒绝: {payload}")
    return str(payload["data"]["token"])


def fetch_payload(subscribe_token: str) -> tuple[bytes, str | None]:
    """拉取 v100 加密订阅, 返回 (base64 解码后的 AES 密文, subscription-userinfo 头).

    面板 CDN 偶发瞬时 403/404/5xx, 多 UA 轮换 + 指数退避重试; 重试策略是
    implementation 细节, 不上 interface.
    """
    url = f"{_API_HOST}{_SUBSCRIBE_PATH}?token={subscribe_token}"
    body = b""
    last_err: Exception | None = None
    userinfo: str | None = None
    with httpx.Client(timeout=_FETCH_TIMEOUT, follow_redirects=True) as client:
        for attempt in range(_RETRIES):
            ua = _UA_ROTATION[attempt % len(_UA_ROTATION)]
            try:
                resp = _send(client, "GET", url, headers={"User-Agent": ua})
            except PanelError as e:
                # 网络层失败(连接/读超时/DNS)一律可重试.
                last_err = e
                if attempt == _RETRIES - 1:
                    raise
                time.sleep(2 ** attempt)
                continue
            if resp.is_success:
                body = resp.content
                userinfo = resp.headers.get("subscription-userinfo")
                break
            last_err = PanelError(f"{url} HTTP {resp.status_code}")
            if resp.status_code not in _RETRYABLE_STATUS or attempt == _RETRIES - 1:
                raise last_err
            time.sleep(2 ** attempt)
    if not body:
        # 200 但空 body 视为失败.
        raise PanelError(f"订阅端点持续不可用: {last_err}")
    return base64.b64decode(body), userinfo
