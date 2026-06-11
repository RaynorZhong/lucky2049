# lucky2049 — 可验证的比特币哈希开奖系统

> 🌏 [English](../../README.md) · **中文**

## 概述

**lucky2049** 是一个**只做开奖**的、公开透明、可独立复现的开号系统。它用 **144 个连续的比特币
主网区块哈希**作为公开且不可预测的熵源，经确定性算法生成一期 **超级大乐透 (Super Lotto)** 号码：

- 前区 (front)：5 个互不相同的号码，范围 1–35
- 后区 (back)：2 个互不相同的号码，范围 1–12

算法是确定性的：任何人用相同的 144 个区块哈希都能**逐位元复现**同一期结果。真值源是比特币
区块链本身（全网共识、客观、不可篡改），而非本系统数据库或任何单一 API。

它以**纯静态、无服务器**的方式发布（GitHub Pages → lucky2049.com）：一个 GitHub Actions 定时
任务负责开奖+发布，验证完全在浏览器/命令行里完成，没有后端、关键链路上没有数据库。

> **范围说明：** 本项目**只负责开奖**。奖池、售票、兑奖等功能不在本项目内，由其他项目完成。
> 这样做是为了规避潜在法律风险。本项目仅供研究与娱乐，不构成任何博彩服务。

**算法规范：** 见 [`SPEC.md`](SPEC.md)（冻结版本 `v1`）。
**独立验证：** 命令行用 [`verify.py`](../../verify.py)，或打开 `verify.html` 页面在浏览器里自证。
**Repository:** https://github.com/RaynorZhong/lucky2049 · **Demo:** https://lucky2049.com

## 公平性保证

| 属性 | 状态 | 说明 |
|------|------|------|
| 可复现 | ✅ | 开源确定性算法 + 公开链上数据，任何人可重算 |
| 运营方零自由度 | ✅ | 第 N 期固定使用高度 `[N*144, N*144+143]`，从创世块锚定，无人工挑选 |
| 算法已冻结 | ✅ | `ALGO_VERSION="v1"`，每期声明版本；改规则须升版本且仅对未来期生效 |
| 防篡改（历史不可改） | ✅ | 每期串入哈希链承诺，全历史压成一个“链头”；外锚链头后运营方无法事后改历史（见下文「防篡改」） |
| 抗链重组 | ✅ | 区块滞后 `DRAW_CONFIRMATIONS`（默认 6）个确认才参与开奖，浅重组无法改变已开结果 |
| 矿工操纵 | ⚠️ 经济安全 | 144 块聚合把攻击成本推至极高；非密码学绝对安全，残余风险随下游奖池上升（详见 SPEC.md §7） |

## 算法（v1 摘要）

1. 取第 N 期的 144 个区块（高度升序），把它们的 64 字符小写十六进制哈希无分隔拼接为 `combined`。
2. `seed = SHA256(utf8(combined))`（32 字节摘要）。
3. 对 `counter = 0..6`：`int_k = HMAC_SHA256(seed, ascii(str(counter)))` 解析为 256 位大端整数。
4. 前区：从 `[1..35]` 池中 `idx = int_i mod len(pool)` 依次 `pop`，取 5 个，升序。
5. 后区：从 `[1..12]` 池中同法取 2 个，升序。

完整规范与测试向量见 [`SPEC.md`](SPEC.md)。

## 架构

整套“开奖 + 发布 + 验证”都跑在 GitHub 上，**无需服务器、无需数据库**：

- **开奖机（cron）** — [`refresh-pages.yml`](../../.github/workflows/refresh-pages.yml) 定时
  运行 [`scripts/extend_pages.py`](../../scripts/extend_pages.py)：对每个**完全确认**的 144 区块新窗口，从
  **≥2 个独立源**取哈希并**要求它们一致**、用 `verify.py` 复算并接上承诺链，追加进 `index.json`，推回 `gh-pages`（各源不一致则**暂缓**该期、不发布，单个出错/分叉的浏览器无法污染历史）。纯 stdlib。
- **站点** — `web/`（`index.html`〔含下一期预计〕/ `verify.html` / `stats.html` / `trend.html`〔走势图〕）+ `static/`（`verify.js` / `stats.js` / `trend.js` /
  `style.css`）。浏览器里读 `index.json`，用自带 SHA-256/HMAC 复算校验，不信任服务器、不连数据库。
- **验证器** — `verify.py`：独立 stdlib 脚本，命令行复算 + 校验承诺链。

> 一个窗口约 144 个区块（≈ 24 小时）才成熟；cron **每小时**跑一次，成熟的窗口一小时内就会发布（没有新开奖的运行只刷新 `status.json` 源健康心跳）。仍然无服务器。

### 本地预览

```shell
python scripts/export_static.py --out /tmp/site   # 从本地 DB 缓存生成 index.json + 页面
python -m http.server -d /tmp/site 8000           # 打开 http://localhost:8000
```

