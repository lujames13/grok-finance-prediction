# Grok Finance Predictions

James 的 Obsidian vault：每筆股票預測一張筆記，用來追蹤推論、數值依據，以及到期後相對基準的超額報酬（excess return）。

在 Obsidian 中選擇「Open folder as vault」，指向本 repo 根目錄，並把 [Home.md](Home.md) 設為首頁（Core plugin「Web viewer」不是必要的；可用 Homepage 外掛，或直接釘選 Home）。

## 必要外掛

1. 安裝並啟用 [Dataview](https://github.com/blacksmithgu/obsidian-dataview)。
2. 打開 **Settings → Dataview → Enable JavaScript Queries**。  
   `Home.md` 頁尾「依信心的命中率」用的是 `dataviewjs`，沒開這項會空白或報錯。一般的 `dataview` 表格不需要 JS。

不需要把 `.obsidian/` 的工作區檔提交進 git（見 `.gitignore`）。社群外掛清單請在你自己的 Obsidian 裡安裝，不要把設定檔推進這個 repo。

## 目錄結構

```
Home.md                     # Dataview 首頁：未到期、已計分、命中率
README.md
Templates/Prediction.md     # 新預測筆記範本
Predictions/                # 一筆預測一張筆記
  <id> <ticker> <name>.md
data/
  predictions.csv           # 預測總表（資料來源）
  notes/<id>.json           # 可選 sidecar（新聞連結、檢討）
scripts/
  sync_vault.py             # 把 CSV 同步成筆記，並可 commit / push
  fetch_prices.py           # 從 Yahoo Finance 拉近期收盤價
  prediction_i18n.json      # 既有 23 筆的繁中正文
pyproject.toml              # uv 專案與依賴
uv.lock
.python-version             # 固定 Python 3.13
.gitignore
```

筆記檔名範例：`20261119-PM-2 3017.TW 奇鋐.md`。`data/` 與 `scripts/` 可以留在 vault 裡，不會當成筆記。工作區檔與 Python 虛擬環境已寫進 `.gitignore`（`.obsidian/workspace*.json`、`.trash/`、`.venv/`、`__pycache__/`）。

## 開發環境（uv）

套件與 Python 版本用 [uv](https://docs.astral.sh/uv/) 管理，不要用系統 pip。

```bash
# 若尚未安裝 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

cd /workspace/grok-finance-prediction
uv sync                          # 依 uv.lock 建立 .venv 並安裝依賴（目前是 PyYAML）
uv run scripts/sync_vault.py --help
uv run scripts/fetch_prices.py --help
```

`.python-version` 固定為 3.13。新增依賴請用 `uv add <package>`，再把 `pyproject.toml` 與 `uv.lock` 一起提交。

## 計分規則

- 方向是**相對基準**：`up` = 表現贏基準（outperform），`down` = 表現輸基準（underperform）。
- 台股基準通常是 0050，美股通常是 QQQ。
- **excess return** = 個股報酬 − 基準報酬。
- 正負號與預測方向一致為 **hit**，相反為 **miss**。若 `|excess| < 0.5%`，計為 **flat**。
- 期限以交易日計算。

## Frontmatter 欄位

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | text | 例如 `20261119-PM-2` |
| `ticker` | text | 例如 `3017.TW`、`NVDA` |
| `name` | text | 中文或官方名稱 |
| `market` | text | `TW` 或 `US` |
| `direction` | text | `up` 或 `down` |
| `confidence` | text | `low` / `low-medium` / `medium` / `medium-high` / `high` |
| `horizon` | number | 交易日天數 |
| `made_at` | date | `YYYY-MM-DD` |
| `session` | text | `AM` 或 `PM` |
| `due_date` | date | `YYYY-MM-DD` |
| `ref_price` | number | 進場／參考價 |
| `benchmark` | text | 例如 `0050`、`QQQ` |
| `benchmark_ref` | number | 基準參考價 |
| `reasoning_type` | text | kebab-case，例如 `counterparty`、`supply-chain`、`mean-reversion`、`dilution`、`beta-direction`、`rotation`、`policy`、`pair-trade` |
| `status` | text | `open` 或 `scored` |
| `result` | text | `hit` / `miss` / `flat`；未計分為空 |
| `exit_price` | number | 未計分為空 |
| `benchmark_exit` | number | 未計分為空 |
| `stock_return` | number | 個股報酬（百分比數值，例如 `-1.74` 代表 −1.74%）；未計分為空 |
| `excess_return` | number | 超額報酬（百分比數值）；未計分為空 |
| `scored_at` | text | 計分時間；未計分為空 |
| `tags` | list | 見下 |

### tags

- `prediction`
- `conf/<confidence>`，例如 `conf/low`
- `pred/<open\|hit\|miss\|flat>`
- `market/<tw\|us>`
- `dir/<up\|down>`
- `reason/<reasoning_type>`，例如 `reason/mean-reversion`

這些欄位同時放在 YAML properties 與 tags，方便 Dataview 篩選，也方便在側邊欄點 tag。

## 筆記本文

每張筆記固定六個標題（繁中，專有名詞保留英文）：

1. `## 預測`：一句話摘要
2. `## 數值依據`：參考價、基準價、時間戳
3. `## 推論理由`：建立時的 thesis
4. `## 觸發新聞`：trigger news；CSV 或 sidecar 沒有 URL 時會註明「（原始紀錄未保存連結）」
5. `## 推論失效條件`
6. `## 結果與檢討`：已計分則寫報酬與結果；未到期則留占位句

## 同步腳本

標準指令（請日常 routine 改用這組）：

```bash
cd /workspace/grok-finance-prediction

# 寫入筆記；有變更就 commit，但不 push
uv run scripts/sync_vault.py --no-push

# 有變更就 commit，並 push 到 origin/main
# 需要環境變數 GH_FINANCE_PAT（fine-grained PAT）
uv run scripts/sync_vault.py
```

可選參數：`--csv PATH`、`--vault PATH`、`--sidecar-dir PATH`。預設讀 `data/predictions.csv` 與 `data/notes/`，vault 就是這個 repo 根目錄。

腳本是冪等的（idempotent）：連續跑兩次，第二次不應再改檔。Commit message 格式：`predictions: sync YYYY-MM-DD HH:MM`（Asia/Taipei）。

相容路徑：`python3 /workspace/finance/sync_vault.py` 仍可用，它是一個薄包裝，會轉去執行上面的 `uv run` 指令。`/workspace/finance/predictions.csv` 與 `/workspace/finance/notes` 是指向 repo 裡 `data/` 的符號連結。

查價：

```bash
cd /workspace/grok-finance-prediction
uv run scripts/fetch_prices.py                 # 預設觀察清單
uv run scripts/fetch_prices.py --from-csv      # 加上 predictions.csv 裡的代號與基準
uv run scripts/fetch_prices.py NVDA 2330.TW
```

### Sidecar JSON

若要替某筆預測補新聞連結、額外數字或計分檢討，放一個檔在：

`data/notes/<id>.json`

```json
{
  "sources": [
    {"title": "來源標題", "url": "https://example.com/story"}
  ],
  "numbers": "可選：附加在「數值依據」後方的文字或清單",
  "lesson": "可選：附加在「結果與檢討」後方的檢討文字"
}
```

- `sources`：寫入「觸發新聞」的 Markdown 連結。不要發明連結；只放你確認過的 URL。
- `numbers`：附加在「數值依據」。
- `lesson`：附加在「結果與檢討」。

新建筆記時會套用 sidecar。之後再跑同步時，`lesson` 會跟著更新「結果與檢討」；`sources` / `numbers` 不會覆寫你已在 Obsidian 裡改過的那兩段（以免蓋掉後來補的連結）。

## Dataview 查詢範例

未到期、依到期日排序：

```dataview
TABLE ticker, name, direction, confidence, due_date, reasoning_type
FROM "Predictions"
WHERE status = "open"
SORT due_date ASC
```

只看低信心且已命中：

```dataview
TABLE ticker, name, excess_return, scored_at
FROM "Predictions"
WHERE status = "scored" AND confidence = "low" AND result = "hit"
SORT scored_at DESC
```

某類推論的超額報酬：

```dataview
TABLE ticker, name, result, excess_return
FROM "Predictions"
WHERE reasoning_type = "mean-reversion"
SORT due_date DESC
```

用 tag 篩選也可以，例如：

```dataview
TABLE ticker, name, result, excess_return
FROM #pred/miss AND #conf/low
```
