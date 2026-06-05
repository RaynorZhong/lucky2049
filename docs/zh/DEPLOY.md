# 部署指南 / Deployment

> 🌏 [English](../DEPLOY.md) · **中文**

lucky2049 是**纯静态、无服务器**的开奖引擎:出号、发布、验证全部跑在 GitHub 上,
没有常驻后端,关键链路上没有数据库。

> 前提:**开奖是确定性 + 可验证的**。任意一期都能用区块高度 `[N*144, N*144+143]`
> 的 144 个哈希按 `SPEC.md` 重算。所以数据库只是可选的本地缓存——发布与验证都**不需要**
> 那个 ~170MB 的 `data/database.db`。

---

## 架构:全跑在 GitHub 上

- **开奖机(cron)** — [`.github/workflows/refresh-pages.yml`](../../.github/workflows/refresh-pages.yml)
  定时(也可手动 `workflow_dispatch`)运行 [`scripts/extend_pages.py`](../../scripts/extend_pages.py):
  读 `gh-pages` 当前的 `index.json` → 对每个**新确认**的 144 区块窗口从 ≥2 个独立源抓哈希并要求一致（不一致则暂缓该期）、
  用 `verify.py` 续算并接上承诺链 → 推回 `gh-pages` → Pages 自动重建。**纯 stdlib、无 DB、无服务器**。
- **站点** — `gh-pages` 上的 `index.json`/`head.json` + `web/`(页面)+ `static/`(JS/CSS)。
  GitHub Pages 免费 CDN 托管;`verify.html` 在浏览器内自带 SHA-256/HMAC 复算,不信任服务器。
- **链头外锚** — [`.github/workflows/anchor-head.yml`](../../.github/workflows/anchor-head.yml) 每周用
  OpenTimestamps 把当前链头(`head.json`)盖时间戳到比特币链上;证明提交进 `anchors/`,并服务于 `/anchors/`。

> 一期开奖需 144 个区块(≈24h)才产生,日更 cron 已足够;想更快可把 `refresh-pages.yml` 的
> cron 改成每小时,仍然无服务器。

---

## 首次启用 Pages + 自定义域名

1. 让 `gh-pages` 上先有一份快照(见下「重建/灾备」),到仓库 **Settings > Pages** 选 `gh-pages`
   分支启用一次(或 `gh api -X PUT repos/<owner>/<repo>/pages -f cname=lucky2049.com`)。
2. 站点地址:`https://<owner>.github.io/<repo>/`,或自定义域名。

**自定义域名(lucky2049.com)** —— `web/CNAME` 存着域名,发布时复制进 `site/`(`export_static.py`
与 `refresh-pages.yml` 都会带上)。这一步**不能省**:发布器会重建 `gh-pages`,
要是不带 `CNAME`,GitHub 会在下一次刷新时把自定义域名清掉、站点回到 404。
- DNS:根域 `lucky2049.com` 用 4 条 A 记录指向 `185.199.108–111.153`(根域不能用 CNAME),
  `www` 用 CNAME 指向 `<owner>.github.io`。
- 登记域名若遇 `gh api ... -f cname=... -F https_enforced=...` 报 `certificate does not exist yet`,
  改为直接往 `gh-pages` 提交 `CNAME` 文件(`gh api -X PUT .../contents/CNAME ... -f branch=gh-pages`),
  Pages 构建会自动认领域名并签证书。

---

## 日常运维

- **单一发布源**:cron 接管后,`gh-pages` 上的 `index.json` 就是权威快照。**别再本地跑
  `scripts/publish-pages.sh`**(它会用本地 DB 导出 force-push 覆盖,可能与 cron 续算冲突)。
  二选一:常态用 cron。
- **手动发一期**:`gh workflow run refresh-pages.yml`(改完 `web/`/`static/` 想立刻上线时用)。
- `refresh-pages.yml` 与跑测试的 `tests.yml` 是两个独立 workflow,互不影响。

---

## 重建 / 灾备

`index.json` 是系统的权威快照,建议定期备份(它在 `gh-pages` git 历史里,也可发个 Release)。链头已由
`anchor-head.yml` 每周经 OpenTimestamps 外锚到比特币链(见 `anchors/`),历史防篡改更稳。万一 `gh-pages`
丢失,两条重建路径:

```shell
# A) 有本地 DB 缓存时:stdlib sqlite3 直读,秒级重建整份快照
python scripts/export_static.py --out site --db data/database.db

# B) 没有 DB 时:从一份空 index.json 让 cron 从创世开始从链上重算(慢,可重复跑/调大 MAX_NEW_DRAWS)
echo '{"count":0,"head":{},"algo_version":"v1","draws":[]}' > site/index.json
MAX_NEW_DRAWS=500 python scripts/extend_pages.py site/index.json   # 重复运行直到追平链尖
```

`data/database.db` 是**可选本地缓存**(gitignored、不随仓库分发),只有路径 A 会读它;
cron 永远不碰 DB。

---

## 自证 / Verify

```shell
python verify.py <draw_id> --site https://lucky2049.com   # 复算号码 + 校验承诺链(静态站亦可)
python verify.py <draw_id> --source core                   # 用自有全节点作真值源
python verify.py <draw_id> --source db --db data/database.db   # 离线对本地缓存
```

或直接打开站点的 `verify.html`,在浏览器里一键复核。真值源始终是比特币区块链——任何人都能用
`verify.py` 或 `verify.html` 独立复算,并用 `head.json` 的链头核对历史未被篡改。

> 旧的容器/Render 实时服务部署方式已移除,存档在 git tag `v1-server`,需要时可取回。
