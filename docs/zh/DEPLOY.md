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
- **链头外锚** — [`.github/workflows/anchor-head.yml`](../../.github/workflows/anchor-head.yml) 每日用
  OpenTimestamps 把当前链头(`head.json`)盖时间戳到比特币链上;证明提交进 `anchors/`,并服务于
  `/anchors/<id>.head.json(.ots)`(没有目录列表;已锚定的期号清单见 GitHub 上的
  [`anchors/`](../../anchors/) 目录)。该工作流特意拆成两个 job:`stamp` 用只读令牌、按哈希锁定安装
  ([`ops/anchor-requirements.txt`](../../ops/anchor-requirements.txt))来运行从 PyPI 装的 OpenTimestamps
  客户端;`commit` 持有写令牌、不运行任何第三方代码,只通过
  [`scripts/accept_anchors.py`](../../scripts/accept_anchors.py) 放行盖好戳的文件。

> 一个窗口约 144 个区块(≈24h)才成熟;`refresh-pages.yml` 的设计是**每小时**跑一次,成熟的窗口
> 一小时内就会发布(没有新开奖的运行只刷新 `status.json` 源健康心跳,提交信息为
> "Refresh source health")——仍然无服务器。
>
> GitHub 并不可靠地兑现每小时的 `schedule:`(到 2026 年 11 月,本仓库每天只触发约 4 次),所以
> 每小时的节奏由一个很小的外部触发器保证——见下文[外部触发器](#外部触发器cloudflare-worker)。
> 工作流自身的 `schedule:` 保留作兜底;触发器不含任何开奖逻辑:决定和发布开奖的一切仍跑在 GitHub 上。

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
- **验证域名**(一次性):GitHub 账号 **Settings → Pages → Add a domain** → 填 `lucky2049.com` →
  按提示创建 `_github-pages-challenge-<user>` TXT 记录(仅 DNS),并一直保留。未验证时,如果本
  Pages 站点哪天被取消发布而 DNS 仍指向 GitHub,别的 GitHub 账号可以认领这个域名。用
  `gh api repos/<owner>/<repo>/pages --jq .protected_domain_state` 检查(应为 `verified`)。

---

## 日常运维

- **单一发布源**:cron 接管后,`gh-pages` 上的 `index.json` 就是权威快照。**别再本地跑
  `scripts/publish-pages.sh`**(它会用本地 DB 导出 force-push 覆盖,可能与 cron 续算冲突)。
  二选一:常态用 cron。
- **手动发一期**:`gh workflow run refresh-pages.yml`(改完 `web/`/`static/` 想立刻上线时用)。
- `refresh-pages.yml` 与跑测试的 `tests.yml` 是两个独立 workflow,互不影响。
- **时间预算**:开奖脚本并发抓取各数据源,每个源每个窗口最多 `SOURCE_BUDGET_S`(默认 300 秒),
  链尖探测失败的源本次直接跳过,超过 `RUN_BUDGET_S`(默认 900 秒)后不再开始新的开奖或审计——
  一个慢源最多丢掉自己那一票,拖不垮整次运行;正常情况下远在 30 分钟的 job 超时之内结束(最坏约为
  `RUN_BUDGET_S` + 一个 `SOURCE_BUDGET_S` + 一次时间戳查询)。代价:探测失败的源本次不投哈希票,
  所以三个源时,同一次运行里若再有一个源出错,该期会暂缓到下一个整点——可自愈,绝不会错开。
- **`anchor-head` 运行变红并显示 `REJECTED the stamp job's output`**:commit job 拒收了盖戳产物,
  什么都没提交。若是刚开奖几分钟后出现 `… is not a published draw`,那只是 `index.json` 的 CDN
  旧副本(它会先重试几分钟),下一次每日运行自愈。其他原因则说明 stamp job 产出了不该有的东西:
  先看日志再决定是否重跑。闸门把盖戳方限制在 `anchors/` 内、并把每份证明绑定到已发布的链头,
  但不会拿证明去对照比特币链——请不时对最近的证明跑一次 `ots verify`
  (见 [`anchors/README.md`](../../anchors/README.md))。

---

## 监控(停摆报警)

运行失败会通知你;根本没有运行则不会。触发器令牌过期、schedule 被停用、GitHub 故障,都是无声的。
所以每个工作流的最后一步可以向外部监控发一次 ping,由它在 ping **停止**时报警。不设置 secret 时
两者都是空操作:

| Secret(仓库 → Settings → Secrets and variables → Actions) | 谁来 ping | 监控端建议的周期 |
|---|---|---|
| `HEARTBEAT_URL` | `refresh-pages.yml`,在一次完全健康的运行之后 | 周期 1 小时,宽限 2 小时 |
| `ANCHOR_HEARTBEAT_URL` | `anchor-head.yml`,在证明已提交且没有卡在 pending 的之后 | 周期 1 天,宽限 12 小时 |

任何提供普通 ping 地址的"定时任务监控"服务都行(例如 [healthchecks.io](https://healthchecks.io)
的免费检查)。每小时的 ping 配 2 小时宽限时,单是外部触发器失效就足以报警,因为 GitHub 自己的
schedule 每隔几小时才触发一次。这个地址要当作 secret:知道它的人可以让检查一直显示正常。

---

## 外部触发器(Cloudflare Worker)

[`ops/trigger-worker/`](../../ops/trigger-worker/) 使用两个 Cloudflare Cron Trigger。每次事件按
完整匹配的 cron 表达式,只触发 `main` 上的一个工作流:

| Cloudflare cron(UTC) | 北京时间 | 工作流 | GitHub schedule 兜底(UTC) |
|---|---|---|---|
| `50 * * * *` | 每小时 :50 | `refresh-pages.yml`(`WORKFLOW_FILE`) | `20 * * * *` |
| `35 2 * * *`(`ANCHOR_CRON`) | 每日 10:35 | `anchor-head.yml`(`ANCHOR_WORKFLOW_FILE`) | `40 2 * * *` |

`workflow_dispatch` 避开 GitHub 定时事件的延迟投递。Worker 没有公开 URL
(`workers_dev = false`、无路由),HTTP 请求不会触发任务;未知 cron 在发请求前报错。
两个 GitHub schedule 都保留作兜底,各工作流现有的并发组负责将重叠运行串行化。
现有 Worker secret `GITHUB_TOKEN` 已覆盖两种触发,无需新增令牌或扩大权限。
修改每日表达式时,须同步修改 `triggers.crons` 和 `ANCHOR_CRON`。

**部署(一次性):**

1. 创建一个**细粒度个人访问令牌**(GitHub → Settings → Developer settings → Personal access
   tokens → Fine-grained tokens):*Repository access* 只选本仓库;*Repository permissions* → **Actions: Read and
   write**(Metadata: Read-only 会自动加上);其余一概不给。选一个不超过 1 年的
   有效期(GitHub 也提供 "No expiration"——不要选:会过期的令牌在泄露时损失有限),并在日历里设好提醒。
2. 在 `ops/trigger-worker/` 目录下:
   ```shell
   npx wrangler login
   npx wrangler deploy
   npx wrangler secret put GITHUB_TOKEN     # 粘贴令牌;它只作为 Worker secret 保存
   ```
3. 每小时 `:50` 之后检查 `gh run list --workflow=refresh-pages.yml -L 3`,02:35 UTC 之后检查
   `gh run list --workflow=anchor-head.yml -L 3`,两者都应出现成功的 `workflow_dispatch` 运行。
   `npx wrangler tail`(或 Worker 控制台日志)会标出 cron,并打印
   `dispatched refresh-pages.yml …` 或 `dispatched anchor-head.yml …`。
   cron 修改最多需要 15 分钟传播;同时核对已部署的定时配置和调用结果。
   单独一次本地 `--test-scheduled` 请求不能证明生产定时触发正常。

**轮换令牌**(到期前,或怀疑泄露时):按第 1 步新建一个,再跑一次
`npx wrangler secret put GITHUB_TOKEN`(无需重新部署),然后在 GitHub 上删掉旧令牌。

**如果它停了:**令牌过期/被撤销时,每次调用都会失败,Worker 日志里是 `HTTP 401 … rotate it`。
两个工作流会退回 GitHub 自己的 schedule,但触发可能延迟;首页数据源一行在超过 4 小时未刷新后
显示 "stale"。锚定工作流应单独检查,并保留每日停摆监控。

**如果令牌泄露:**它不能推代码、不能读 secrets,但并非无害。细粒度令牌无法收窄到某一个工作流或
某一个 ref,所以 "Actions: Read and write" 允许持有者在**任意 ref** 上触发本仓库的**任意**工作流、
重跑或取消运行、删除运行日志/产物/缓存,以及禁用工作流。最锋利的一处是存档的 `v1-server` 标签:
那里的旧版 `refresh-pages.yml` 仍可被触发,它会用单一哈希源强制推送 `gh-pages`。下一次从 `main`
的运行会把站点恢复,但 `gh-pages` 的历史就没了。所以:一有怀疑立即吊销令牌,检查有没有工作流被
禁用(`gh workflow list --all`),并从 `main` 重跑一次 `refresh-pages`。

**建议的加固(一次性,在仓库 Settings → Rules → Rulesets 里):**对 `gh-pages` 和 `main` 禁止强制
推送和删除。`main` 上的所有工作流都用普通推送,正常流程不受影响,而存档发布器的 `push -f` 就再也
改写不了站点。(`scripts/publish-pages.sh` 也是强制推送——它是灾备路径;真要用时临时解除规则。)

**移除:**在 `ops/trigger-worker/` 下执行 `npx wrangler delete`,并在 GitHub 上删除令牌。

---

## 可选:用自己的 Bitcoin Core 节点作为发布源

在仓库的 **Actions secrets** 里配置后,cron 会把你的节点当作*优先*源,并成为哈希一致性投票者之一
(与 mempool.space / blockstream.info 并列):

- `BITCOIN_RPC_URL` — `https://rpcuser:rpcpassword@你的RPC域名/`(Basic 认证会以显式请求头发送;
  urllib 单靠 URL 里的 userinfo 是不会认证的)。
- `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` — 仅当端点位于 Cloudflare Access 服务令牌
  (Zero Trust)之后;设置后客户端会附带令牌请求头。

行为:不配 secrets → 只用区块浏览器,与之前完全一致。节点离线/不可达 → 它的票自然缺席,两个浏览器
仍满足 `MIN_SOURCE_AGREEMENT=2`,发布照常。节点仍在初始区块下载(IBD)→ 它对链尖弃权(过期高度
不能拖延开奖),但哈希查询仍由其头索引照常应答。剪枝节点没问题——任意高度的区块哈希与头时间戳都
来自头索引。cron 日志会显示每个源的探测结果(`source core: ok tip=<h> (12ms)`)、法定链尖及其
领先源(`tip <h> (quorum of 2; leading source core)`),以及每期哈希由谁达成一致。

**节点监控:**每小时的运行都会探测全部源并把结果发布到
[`status.json`](https://lucky2049.com/status.json)(逐源:ok / 链尖 / 延迟 / 错误),
首页也渲染为「Sources (last refresh)」状态条——绿色即表示你的 Core RPC 在最近一次刷新时
正常应答。格式见 `docs/SCHEMA.md`。

---

## 重建 / 灾备

`index.json` 是系统的权威快照,建议定期备份(它在 `gh-pages` git 历史里,也可发个 Release)。链头已由
`anchor-head.yml` 每日经 OpenTimestamps 外锚到比特币链(见 `anchors/`),历史防篡改更稳。万一 `gh-pages`
丢失,两条重建路径:

```shell
# A) 有本地 DB 缓存时:stdlib sqlite3 直读,秒级重建整份快照
python scripts/export_static.py --out site --db data/database.db

# B) 没有 DB 时:从一份空 index.json 从创世开始从链上重算
#    (慢:每次运行在 RUN_BUDGET_S 后不再开始新的开奖,此时才写 index.json——所以用循环重复跑,
#    不要调大预算;每一轮都是一个存档点)
echo '{"count":0,"head":{},"algo_version":"v1","draws":[]}' > site/index.json
while MAX_NEW_DRAWS=500 python scripts/extend_pages.py site/index.json | tee /dev/stderr | grep -q 'run budget'; do :; done
```

`data/database.db` 是**可选本地缓存**(gitignored、不随仓库分发),只有路径 A 会读它;
cron 永远不碰 DB。缓存只新到你上次填充它的时候,所以走完路径 A 后,发布前再跑
`python scripts/extend_pages.py site/index.json`(重复到输出 `ADDED=0`),把缓存最后一期到链尖之间补齐。

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
