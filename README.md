# lucky2049 — 可验证的比特币哈希开奖系统 / Verifiable Bitcoin-Hash Draw System

## Overview

**lucky2049** 是一个**只做开奖**的、公开透明、可独立复现的开号系统。它用 **144 个连续的比特币
主网区块哈希**作为公开且不可预测的熵源，经确定性算法生成一期 **超级大乐透 (Super Lotto)** 号码：

- 前区 (front)：5 个互不相同的号码，范围 1–35
- 后区 (back)：2 个互不相同的号码，范围 1–12

算法是确定性的：任何人用相同的 144 个区块哈希都能**逐位元复现**同一期结果。真值源是比特币
区块链本身（全网共识、客观、不可篡改），而非本系统数据库或任何单一 API。

> **范围说明：** 本项目**只负责开奖**。奖池、售票、兑奖等功能不在本项目内，由其他项目完成。
> 这样做是为了规避潜在法律风险。本项目仅供研究与娱乐，不构成任何博彩服务。

**算法规范：** 见 [`SPEC.md`](SPEC.md)（冻结版本 `v1`）。
**独立验证：** 命令行用 [`verify.py`](verify.py)，或打开 `/verify` 页面在浏览器里自证。
**Repository:** https://github.com/RaynorZhong/lucky2049 · **Demo:** http://www.lucky2049.com:8000/

## 公平性保证 / Fairness Properties

| 属性 | 状态 | 说明 |
|------|------|------|
| 可复现 | ✅ | 开源确定性算法 + 公开链上数据，任何人可重算 |
| 运营方零自由度 | ✅ | 第 N 期固定使用高度 `[N*144, N*144+143]`，从创世块锚定，无人工挑选 |
| 算法已冻结 | ✅ | `ALGO_VERSION="v1"`，每期声明版本；改规则须升版本且仅对未来期生效 |
| 防篡改（历史不可改） | ✅ | 每期串入哈希链承诺，全历史压成一个"链头"；外锚链头后运营方无法事后改历史（见下文「防篡改」） |
| 抗链重组 | ✅ | 区块滞后 `DRAW_CONFIRMATIONS`（默认 6）个确认才入库/开奖，浅重组无法改变已开结果 |
| 矿工操纵 | ⚠️ 经济安全 | 144 块聚合把攻击成本推至极高；非密码学绝对安全，残余风险随下游奖池上升（详见 SPEC.md §7） |

## 算法（v1 摘要）

1. 取第 N 期的 144 个区块（高度升序），把它们的 64 字符小写十六进制哈希无分隔拼接为 `combined`。
2. `seed = SHA256(utf8(combined))`（32 字节摘要）。
3. 对 `counter = 0..6`：`int_k = HMAC_SHA256(seed, ascii(str(counter)))` 解析为 256 位大端整数。
4. 前区：从 `[1..35]` 池中 `idx = int_i mod len(pool)` 依次 `pop`，取 5 个，升序。
5. 后区：从 `[1..12]` 池中同法取 2 个，升序。

完整规范与测试向量见 [`SPEC.md`](SPEC.md)。

## 运行 / Running

```shell
docker compose up --build       # http://localhost:8000
# 或本地：
pip install -r requirements.txt
uvicorn app.main:app            # 或 fastapi run app/main.py
```

应用启动后每 10 分钟检查新区块、自动出新一期（APScheduler）。

部署（容器一键 / GitHub Pages 静态验证站 / Actions 开奖机）与"高效同步号码"见 [`docs/DEPLOY.md`](docs/DEPLOY.md)。

### 比特币哈希来源（全节点）

系统以**自建比特币全节点**作为哈希真值源（canonical source of truth），公开浏览器仅作降级备援。
通过环境变量配置 JSON-RPC：

```shell
export BITCOIN_RPC_URL="http://user:pass@127.0.0.1:8332"
# 或分项：BITCOIN_RPC_USER / BITCOIN_RPC_PASSWORD / BITCOIN_RPC_HOST / BITCOIN_RPC_PORT
```

未配置节点时回退到 mempool.space。节点搭建参考 Bitcoin Core 文档（`bitcoind` + `getblockhash`/`getblockheader` RPC）。

### 确认数 / Confirmations

区块只在被埋够确认数后才入库，避免浅层链重组在已开结果之后改写区块哈希：

```shell
export DRAW_CONFIRMATIONS=6     # 默认 6；高价值场景可调大（延迟换安全）
```

## 接口 / API

