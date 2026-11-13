# GLOSSARY

subunlock 领域词汇. 一次一义; 中文术语与代码标识符对齐, 改名需同步本档.

## 领域概念

- **Provider(提取器)**: 从一个机场魔改客户端还原标准 Clash 订阅的实现. 注册于
  `subunlock.PROVIDERS`; 契约见 `base.Provider`(params_model / create / fetch).
- **Subscription(订阅)**: 一次成功提取的产出(`base.Subscription`): YAML 文本、
  上游配额头(subscription-userinfo)、节点/策略组/规则计数.
- **Panel(面板)**: 机场后端 API. `providers/maomaoyun_api.py` 是猫猫云面板的
  传输模块; 上游失败 vs 凭证被拒是两种不同错误(见下).
- **魔改客户端**: 不直接提供标准订阅的机场客户端; 其订阅 payload 格式为
  `base64(IV ‖ AES-CBC(base64(yaml)))`, 细节见 `maomaoyun_crypto` 模块文档.

## 错误模式(Provider interface 的一部分)

- **ProviderArgumentError**: 调用参数在运行期不成立(账密被拒/token 缺失).
  REST→400, CLI→exit 2. 有意不继承 ProviderError, 捕获顺序无关.
- **ProviderError**: 上游网络/面板/解密失败. REST→502, CLI→exit 1.
- **PanelRejected**(⊂ PanelError): 面板明确拒绝本次凭证; provider 层唯一翻译点
  在 `maomaoyun.fetch()`, 转为 ProviderArgumentError.

## 架构分层

- **共享管线**: `base.extract_subscription()` — create→fetch→裁剪→YAML 序列化
  的唯一实现; 两个传输 adapter 都走它.
- **传输层 adapter**: `cli.py`(CLI)、`server.py`(REST/ASGI)、`worker.py`
  (Cloudflare 入口). 职责仅: 参数收集、错误翻译、平台特有头部/展示.
