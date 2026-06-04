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

**Render(Blueprint 一键)** —— 仓库根已带 [`render.yaml`](../render.yaml)(Docker +
`/healthz` 健康检查 + 已接上 seed Release 的 `LOTTO_SEED_CSV_URL`)。在 Render 里
New > Blueprint 指向本仓库即可一键起;Railway 读 `Dockerfile`,Fly 用 `fly.toml` 同理。

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

## 路径 B:GitHub Pages 静态验证站(已实现)

最契合"公开透明",也是给"其他人"最省事的消费方式 —— **这就是"部署在 GitHub 上"**。

在有数据库的机器上(`gh auth login` 之后)一条命令发布:
```shell
./scripts/publish-pages.sh      # 导出 site/ 并推到 gh-pages 分支
```
- [`scripts/export_static.py`](../scripts/export_static.py):从库导出 **精简快照**
  `site/index.json`(全量号码:id/高度/结果/承诺/前一承诺,**不含 144 哈希**,约 2MB)
  + `head.json` + 静态页(`web/index.html`、`web/verify.html`、`static/verify.js`)。
- [`web/verify.html`](../web/verify.html):纯静态自证页 —— 给定期号,**浏览器内从
  mempool.space 拉那 144 个区块哈希**、用 `verify.js`(自带 SHA-256/HMAC、零外部脚本)
  复算,并与发布的结果/承诺链比对。不信任服务器、不连数据库。
- **首次**之后到仓库 Settings > Pages 选 `gh-pages` 分支启用一次(或 `gh api`,脚本里有注释)。

站点地址:`https://<owner>.github.io/<repo>/`。因为快照不含哈希(只 ~2MB),验证时按需
从链上抓哈希 —— 链才是真值源,这也是最 trustless 的校验。

**自定义域名(lucky2049.com)** —— `web/CNAME` 存着域名,发布时复制进 `site/`(`export_static.py`
与 `refresh-pages.yml` 都会带上)。这一步**不能省**:两个发布脚本都是 force-push 重建 `gh-pages`,
要是不带 `CNAME`,GitHub 会在下一次刷新时把自定义域名清掉、站点回到 404。DNS 侧:根域 `lucky2049.com`
用 4 条 A 记录指向 `185.199.108–111.153`(根域不能用 CNAME),`www` 用 CNAME 指向 `<owner>.github.io`。
首次需 `gh api -X PUT repos/<owner>/<repo>/pages -f cname=lucky2049.com` 登记一次(或 Settings > Pages 填一次)。

---

## 路径 C:GitHub Actions 自动刷新(已实现 —— 无需服务器)

[`.github/workflows/refresh-pages.yml`](../.github/workflows/refresh-pages.yml) 每天定时
(也可手动 `workflow_dispatch`)运行 [`scripts/extend_pages.py`](../scripts/extend_pages.py):
checkout `gh-pages` 上当前的 `index.json` → 对**新确认的 144 区块窗口**从 mempool.space
抓哈希、用 `verify.py` 续算并接上承诺链 → 推回 `gh-pages` → Pages 自动重建。

**不需要数据库、不需要服务器、纯 stdlib**(`extend_pages.py` 复用 `verify.py` 的算法,
GitHub 托管 runner 即可跑)。这让整套"开奖 + 发布"完全跑在 GitHub 上,**用不着 Render**。

> 单一发布源:启用本 cron 后,gh-pages 上的 `index.json` 就是权威快照。**别再本地跑
> `publish-pages.sh`**(它会用本地库的导出覆盖,可能与 cron 续算冲突)。要么只用 cron,
> 要么只用本地 publish,二选一。
>
> 注:`refresh-pages.yml` 与跑测试的 `tests.yml` 是两个独立 workflow,互不影响。

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
