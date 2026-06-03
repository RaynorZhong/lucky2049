# 部署指南 / Deployment

lucky2049 是**开奖引擎**:一个 FastAPI 服务,定时(每 10 分钟)把比特币区块哈希
确定性地变成开奖号码并入库。下面给出三条部署路径,以及"高效同步号码"的做法。

> 先理解一个前提:**开奖是确定性 + 可验证的**。任意一期都能用区块高度
> `[N*144, N*144+143]` 的 144 个哈希按 `SPEC.md` 重算。所以数据库只是缓存——
> 新部署**不需要**搬那个 ~170MB 的 `data/database.db`。这决定了下面所有"同步"策略。

---

## 能直接放 GitHub 吗?

- **不能**把这个常驻 FastAPI 服务跑在 GitHub 上(GitHub 不托管动态后端)。
- **可以**用两种 GitHub 原生能力:
  - **GitHub Pages** 托管**静态验证站**:验证是纯前端的(`static/verify.js` 在浏览器里
    用自带 SHA-256/HMAC 复算),把开奖历史导成 JSON + 验证页即可免费 CDN 托管。
  - **GitHub Actions(cron)** 当"开奖机":定时拉块、算号、提交结果,无需常驻服务器。

---

## 路径 A:一键容器部署(实时服务)

项目已 Docker 化(`Dockerfile` + `compose.yaml`),可部署到任意容器平台。

**本地 / 自托管**
```shell
docker compose up --build      # http://localhost:8000
```

**Render(Blueprint 一键)** — 在仓库根加 `render.yaml`:
```yaml
services:
  - type: web
    name: lucky2049
    runtime: docker
    plan: starter
    healthCheckPath: /healthz
    envVars:
      - key: DRAW_CONFIRMATIONS
        value: "6"
      # - key: BITCOIN_RPC_URL      # 可选:自有全节点(权威真值源)
      #   sync: false
```
然后用 “Deploy to Render” 按钮 / Blueprint 一键起。Railway、Fly.io 同理
(Railway 读 `Dockerfile`;Fly 用 `fly.toml`)。

**环境变量**
| 变量 | 作用 | 默认 |
|------|------|------|
| `BITCOIN_RPC_URL` | 自有 Bitcoin Core(权威源);留空则用 mempool.space | 空 |
| `DRAW_CONFIRMATIONS` | 入库/开奖前的确认缓冲(抗重组) | 6 |
| `LOTTO_DB_URL` | 数据库位置 | `data/database.db` |
| `LOTTO_SEED_CSV_URL` | 冷启动播种 CSV 的下载地址(见下) | 空 |

> 健康检查打 `/healthz`。

### 冷启动播种 CSV

~82MB 的 `data/blockchain_timeup898560.csv` **不再随仓库分发**(太大,已从 git 历史移除)。
新部署三选一:① 设 `LOTTO_SEED_CSV_URL` 首启自动下载;② 什么都不设,空表启动,
调度器从链上慢慢补;③ 拉一个 sqlite 快照(见下面"高效同步号码")。本地已有该 CSV 时照常使用。

**把 CSV 发布成 Release(维护者做一次)** —— 在有本地 CSV 的机器上,`gh auth login` 后:
```shell
./scripts/publish-seed.sh          # 需要 gh CLI;创建/更新 Release seed-v1 并上传 CSV
```
之后新部署设:
```shell
export LOTTO_SEED_CSV_URL="https://github.com/RaynorZhong/lucky2049/releases/download/seed-v1/blockchain_timeup898560.csv"
```
> 资产校验:`sha256 = 1c4d83d98fdf9f17ce0009cfe0ed0e5620008f81b300a75b86dab5693cf02877`

---

## 路径 B:GitHub Pages 静态验证站(只读 + 自证)

最契合"公开透明"的玩法,也是给"其他人"最省事的消费方式。

思路:开奖机(路径 A 的服务,或下面的 Actions)产出每期 manifest →
导成静态 JSON → Pages 托管 JSON + 一个读 JSON 的验证页。访客在浏览器里
本地复算,**不信任服务器、不连数据库**。

需要新增(尚未实现,按需再做):
1. **导出脚本** `scripts/export_static.py`:遍历 draws,写
   `site/draws/<id>.json`(复用 `lotto.build_draw_manifest`)+ 一个
   `site/index.json` 汇总 + 最新 `commitment head`。
2. **静态验证页**:把现有 `templates/verify.html` 改成读 `site/*.json`
   (而不是 `/api/...`),复用 `static/verify.js`(已是纯前端、零外部脚本)。
3. **Pages workflow** `.github/workflows/pages.yml`:导出 → 上传 → 部署 Pages。

---

## 路径 C:GitHub Actions 当"开奖机"(无常驻服务器)

定时 cron 拉块、算号、提交结果,适合不想养服务器的部署者。

需要新增(尚未实现):
- `.github/workflows/draw.yml`:`schedule: cron` 每天触发 → 装依赖 →
  跑 `python -c "from app.lotto import update_draws; update_draws()"` →
  `git commit` 新增的开奖 JSON(配合路径 B 的导出)。
- 要确定性,建议配 `BITCOIN_RPC_URL`(或固定的可信浏览器)作为哈希源。

---

## 高效同步号码 / Efficient sync

**核心:不要搬 170MB 库,也不要重放 86MB CSV。** 按场景选:

| 方案 | 同步量 | 适合 |
|------|--------|------|
| 拉**静态 JSON 历史** + 客户端验证(推荐) | 几 MB | 只读 / 验证 / Pages 消费者 |
| 自建 **Bitcoin 节点**按需重算 | 0(链即真值) | 重视独立性的部署者 |
| 发布 **sqlite 快照**(GitHub Release) | 一个压缩 db | 想要现成缓存、快速起服务 |
| 重放 CSV 再追块(现状默认) | 慢 | 不推荐 |

无论哪种,真值源始终是比特币区块链——任何人都能用 `verify.py` 或 `/verify`
页面独立复核,并用 `commitment head`(`/api/commitments/head`)核对历史未被篡改。

## 自证 / Verify

```shell
python verify.py <draw_id> --site <url>        # 复算号码 + 校验承诺链
python verify.py <draw_id> --source core       # 用自有全节点作真值源
```
或直接打开部署后的 `/verify` 页面,在浏览器里一键复核。
