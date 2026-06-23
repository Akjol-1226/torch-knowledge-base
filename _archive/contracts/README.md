# Contracts — 跨角色契约单一真相源

> 所有跨服务、跨端、跨角色的接口/事件/数据契约都在这里。
> 修改实现代码前必须先改这里（铁律 5）。

## 目录

| 子目录 | 内容 | 推荐格式 |
|---|---|---|
| `api/` | REST / GraphQL / RPC 接口 | OpenAPI yaml 或 Markdown 表格 |
| `events/` | 消息队列 / 事件 schema | Markdown + JSON Schema |
| `data-models/` | 跨服务/跨端共享数据结构 | Markdown 表格 + 类型定义 |

详见 `decisions/ADR-011-contracts-layer.md`。
