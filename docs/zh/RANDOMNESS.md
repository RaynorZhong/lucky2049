# 可验证随机数(抛硬币 & 掷骰子演示)

> 🌏 [English](../RANDOMNESS.md) · **中文**

开奖的一个**说明性**小配套——它**不属于**冻结的开奖算法([`SPEC.md`](../../SPEC.md)),也**不是游戏**
(无下注、无金钱、无赌注)。它演示:支撑抽奖的同一套"公开种子"机器,如何把**一个**不可篡改的比特币
区块哈希,变成任何人都能复现、且**无偏**的抛硬币与掷骰子结果。

浏览器内运行在 [`/randomness.html`](https://lucky2049.com/randomness.html);参考实现为
[`randomness.py`](../../randomness.py)(Python,纯标准库)与
[`static/randomness.js`](../../static/randomness.js)(浏览器内 JS),由 Node 对拍测试
([`tests/test_random_js.py`](../../tests/test_random_js.py))保证两者逐位一致。

## 算法

输入:一个比特币区块哈希 `H`——64 位小写十六进制字符串(自动规范化大小写/空白)。

```
seed         = SHA-256( ascii(H) )                              # 32 字节(哈希按 ASCII 处理,与开奖种子一致)
stream(d)    = HMAC-SHA-256( seed, ascii("d:0") )               # 32 字节
             ‖ HMAC-SHA-256( seed, ascii("d:1") ) ‖ …           # 拼接;域 d ∈ {"coin","dice"}
```

**抛硬币。** 第 `j` 次(从 0 计)= `stream("coin")` 的第 `j` 个比特,**每字节高位在前(MSB-first)**:
`bit = (stream[j // 8] >> (7 - (j mod 8))) & 1`;`1 → 正面(Heads)`,`0 → 反面(Tails)`。单个均匀比特
本身就完全公平(P = ½),无需拒绝采样。

**骰子(d6)。** 顺序读取 `stream("dice")` 的字节。对字节 `b`:若 `b < 252`,点数 = `b mod 6 + 1`;
否则(`b ∈ {252,…,255}`)**丢弃并读下一个字节**。因为 `252 = 6×42`,每个点数 1–6 恰好对应 42 个字节值,
所以是**无偏** d6(这步拒绝采样消除了直接 `b mod 6` 会引入的模偏差)。一般 `sides` 面骰子的丢弃阈值为
`256 − (256 mod sides)`。

两种输出都**前缀稳定**:要更多结果不会改变前面的;两个域相互独立(改硬币数量不影响骰子)。

## 测试向量

对**比特币创世区块**哈希
`000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f`:

| 输出 | 值 |
| --- | --- |
| `seed` | `09f663de96be771f50cab5ded00256ffe63773e2eaa9a604092951cc3d7c6621` |
| 前 24 次抛硬币 | `T H H H T T H T H H H H H H H H T T T T H H H H` |
| 前 16 次掷骰子 | `3 3 5 6 1 2 2 3 1 4 3 4 5 4 6 2` |

## 自己复现

```shell
python randomness.py        # 打印创世哈希的 seed、20 次抛硬币、12 次掷骰子
```

或在任意 JS 运行时(网页就是这么做的):

```js
const R = require('./static/randomness.js');     // 复用 verify.js 的 SHA-256/HMAC
R.coinFlips('000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f', 24);
R.diceRolls('000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f', 16);
```
