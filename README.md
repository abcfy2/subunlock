# subunlock

从"不提供标准 Clash 订阅"的机场魔改客户端中提取标准 Clash 订阅配置。

## 安装

```bash
uv sync
```

## 使用

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
```

输出为标准 mihomo YAML, 可直接导入 Clash Verge / FlClash / ShellCrash。

仅供个人已购服务使用, 请勿分发提取结果。


## 扩展新 provider

各机场的加密方案与认证方式不同, 需要先逆向对应的客户端(还原端点、
加密算法与 key), 然后继承 `base.Provider`, 实现 `configure_args` /
`create` / `fetch`, 在 `src/subunlock/__init__.py` 的 `PROVIDERS` 注册即可,
CLI 自动生效。具体加解密细节写在对应 provider 的模块文档里。

## 开发

```bash
uv run ruff check --fix src/     # lint + 自动修复
uv run basedpyright src/         # 类型检查, 0 error/warning 为通过
uv run subunlock maomaoyun --subscribe-token <token> --output /tmp/smoke.yaml   # 冒烟
```