## 静态数据

站点即数据源，无动态 API：

- `index.json` — 全量精简快照：`{count, head, algo_version, draws:[…]}`，每期含 id / 高度区间 / 前后区 /
  算法版本 / 承诺 / 前一承诺 / 时间戳（**不含 144 哈希**，约 2MB；哈希按需从链上取，链才是真值源）。
- `head.json` — 历史**链头**：承诺整段开奖历史的 32 字节哈希（外锚它即可固定历史，见「防篡改」）。
- `latest.json` — 最新一期 + 链头，轮询它；`feed.json` — 最近开奖的 [JSON Feed](https://jsonfeed.org)，订阅它。

**想在 lucky2049 上构建？** 开奖是一个可消费的公共信标——数据契约见 [`docs/SCHEMA.md`](SCHEMA.md)。
（本项目只发布开奖；奖池 / 票务 / 兑奖不在范围内。）

## 独立验证

`verify.py` 自包含、仅用标准库。给定期号，它从独立来源拉取 144 个哈希、按 SPEC v1 重算，并与已发布
快照比对结果 + 承诺链：

```shell
# 从公开浏览器复算，并与已发布站点比对（RESULT MATCH + CHAIN MATCH）
python verify.py 6315 --source mempool --site https://lucky2049.com

# 用自己的全节点作为真值源
export BITCOIN_RPC_URL="http://user:pass@127.0.0.1:8332"
python verify.py 6315 --source core

# 离线对本地数据库缓存
python verify.py 6315 --source db --db data/database.db
```

不想用命令行？直接打开站点的 `verify.html`，在浏览器里一键复算同样的校验。

## 防篡改

每期开奖都串入一条 SHA-256 哈希链承诺：

```
commitment = SHA256( 上一期承诺 | 期号 | 算法版本 | 种子 | 前区 | 后区 | 高度区间 )
```

于是整段历史被压缩成一个 32 字节的**链头**（`head.json`）。改动任何一期都会改变链头。**链头每周经
[OpenTimestamps](https://opentimestamps.org) 外锚到比特币区块链**（[`anchor-head.yml`](../../.github/workflows/anchor-head.yml)，
证明发布在 [`anchors/`](../../anchors/) 并同时服务于 `https://lucky2049.com/anchors/`），于是旧链头被第三方
时间戳固定，运营方无法在事后悄悄改写历史。关键在于 `verify.py` 与 `verify.html` 都能**独立重算**这条
链，承诺并非运营方自说自话；任何人可 `ots verify anchors/<id>.head.json.ots` 复核锚点。

## 代码结构

```
verify.py       独立引擎(标准库,单文件可复制):算法 generate、承诺链 commitment_for、
                区块哈希抓取(Core RPC / mempool / blockstream / sqlite)、--site CLI 验证器
scripts/
  extend_pages.py  cron 开奖+发布:从链上扩展 index.json(stdlib,复用 verify.py,无 DB)
  export_static.py 从本地 SQLite 缓存(stdlib sqlite3)重建整个 index.json + 站点(初建/灾备)
  publish-pages.sh 手动发布(调 export_static + 推 gh-pages);常态用 cron,二选一
web/            index.html(首页+下一期预计)/ verify.html / stats.html / trend.html(走势图)+ CNAME
static/         verify.js(验证器)、stats.js(频率+卡方)、trend.js(走势图)、style.css、favicon.svg —— 自带算法、无外部脚本
.github/workflows/  refresh-pages.yml(cron 发布)、tests.yml(算法/承诺/JS 锁)
SPEC.md         冻结算法规范 v1        docs/DEPLOY.md 部署   docs/TDD.md TDD 工作流
data/           database.db —— 可选本地缓存,gitignored、不随仓库分发,仅 export_static 重建时读
```

> 旧的 FastAPI 服务器 + 数据库 + Docker/Render 已从 `main` 移除,存档在 git tag `v1-server`,
> 需要实时 API/自托管时可从那里取回。`lucky.py`（gitignored）是独立的经济模拟器,与开奖无关。

## 测试

```shell
make install-dev   # 只装 pytest 工具(运行期纯标准库)
make test          # 跑一次
make watch         # 存盘即自动重跑(TDD 红-绿循环)
make cov           # 带覆盖率报告
python -m unittest discover -s tests   # 不装 pytest 也行(同一套)
```

测试是**标准库 + Node**(无 DB、无 fixture):算法/承诺的黄金向量锁(`test_spec_v1`、`test_commitment`)、
独立验证器(`test_verify_site`)、以及在 Node 里跑浏览器 JS 对拍(`test_verify_js`、`test_stats_js`)。
TDD 工作流见 [`TDD.md`](TDD.md)。CI(GitHub Actions)对每次 push/PR 跑这套锁。

## License

MIT License — 见 [LICENSE](../../LICENSE)。本项目仅供研究与娱乐，不构成博彩服务，请遵守当地法律。
