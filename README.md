# subunlock

从"不提供标准 Clash 订阅"的机场魔改客户端中提取标准 Clash 订阅配置。

## 安装

```bash
uv sync    # 开发环境一把同步(dev 组已含 fastapi/uvicorn/fastapi-cli)
```

发布两档: 仅 CLI `pip install subunlock`; 需要 REST 服务 `pip install "subunlock[server]"`。

## CLI 使用

```bash
# 查看 provider 列表与各自参数
uv run subunlock --help
uv run subunlock maomaoyun --help

# 订阅 token
uv run subunlock maomaoyun --subscribe-token <32hex> --output maomao.yaml

# 账号密码
uv run subunlock maomaoyun --user <email> --password <pw> --output maomao.yaml

# 保留完整配置(默认剥离 port/external-controller/allow-lan 等运行时键)
uv run subunlock maomaoyun --subscribe-token <32hex> --full --output maomao.yaml

# 打印订阅摘要(流量/到期/节点数), 不输出配置; 与 --output 叠加时附加打印
uv run subunlock maomaoyun --subscribe-token <32hex> --info
```

输出为标准 mihomo YAML, 可直接导入 Clash Verge / FlClash / ShellCrash。

## REST 服务

```bash
uv run subunlock serve                  # 默认 127.0.0.1:8000
uv run subunlock serve --port 8123      # 自定义端口
```

端点按 provider 从注册表枚举, 返回的地址直接填进 Clash 订阅:

```
http://127.0.0.1:8000/maomaoyun?subscribe_token=<32hex>
http://127.0.0.1:8000/maomaoyun?user=<email>&password=<pw>
```

`&full=true` 等价 CLI 的 `--full`; 成功返回 `text/yaml`。参数缺失返回 422,
参数/凭证问题(token 缺失、账密被拒) 400, 上游网络/面板/解密失败 502。
参数即各 provider 的 `--help` 同款, 交互文档见 `http://127.0.0.1:8000/docs`。

成功响应带 `Cache-Control: public, s-maxage=300`, 供部署平台 CDN 或前置
反代缓存 5 分钟: Vercel Edge Network 直接生效; Cloudflare 需在 wrangler
配置开启 Workers Cache(`cache.enabled`)后同样生效。错误响应(422/400/404/
502)统一 `no-store`, 不会被缓存。

> 安全提示: token/密码随 URL 明文出现(uvicorn 日志、Clash 配置、浏览器历史),
> 端点无鉴权, 仅限个人本机/LAN 使用, 请勿暴露公网。

仅供个人已购服务使用, 请勿分发提取结果。

## 部署 Vercel

执行 `npx -y vercel --prod` 即可, 首次运行会引导登录并创建项目链接。
注意端点无鉴权, 部署后即公网可达(见上方安全提示)。

## 部署 Cloudflare Workers

```bash
uv run pywrangler deploy      # 首次运行会引导登录
```

配置与依赖见 `wrangler.jsonc` 与 `pyproject.toml`, 打包由 pywrangler 自动完成。
注意端点无鉴权, 部署后即公网可达(见上方安全提示)。

## 本地开发

```bash
uv run fastapi dev                             # 热重载([tool.fastapi] entrypoint 已配置)
uv run uvicorn subunlock.server:app --reload   # 等价, 显式 import string
uv run pywrangler dev                          # Workers 同运行时本地验证(workerd, :8787)
uv run main.py --help                          # 等价于 uv run subunlock --help
```

## 扩展新 provider

各机场的加密方案与认证方式不同, 需要先逆向对应的客户端(还原端点、
加密算法与 key), 然后:

1. 声明 pydantic 参数模型(`params_model`): 字段、必选性、校验器与
   `description` 即参数契约的唯一声明处
2. 继承 `base.Provider`, 实现 `create()` / `fetch()`
3. 在 `src/subunlock/__init__.py` 的 `PROVIDERS` 注册

CLI 选项与帮助、REST 路由与 OpenAPI 文档全部自动生成。
具体加解密细节写在对应 provider 的模块文档里。

## 开发

```bash
uv run ruff check --fix src/     # lint + 自动修复
uv run basedpyright src/         # 类型检查, 0 error/warning 为通过
uv run subunlock maomaoyun --subscribe-token <token> --output /tmp/smoke.yaml   # 冒烟
```
