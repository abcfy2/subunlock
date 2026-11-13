# ADR-0001: Provider 每层样板待第二个 provider 落地再抽象

日期: 2026-11-14 · 状态: Accepted

## 背景

Architecture review 提出两个可下沉的 per-provider 样板: `create()` 的
isinstance 守卫 + 构造调用(可改为 base 提供默认实现), 以及 `fetch()` 里
PanelError→ProviderError 的翻译 try/except(可改为公共装饰器或 api 层直抛).

## 决策

暂不下沉. 当前 `PROVIDERS` 只有 maomaoyun 一个 provider; 一个 adapter =
假想 seam, 抽象程度无从校准. 等第二个 provider 落地、重复出现第二次时再收.

## 影响

- 新 provider 暂时复制约 10 行样板, 可接受.
- 未来 architecture review 不应再把本项列为候选, 除非 PROVIDERS 注册表
  出现第二个条目.
