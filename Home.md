# 預測總覽

這是 Grok Finance 預測 vault 的首頁。每筆預測是相對基準（台股對 **0050**、美股對 **QQQ** 或 SOX）的表現，不是絕對漲跌。

- **超額報酬（excess return）** = 個股報酬 − 基準報酬
- **命中／未中** 看超額報酬的正負號是否與預測方向一致（`up` = 表現贏基準，`down` = 表現輸基準）
- 若 |excess| < 0.5%，計為 **持平（flat）**
- 期限以**交易日**計算

開啟本頁的 Dataview 查詢前，請先安裝並啟用 [Dataview](https://github.com/blacksmithgu/obsidian-dataview) 外掛。頁尾的命中率百分比區塊還需要在 Dataview 設定中打開 **Enable JavaScript Queries**。

---

## 未到期（open）

```dataview
TABLE
  ticker AS 代號,
  name AS 名稱,
  direction AS 方向,
  confidence AS 信心,
  horizon AS 天數,
  due_date AS 到期,
  ref_price AS 參考價,
  benchmark AS 基準,
  reasoning_type AS 推論類型
FROM "Predictions"
WHERE status = "open"
SORT due_date ASC, id ASC
```

---

## 已計分（scored）

```dataview
TABLE
  ticker AS 代號,
  name AS 名稱,
  direction AS 方向,
  confidence AS 信心,
  result AS 結果,
  stock_return AS 個股報酬,
  excess_return AS 超額報酬,
  due_date AS 到期,
  scored_at AS 計分時間
FROM "Predictions"
WHERE status = "scored"
SORT scored_at DESC, id DESC
```

---

## 命中率總覽

依結果分組，`length(rows)` 為該組筆數。

```dataview
TABLE WITHOUT ID
  result AS 結果,
  length(rows) AS 筆數
FROM "Predictions"
WHERE status = "scored"
GROUP BY result
SORT result ASC
```

---

## 依信心分組

```dataview
TABLE WITHOUT ID
  confidence AS 信心,
  length(rows) AS 筆數,
  length(filter(rows, (r) => r.result = "hit")) AS 命中,
  length(filter(rows, (r) => r.result = "miss")) AS 未中,
  length(filter(rows, (r) => r.result = "flat")) AS 持平
FROM "Predictions"
WHERE status = "scored"
GROUP BY confidence
```

---

## 依推論類型分組

```dataview
TABLE WITHOUT ID
  reasoning_type AS 推論類型,
  length(rows) AS 筆數,
  length(filter(rows, (r) => r.result = "hit")) AS 命中,
  length(filter(rows, (r) => r.result = "miss")) AS 未中,
  length(filter(rows, (r) => r.result = "flat")) AS 持平
FROM "Predictions"
WHERE status = "scored"
GROUP BY reasoning_type
SORT reasoning_type ASC
```

---

## 依市場分組

```dataview
TABLE WITHOUT ID
  market AS 市場,
  length(rows) AS 筆數,
  length(filter(rows, (r) => r.result = "hit")) AS 命中,
  length(filter(rows, (r) => r.result = "miss")) AS 未中,
  length(filter(rows, (r) => r.result = "flat")) AS 持平
FROM "Predictions"
WHERE status = "scored"
GROUP BY market
SORT market ASC
```

---

## 近期未中（miss）

```dataview
TABLE
  ticker AS 代號,
  name AS 名稱,
  confidence AS 信心,
  reasoning_type AS 推論類型,
  excess_return AS 超額報酬,
  due_date AS 到期,
  scored_at AS 計分時間
FROM "Predictions"
WHERE status = "scored" AND result = "miss"
SORT scored_at DESC, id DESC
```

---

## 依信心的命中率（排除持平）

此區塊用 DataviewJS 計算命中率百分比，樣本不含 `flat`。需啟用 Dataview 的 JavaScript Queries。

```dataviewjs
const pages = dv.pages('"Predictions"')
  .where(p => p.status == "scored" && p.result && p.result != "flat");

const byConf = new Map();
for (const p of pages) {
  const c = String(p.confidence || "(none)");
  if (!byConf.has(c)) byConf.set(c, { hit: 0, total: 0 });
  const g = byConf.get(c);
  g.total += 1;
  if (p.result == "hit") g.hit += 1;
}

const order = ["high", "medium-high", "medium", "low-medium", "low"];
const rows = [];
let allHit = 0;
let allTotal = 0;
const keys = Array.from(byConf.keys()).sort((a, b) => {
  const ia = order.indexOf(a);
  const ib = order.indexOf(b);
  return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
});
for (const c of keys) {
  const g = byConf.get(c);
  allHit += g.hit;
  allTotal += g.total;
  const pct = g.total ? (100 * g.hit / g.total).toFixed(1) + "%" : "—";
  rows.push([c, g.hit, g.total, pct]);
}
if (allTotal) {
  rows.push(["全部", allHit, allTotal, (100 * allHit / allTotal).toFixed(1) + "%"]);
}

dv.table(["信心", "命中", "樣本（排除持平）", "命中率"], rows);
```
