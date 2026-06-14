# 测试驱动开发指南 (TDD)

> 🌏 [English](../TDD.md) · **中文**

本项目为快速的 红 → 绿 → 重构 循环做了配置。

## 循环

1. **红** —— 为想要的行为先写测试;运行它;确认它因为**正确的原因**失败(断言失败,而非 import 错误)。
2. **绿** —— 写最少的代码让它通过。
3. **重构** —— 以测试为安全网清理代码;保持绿色。

让测试在每次保存时自动跑:

```shell
make install-dev    # 一次性:把 pytest 工具装进 venv(运行期纯标准库)
make watch          # 每次文件改动就重跑整套(TDD 循环)
```

其他命令:

```shell
make test           # 跑一次
make cov            # 带覆盖率报告(term-missing)
./.venv/bin/python -m pytest tests/test_spec_v1.py -v     # 单个文件
./.venv/bin/python -m pytest -k commitment                # 按关键字
python -m unittest discover -s tests                      # 不依赖 pytest 的后备
```

## 测试放哪

`tests/test_*.py`。普通的 `unittest.TestCase` 类和 pytest 风格的函数都能在 pytest 下运行,按需选用。

## 这套测试长什么样

一切都**只用标准库 + Node** —— 无数据库、无重依赖;唯一的 fixture 是 SPEC 第 0 期窗口(`tests/fixtures/draw0_hashes.json`),黄金向量锁与 JS 对拍锁都会读它。
站点是静态的,所以测试钉住两件最关键的事:

- **冻结的算法 + 承诺** —— `tests/test_spec_v1.py` 和 `tests/test_commitment.py` 里的黄金向量
  对着 `verify.py` 和 `SPEC.md` 复算。
- **浏览器内 JS** —— `tests/test_verify_js.py`、`tests/test_stats_js.py`、`tests/test_random_js.py`
  和 `tests/test_trend_js.py` 在 Node 里跑 `static/verify.js` / `static/stats.js` / `static/randomness.js` /
  `static/trend.js`,检查它们复现出 Python 的结果(及冻结的黄金值)。`tests/test_index_js.py` 与
  `tests/test_verify_fetch_js.py` 同样在 Node 里跑页面内联逻辑 —— 首页的下一期 ETA 窗口,以及 verify.html 的 144 哈希抓取/拼装 + 通过/失败裁决。Node 缺失时自动跳过。(页面结构 —— 共享导航、统计页措辞、首页去重 —— 由纯标准库的 `tests/test_pages.py` 守护。)

`tests/test_verify_site.py` 用 mock 掉的 HTTP 覆盖 `verify.py --site` 的链路(实时 API + 静态
`index.json` 回退)—— 不联网。

## 实例:stats.js 对拍(黄金锁定)

`tests/test_stats_js.py` 是跨实现锁定的模板:

1. 先把权威答案算一次(这里是开发期用 scipy 算的卡方),并**硬编码为黄金向量**写进测试。
2. 在 Node 里对一份确定性数据集跑 JS(`static/stats.js`),断言它与黄金值吻合 —— 同时一份独立的
   纯 Python 同公式参考实现也吻合。仓库里不再留 scipy 依赖。

和冻结算法同样的纪律:钉死已知正确的输出,然后任一实现的漂移都会让它变红。

## 锁住冻结的算法

开奖算法与承诺公式是**冻结的**(见 `SPEC.md`)。它们的黄金向量测试是护栏,不是 TODO:
若某个改动让它们变红,那是改动错了(或需要一个新的算法版本),不是测试错了。
