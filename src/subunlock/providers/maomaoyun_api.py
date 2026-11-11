import base64
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

API_BASE = "https://api.brfcdu.cn/api/v1"
PANEL_HOST = "https://api.brfcdu.cn"
SUBSCRIBE_PATH = "/api/v100/client/subscribe"


@dataclass
class AuthInfo:
    auth_data: str
    subscribe_token: str
    email: str


class PanelError(RuntimeError):
    pass


def _request_json(
    method: str, url: str, *, headers: dict[str, str], body: bytes | None = None
) -> dict[str, Any]:
    # Cloudflare Python Workers(workerd/Pyodide) 上 stdlib socket 的 TLS 握手
    # 不可用; httpx 是 Cloudflare 官方上游适配过 JS Fetch 通道的客户端,
    # 本地/Vercel/Workers 三端行为一致。JSON API 响应小, 不做连接复用。
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.request(method, url, headers=headers, content=body)
    except httpx.HTTPError as e:
        raise PanelError(f"{url} 请求失败: {e}") from e
    if resp.status_code >= 400:
        raise PanelError(f"{url} HTTP {resp.status_code}")
    payload = resp.json()
    if payload.get("status") != "success":
        raise PanelError(f"{url} 返回异常: {payload}")
    return payload


def login(email: str, password: str) -> AuthInfo:
    body = json.dumps({"email": email, "password": password}).encode()
    payload = _request_json(
        "POST",
        f"{API_BASE}/passport/auth/login",
        headers={"Content-Type": "application/json"},
        body=body,
    )
    d = payload["data"]
    return AuthInfo(
        auth_data=str(d["auth_data"]),
        subscribe_token=str(d["token"]),
        email=email,
    )


def get_subscribe(auth_data: str) -> dict[str, Any]:
    payload = _request_json(
        "GET",
        f"{API_BASE}/user/getSubscribe",
        headers={"Authorization": auth_data},
    )
    return payload["data"]


def fetch_payload(subscribe_token: str, retries: int = 7) -> tuple[bytes, str | None]:
    """拉取 v100 加密订阅, 返回 (base64 解码后的 AES 密文, subscription-userinfo 头).

    面板 CDN 偶发瞬时 404/5xx, 采用多 UA 轮换 + 指数退避重试.
    """
    url = f"{PANEL_HOST}{SUBSCRIBE_PATH}?token={subscribe_token}"
    uas = (
        "clash-verge",
        "curl/8.14.1",
        "clash.meta",
        "ClashforWindows/0.20.39",
        "clash-verge/v2.4.3",
    )
    body = b""
    last_err: Exception | None = None
    userinfo: str | None = None
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        for attempt in range(retries):
            try:
                resp = client.get(url, headers={"User-Agent": uas[attempt % len(uas)]})
            except httpx.HTTPError as e:
                # 网络层失败(连接/读超时/DNS)一律可重试。
                last_err = e
                if attempt == retries - 1:
                    raise PanelError(f"订阅端点持续不可用: {e}") from e
                time.sleep(2**attempt)
                continue
            if resp.is_success:
                body = resp.content
                userinfo = resp.headers.get("subscription-userinfo")
                break
            # 面板 CDN 偶发瞬时 403/404/5xx; 其余状态码直接失败。
            last_err = PanelError(f"{url} HTTP {resp.status_code}")
            if resp.status_code not in (403, 404, 500, 502, 503) or attempt == retries - 1:
                raise last_err
            time.sleep(2**attempt)
    if not body:
        # 200 但空 body 视为失败(原 curl 子进程兜底是准死代码, 已删)。
        raise PanelError(f"订阅端点持续不可用: {last_err}")
    return base64.b64decode(body), userinfo
