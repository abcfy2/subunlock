import base64
import json
import subprocess
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

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
    req = Request(url, method=method, headers=headers, data=body)
    with urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read())
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


def fetch_payload(subscribe_token: str, retries: int = 7) -> bytes:
    """拉取 v100 加密订阅并 base64 解码, 返回 AES 密文字节.

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
    body: bytes | None = None
    last_err: Exception | None = None
    for attempt in range(retries):
        req = Request(url, headers={"User-Agent": uas[attempt % len(uas)]})
        try:
            with urlopen(req, timeout=20) as resp:
                body = resp.read()
            break
        except HTTPError as e:
            last_err = e
            if e.code not in (403, 404, 500, 502, 503) or attempt == retries - 1:
                raise
        except (TimeoutError, OSError) as e:
            last_err = e
            if attempt == retries - 1:
                raise
        time.sleep(2 ** attempt)
    if not body:
        try:
            res = subprocess.run(
                ["curl", "-sSL", "-m", "40", "-A", "clash-verge", url],
                capture_output=True,
                timeout=50,
                check=True,
            )
            body = res.stdout
        except (OSError, subprocess.SubprocessError) as e:
            raise PanelError(f"订阅端点持续不可用: {last_err}") from e
    return base64.b64decode(body)
