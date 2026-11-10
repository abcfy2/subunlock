"""猫猫云订阅解密: payload = base64( IV ‖ AES-128-CBC( base64(yaml) ) ).

key: ASCII b'4422a60e08c97f30' (AES-128), 加密逻辑位于客户端 Dart 层
(libapp.so), 通过对运行中客户端进程内存的全偏移已知明文暴力搜索还原;
IV 为密文前 16 字节(每份 payload 独立).
商家生成的 yaml 首行缺失 "port:" 键名(裸 7890), 解密后自动补齐.
"""

import base64
from typing import Any

import yaml
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DEFAULT_AES_KEY = b'4422a60e08c97f30'


def decrypt_payload(raw: bytes, key: bytes = DEFAULT_AES_KEY) -> str:
    """解密订阅 payload, 返回标准 YAML 文本.

    raw: base64 解码后的 IV ‖ AES-CBC 密文.
    """
    if len(key) not in (16, 24, 32):
        msg = f'AES key 长度必须为 16/24/32 字节, 当前 {len(key)}'
        raise ValueError(msg)
    if len(raw) < 48:
        raise ValueError('payload 过短')
    iv, body = raw[:16], raw[16:]
    d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = d.update(body) + d.finalize()
    pad = plain[-1]
    if 1 <= pad <= 16 and plain[-pad:] == bytes([pad]) * pad:
        plain = plain[:-pad]
    yaml_text = base64.b64decode(plain).decode('utf-8')
    lines = yaml_text.split('\n')
    if lines and lines[0] == '7890':
        lines[0] = 'port: 7890'
    return '\n'.join(lines)


def parse_config(text: str) -> dict[str, Any]:
    cfg = yaml.safe_load(text)
    if not isinstance(cfg, dict):
        raise ValueError('解密结果不是 YAML dict')
    return cfg