- `GET /api/spec` — 机器可读的算法规范摘要（版本、参数、选块规则）。
- `GET /api/draw/{id}` — 某期结果及所用区块。
- `GET /api/draw/{id}/manifest` — **每期算法声明**：期号、算法版本、高度区间、144 个哈希、种子、结果、**承诺（commitment）与前一期承诺**、验证说明（含自复算校验）。
- `GET /api/commitments/head` — **历史链头**：一个承诺整段开奖历史的 32 字节哈希。
- `GET /api/draws` / `GET /api/index` — 列表与首页数据。
- `GET /verify` — **浏览器内自证页面**：纯前端用自带 SHA-256/HMAC 重算某期号码与承诺链，无需信任服务器、不加载任何外部脚本。

## 独立验证 / Verify

`verify.py` 自包含、仅用标准库。给定期号，它从独立来源拉取 144 个哈希、按 SPEC v1 重算、并可与已发布结果比对：

```shell
# 用公开浏览器复算并与线上结果比对（含哈希交叉校验，可检测数据库篡改）
python verify.py 6315 --source mempool --site http://www.lucky2049.com:8000

# 用自己的全节点作为真值源
export BITCOIN_RPC_URL="http://user:pass@127.0.0.1:8332"
python verify.py 6315 --source core

# 离线对本地数据库
python verify.py 6315 --source db --db data/database.db
```

加上 `--site` 时，`verify.py` 还会重算该期的**承诺链**并校验它正确链到上一期（`CHAIN MATCH`）。
不想用命令行？直接打开 `GET /verify` 页面，在浏览器里一键复算。

## 防篡改 / Tamper-evidence

每期开奖都串入一条 SHA-256 哈希链承诺：

```
commitment = SHA256( 上一期承诺 | 期号 | 算法版本 | 种子 | 前区 | 后区 | 高度区间 )
```

于是整段历史被压缩成一个 32 字节的**链头**（`GET /api/commitments/head`）。改动任何一期都会
改变链头。把链头**定期外锚**到不可篡改的见证处（OpenTimestamps、git tag、公开发帖等），运营方
就无法在事后悄悄改写历史——因为旧链头已被第三方/时间戳固定。关键在于 `verify.py` 与 `/verify`
页面都能**独立重算**这条链，承诺并非运营方自说自话。

> 部署本特性后运行一次 `lotto.backfill_commitments()` 为历史各期回填承诺链（幂等）。

## 代码结构 / Structure

```
app/            应用包（FastAPI 服务 + 开奖引擎）
  main.py       路由（含 /verify、/healthz、/api/commitments/head）+ 调度（每 10 分钟）
  lotto.py      开奖引擎：generate_lotto_numbers_bitcoin、build_draw_manifest、
                承诺链（backfill_commitments、get_commitment_head）、ALGO_VERSION、统计
  bitcoin.py    区块哈希抓取：全节点 RPC（主）+ mempool.space（备援）；CONFIRMATIONS 确认缓冲
  models.py     SQLModel/SQLite（Draw 含 algo_version、commitment 列）+ 幂等轻量迁移
verify.py       独立验证脚本（标准库，留在根目录便于单文件复制）：号码复算 + 承诺链校验
static/         style.css、verify.js（浏览器内验证器，自带 SHA-256/HMAC、无外部脚本）
templates/      Jinja2 页面（index/draw/stats/logs/verify）
data/           blockchain_timeup898560.csv（冷启动播种）、database.db（运行时，gitignored）
tests/          回归测试 + conftest（隔离 fixture）；CI 见 .github/workflows/tests.yml
SPEC.md         冻结算法规范 v1        docs/TDD.md   TDD 工作流
```

> 运行：`uvicorn app.main:app`（Docker：`docker compose up`）。
> `lucky.py`（gitignored）是独立的经济/奖金模拟器，**不是开奖引擎**，与本系统开号无关。

## 测试 / Tests

```shell
make install-dev   # pytest + pytest-cov + pytest-watcher 装进 venv
make test          # 跑一次
make watch         # 存盘即自动重跑（TDD 红-绿循环）
make cov           # 带覆盖率报告
# 不装 pytest 也行：python -m unittest discover -s tests（仅标准库即可跑核心算法锁）
```

测试隔离：`tests/conftest.py` 把数据库指向临时文件，提供 `db`（每个测试一份干净表）与
`client`（无 lifespan 副作用的 TestClient）两个 fixture，测试永远碰不到真实库。
TDD 工作流与"测试先行"示例见 [`docs/TDD.md`](docs/TDD.md)。

CI（GitHub Actions）对每次 push/PR 运行：一个零依赖的「算法锁」任务（黄金测试向量），加一个装全依赖的完整任务。

## License

MIT License — 见 [LICENSE](LICENSE)。本项目仅供研究与娱乐，不构成博彩服务，请遵守当地法律。
