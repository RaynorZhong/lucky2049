# 数据格式与消费指南 / Data Schema

> 🌏 [English](../SCHEMA.md) · **中文**

lucky2049 是一个**可验证的公共随机信标**:它只发布**开奖**(号码 + 防篡改链),由 144 个比特币
区块哈希确定性推导而来(见 [`SPEC.md`](../../SPEC.md))。奖池、售票、兑奖**有意不在范围内**——
那些属于独立的下游项目。本页就是这些下游项目可以依赖的**稳定契约**。

所有产物都是站点根目录下的纯静态 JSON(`https://lucky2049.com/…`),由 GitHub Pages 提供——
无 API、无鉴权,除 CDN 外无速率限制。

## 发布的文件

| 文件 | 用途 |
|------|------|
| [`latest.json`](https://lucky2049.com/latest.json) | 最新一期 + 历史链头。**轮询它**看「有没有新开奖」。 |
| [`feed.json`](https://lucky2049.com/feed.json) | 最近约 30 期的 [JSON Feed 1.1](https://jsonfeed.org)。**订阅它。** |
| [`status.json`](https://lucky2049.com/status.json) | 最近一次刷新的健康度:逐源探测结果(Core 节点 / 浏览器)。 |
| [`index.json`](https://lucky2049.com/index.json) | 全量历史:每一期 + 链头(约 2 MB,gzip 后约 350 KB)。 |
| [`head.json`](https://lucky2049.com/head.json) | 单独的承诺链头(承诺整段历史的 32 字节哈希)。 |
| `anchors/<id>.head.json.ots` | 把链头外锚到比特币链的 OpenTimestamps 证明。 |

## 格式 / Schemas

### 单期记录 / Draw record
`index.json` 的 `draws[]` 和 `latest.json` 的 `latest` 里的对象:

```jsonc
{
  "id": 6611,                              // 期号;高度区间 = [id*144, id*144+143]
  "algo_version": "v1",                    // 该期使用的算法版本(见 SPEC.md)
  "front": [4, 6, 14, 19, 27],             // 前区 5 个,互异、升序、1–35
  "back": [3, 11],                         // 后区 2 个,互异、升序、1–12
  "start_height": 951984,                  // 起始比特币区块高度
  "end_height": 952127,                    // 结束高度(= start_height + 143)
  "commitment": "<64-hex>",                // 该期的 SHA-256 哈希链承诺
  "prev_commitment": "<64-hex>",           // 上一期承诺(第 0 期为创世哨兵值)
  "timestamp": "2026-06-02 16:05:05 UTC"   // 末块时间(仅展示,**不进承诺**)
}
```
在 `latest.json`(及其他精选视图)里,记录还会带一个 `verify_url`,方便直达浏览器验证页。

### `index.json`
```jsonc
{ "count": 6612, "algo_version": "v1", "head": <head>, "draws": [ <单期记录>, … ] }  // 最旧在前
```

### `head` 对象(见于 `head.json`、`index.json.head`、`latest.json.head`)
```jsonc
{ "head": "<64-hex>", "draw_id": 6611, "count": 6612, "algo_version": "v1" }
```

### `latest.json`
```jsonc
{ "schema": "lucky2049/latest/v1", "head": <head>, "latest": <单期记录 + verify_url> }
```

### `feed.json`
标准 [JSON Feed 1.1](https://jsonfeed.org/version/1.1):`version`、`title`、`home_page_url`、
`feed_url`、`items[]`。每个 item:`id`(期号字符串)、`url`(验证页链接)、`title`、
`content_text`、`date_published`(RFC 3339)。最近约 30 期,最新在前。

每个 item 还带一个 `_lucky2049` 扩展对象(JSON Feed 约定 `_` 前缀成员留给发布方扩展,普通阅读器
会忽略),携带结构化号码,消费方无需再解析展示字符串:`{ "front": [5个int], "back": [2个int],
"start_height", "end_height" }`。

### `status.json`
每次发布器运行(每小时)写入:每个哈希源有没有应答、答了什么?

```jsonc
{
  "schema": "lucky2049/status/v1",
  "checked_at": "2026-06-11 07:20:12 UTC",
  "checked_at_unix": 1781075212,
  "tip_source": "core",                    // 本次链尖由谁提供
  "sources": [                             // 按优先序;"core" 仅在配置后出现
    { "name": "core", "ok": true, "tip": 953202, "ms": 312 },
    { "name": "mempool", "ok": true, "tip": 953202, "ms": 145 },
    { "name": "blockstream", "ok": false, "error": "HTTP Error 503: ...", "ms": 1042 }
  ],
  "head": { "head": "<64-hex>", "draw_id": 6618, "count": 6619, "algo_version": "v1" },
  "added": 0,                              // 本次发布的期数
  "held": 6619                             // 仅当有期被暂缓(源不一致)时出现
}
```
首页把它渲染成「Sources (last refresh)」状态条。cron 为每小时,但 GitHub Actions 定时会有抖动,
所以 `checked_at` 超过约 4 小时(而非约 1 小时)才说明 cron 本身没在跑。

两个特殊形态:**离线重建**(`export_static.py`,灾备)写入的是占位——`sources: []`、
`tip_source: null` 外加一个 `note` 字段说明本次未探测,下一次小时级运行会用真实探测覆盖;
而当探测了但**全部源都不正常**时,发布器仍会把这份(全红的)文件发上线,随后 workflow
以失败告警。

## 怎么消费

- **「最新一期是哪期?」** → 按你想要的频率 GET `latest.json`(很小);新一期约每天产生一期。
- **「订阅。」** → 用 JSON Feed 阅读器指向 `feed.json`(首页 `<link rel="alternate">` 可自动发现)。
- **「全都要。」** → GET 一次 `index.json` 并缓存,它包含每一期。

## 验证——别信任,重算

每一期都能从链上独立复现;别盲信发布的号码:

- 命令行:`python verify.py <id> --site https://lucky2049.com`
- 浏览器:打开 `verify.html?draw=<id>`
- 防篡改:每期的 `commitment` 串入 `head.json` 的链头;`anchors/` 下的证明把该链头盖时间戳到
  比特币链。见 [`SPEC.md`](../../SPEC.md) §5 与 README。

## 稳定性承诺

- **只增不改。** 已有字段名和含义不变,只新增字段。顶层 `schema` 标签(如 `lucky2049/latest/v1`)
  只在破坏性变更时才升。
- **逐期版本化。** 每期声明 `algo_version`;`v1` 已冻结,未来任何算法改动只对**新期**生效
  (历史期次永远按其声明的版本可验证)。
- **高度可推导**、绝非运营方挑选:第 `N` 期固定用 `[N*144, N*144+143]`。

## 范围——红线

这是一个**信标**,不是彩票运营方。它**不设奖池、不售票、不兑奖**——有意如此,以远离博彩监管。
若你在其上构建奖池,风险自负;并请把任何单注奖金控制在 [`SPEC.md`](../../SPEC.md) §8 的经济安全
上限(`W < B/p`)以下。
