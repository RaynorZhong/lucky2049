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
**独立验证：** 见 [`verify.py`](verify.py)。
**Repository:** https://github.com/RaynorZhong/lucky2049 · **Demo:** http://www.lucky2049.com:8000/

## 公平性保证 / Fairness Properties

| 属性 | 状态 | 说明 |
|------|------|------|
| 可复现 | ✅ | 开源确定性算法 + 公开链上数据，任何人可重算 |
| 运营方零自由度 | ✅ | 第 N 期固定使用高度 `[N*144, N*144+143]`，从创世块锚定，无人工挑选 |
| 算法已冻结 | ✅ | `ALGO_VERSION="v1"`，每期声明版本；改规则须升版本且仅对未来期生效 |
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
fastapi run main.py             # 或 uvicorn main:app
```

应用启动后每 10 分钟检查新区块、自动出新一期（APScheduler）。

### 比特币哈希来源（全节点）

系统以**自建比特币全节点**作为哈希真值源（canonical source of truth），公开浏览器仅作降级备援。
通过环境变量配置 JSON-RPC：

```shell
export BITCOIN_RPC_URL="http://user:pass@127.0.0.1:8332"
# 或分项：BITCOIN_RPC_USER / BITCOIN_RPC_PASSWORD / BITCOIN_RPC_HOST / BITCOIN_RPC_PORT
```

未配置节点时回退到 mempool.space。节点搭建参考 Bitcoin Core 文档（`bitcoind` + `getblockhash`/`getblockheader` RPC）。

## 接口 / API

- `GET /api/spec` — 机器可读的算法规范摘要（版本、参数、选块规则）。
- `GET /api/draw/{id}` — 某期结果及所用区块。
- `GET /api/draw/{id}/manifest` — **每期算法声明**：期号、算法版本、高度区间、144 个哈希、种子、结果、验证说明（含自复算校验）。
- `GET /api/draws` / `GET /api/index` — 列表与首页数据。

## 独立验证 / Verify

`verify.py` 自包含、仅用标准库。给定期号，它从独立来源拉取 144 个哈希、按 SPEC v1 重算、并可与已发布结果比对：

```shell
# 用公开浏览器复算并与线上结果比对（含哈希交叉校验，可检测数据库篡改）
python verify.py 6315 --source mempool --site http://www.lucky2049.com:8000

# 用自己的全节点作为真值源
export BITCOIN_RPC_URL="http://user:pass@127.0.0.1:8332"
python verify.py 6315 --source core

# 离线对本地数据库
python verify.py 6315 --source db --db db/database.db
```

## 代码结构 / Structure

- `main.py` — FastAPI 应用、路由、定时调度（每 10 分钟）。
- `lotto.py` — 开奖引擎：`generate_lotto_numbers_bitcoin`、`verify_lotto_numbers`、`build_draw_manifest`、`get_spec`、`ALGO_VERSION`、卡方统计。
- `bitcoin.py` — 区块哈希抓取：全节点 RPC（主）+ 公开浏览器（备援）。
- `db/models.py` — SQLModel/SQLite 模型与读写（`Draw` 含 `algo_version` 列）。
- `verify.py` — 独立验证脚本（标准库）。
- `SPEC.md` — 冻结算法规范 v1。
- `lucky.py` — **独立的经济/奖金模拟器**（自带一套 69/26 示例参数），**不是开奖引擎**，与本系统开号无关。

## License

MIT License — 见 [LICENSE](LICENSE)。本项目仅供研究与娱乐，不构成博彩服务，请遵守当地法律。
