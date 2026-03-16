# Oracle 私銀大表開發決策紀錄

> 本文件記錄私銀大表（`PB_MART_CUST_MONTH`、`PB_MART_CUST_PROD_MONTH`）開發過程中確立的
> Oracle 技術限制對應解法與業務邏輯決策，供後續開發同類報表時參考。

---

## 一、Oracle 技術限制與對應解法

### 1. LAG() 不可巢狀

Oracle 不支援 `LAG(LAG(...))` 語法，執行時會報錯。

**解法：多層 CTE 物化前期值**

將所有 `LAG()` 呼叫集中在同一個 CTE 層，把結果存成獨立欄位，下一層再使用這些欄位做運算，全程不出現巢狀 `LAG()`。

```sql
-- ✗ 錯誤：LAG 巢狀，Oracle 不支援
CASE WHEN LAG(LAG(aum_bal_eom, 1) OVER (...), 1) OVER (...) ...

-- ✓ 正確：在 lag_base CTE 一次物化所有前期值
lag_base AS (
    SELECT cust_id,
           stat_month,
           aum_bal_eom,
           LAG(aum_bal_eom, 1)  OVER (PARTITION BY cust_id ORDER BY stat_month) AS prev_1m_aum,
           LAG(aum_bal_eom, 2)  OVER (PARTITION BY cust_id ORDER BY stat_month) AS prev_2m_aum,
           LAG(aum_bal_eom, 12) OVER (PARTITION BY cust_id ORDER BY stat_month) AS prev_12m_aum
      FROM monthly_base
),

-- 下一層直接用欄位名稱，不再呼叫 LAG()
calc_base AS (
    SELECT aum_bal_eom - NVL(prev_1m_aum, 0) AS aum_mom_chg
      FROM lag_base
)
```

---

### 2. NET_FLOW_MOM_CHG 的巢狀 LAG 替代方案

「本月淨流入月增減」在概念上等於「本月淨流入 − 上月淨流入」，直覺寫法需要巢狀 LAG，但 Oracle 不允許。

**解法：代數展開，只用 LAG(1) 和 LAG(2)**

```
NET_FLOW_M(t)    = AUM(t)   - AUM(t-1)
NET_FLOW_M(t-1)  = AUM(t-1) - AUM(t-2)

NET_FLOW_MOM_CHG = NET_FLOW_M(t) - NET_FLOW_M(t-1)
                 = AUM(t) - 2 × AUM(t-1) + AUM(t-2)
```

```sql
-- 在 lag_base 層取出 prev_1m_aum 和 prev_2m_aum
-- 在 calc_base 層直接代入公式，不需巢狀
l.aum_bal_eom
- 2 * NVL(l.prev_1m_aum, 0)
+ NVL(l.prev_2m_aum, 0)   AS net_flow_mom_chg
```

---

### 3. CTAS 不支援純 NULL 欄位

`CREATE TABLE AS SELECT NULL AS col_name` 在 SQL Developer 中會因為無法推斷欄位型別而報錯。

**三種處理方式與取捨：**

| 方案 | 語法 | 優點 | 缺點 |
|---|---|---|---|
| **A. 刪除欄位** | 直接不寫 | 最乾淨 | 日後需 `ALTER TABLE ADD` |
| **B. CAST 佔位** | `CAST(NULL AS NUMBER)` | 保留欄位結構與型別 | 需知道每欄目標型別 |
| **C. 預設值** | `0` 或 `'N/A'` | 無 NULL | 語意上「無資料」與「0」容易混淆 |

**本專案採用方案 A**：暫無資料來源的欄位直接刪除，日後補回語法如下：

```sql
ALTER TABLE pb_mart_cust_month ADD (commission_m NUMBER);
-- 取得來源後再 UPDATE
UPDATE pb_mart_cust_month SET commission_m = ... WHERE ...;
```

本次刪除的欄位（待日後補回）：

| 表 | 欄位 | 原因 |
|---|---|---|
| `PB_MART_CUST_MONTH` | `SEGMENT` | 無客戶分層來源表 |
| `PB_MART_CUST_MONTH` | `ASOF_DATE_MAX` | 無資料切點欄位 |
| `PB_MART_CUST_MONTH` | `COMMISSION_M` | 無佣金來源表 |
| `PB_MART_CUST_MONTH` | `NEW_MONEY_M` | 無逐筆資金流水來源 |
| `PB_MART_CUST_MONTH` | `OUT_FLOW_M` | 無逐筆資金流水來源 |
| `PB_MART_CUST_MONTH` | `COMM_TO_AUM` | 無佣金來源表 |
| `PB_MART_CUST_MONTH` | `COMM_SHARE_REV` | 無佣金來源表 |
| `PB_MART_CUST_PROD_MONTH` | `COMMISSION_PT` | 無佣金來源表 |

---

### 4. Window Function 不可在同一 SELECT 中自我參照

同一 SELECT 區塊內，不能用剛算出的 Window 結果再做另一個 Window 運算。

**錯誤範例：**

```sql
-- ✗ aum_mix_pct 和 conc_hhi_aum 在同一 SELECT，Oracle 不允許
SELECT aum_bal_eom_pt / SUM(aum_bal_eom_pt) OVER (...) AS aum_mix_pct,
       SUM(POWER(aum_mix_pct, 2)) OVER (...)           AS conc_hhi_aum  -- 參照同層的 aum_mix_pct，報錯
  FROM ...
```

**解法：強制拆層**

```sql
-- Layer 4：計算 aum_mix_pct
prod_mix AS (
    SELECT aum_bal_eom_pt / SUM(aum_bal_eom_pt) OVER (...) AS aum_mix_pct
      FROM prod_with_market
),

-- Layer 5：用上一層結果計算 HHI（此時 aum_mix_pct 已完全物化）
prod_hhi AS (
    SELECT SUM(POWER(NVL(aum_mix_pct, 0), 2)) OVER (...) AS conc_hhi_aum
      FROM prod_mix
)
```

---

### 5. YYYYMM 格式取年份

當日期欄位為 `NUMBER` 型態、格式為 YYYYMM（如 `202603`）時，取出 YYYY 年份：

```sql
-- ✓ 用除法，不需型別轉換
TRUNC(stat_month / 100)          -- 202603 / 100 = 2026.03 → TRUNC = 2026

-- 可用於 YTD 的 PARTITION BY 年份切分
SUM(total_revenue_m)
    OVER (PARTITION BY cust_id,
                       TRUNC(stat_month / 100)   -- 按年份分組
              ORDER BY stat_month
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS rev_ytd_sum

-- 取上一年 12 月（年底快照）的 JOIN KEY 公式
-- 例：stat_month=202603 → 2026*100-88 = 202512（2025年12月）
TRUNC(stat_month / 100) * 100 - 88   AS prior_dec_yyyymm
```

---

## 二、業務邏輯決策

### 6. MOM / YOY CHG 的 NULL 補 0 規則

| 欄位類型 | 前期無資料時 | 理由 |
|---|---|---|
| `_CHG` 欄位（差額） | `NVL(prev, 0)`，回傳當期值 | 確保每個月都有數值，方便下游彙總 |
| `_PCT` 欄位（增減率） | 維持 `NULL` | 分母為 0 時無意義，強制補值反而誤導 |

```sql
-- CHG：補 0，永遠有值
l.aum_bal_eom - NVL(l.prev_1m_aum, 0)   AS aum_mom_chg

-- PCT：分母為 0 維持 NULL
CASE WHEN NVL(l.prev_1m_aum, 0) = 0
     THEN NULL
     ELSE (l.aum_bal_eom - l.prev_1m_aum) / ABS(l.prev_1m_aum)
END                                       AS aum_mom_pct
```

---

### 7. 利息收入的識別方式

不用 `PROD_CD` 白名單，改用 `SUB_PROD_CD LIKE '%利%'` 識別利息類收入。

**優點：** 未來新增「OBU 存款利收」、「外幣放款利收」等細類時，自動涵蓋，不需維護白名單。

```sql
-- ✗ 舊方式：PROD_CD 白名單，新產品別需手動維護
CASE WHEN prod_cd = '銀行' THEN twd_amt ELSE 0 END

-- ✓ 新方式：SUB_PROD_CD 含「利」字即為利息
SUM(CASE WHEN sub_prod_cd LIKE '%利%'
         THEN NVL(twd_amt, 0) ELSE 0 END)   AS interest_income_m,
SUM(CASE WHEN sub_prod_cd NOT LIKE '%利%'
         THEN NVL(twd_amt, 0) ELSE 0 END)   AS fee_income_m
```

---

### 8. FEE / INTEREST / TOTAL REVENUE 的加總關係

三個收入欄位為**互斥且完整**的關係：

```
INTEREST_INCOME_M = SUM( twd_amt WHERE sub_prod_cd LIKE '%利%' )
FEE_INCOME_M      = SUM( twd_amt WHERE sub_prod_cd NOT LIKE '%利%' )
TOTAL_REVENUE_M   = FEE_INCOME_M + INTEREST_INCOME_M  ← 等於 SUM(ALL)，無重複計算
```

> ⚠️ 注意：若 `FEE_INCOME_M` 定義改為 `SUM(ALL)`（包含利息），則 `TOTAL_REVENUE_M` 不可再加 `INTEREST_INCOME_M`，否則銀行利息被雙重計算。兩者定義必須保持互斥。

---

### 9. DENSE_RANK 取代 RANK 用於客戶排名

排名類欄位一律使用 `DENSE_RANK()`，避免相同值時產生跳號。

```sql
-- ✗ RANK()：相同值跳號（1, 1, 3, 4...）
RANK() OVER (PARTITION BY market, stat_month ORDER BY aum_bal_eom DESC)

-- ✓ DENSE_RANK()：相同值不跳號（1, 1, 2, 3...）
DENSE_RANK() OVER (PARTITION BY market, stat_month ORDER BY aum_bal_eom DESC) AS cust_aum_rank_mkt
```

例外：商品類別在客戶內的 AUM 排名（`PRODUCT_TYPE_RANK`）視業務需求決定，本次維持 `RANK()`。

---

### 10. 數值欄位不做四捨五入

移除所有 `ROUND()`，保留完整精度。

**理由：** 極小值（如 `0.03`）在 `ROUND(..., 0)` 後會被無聲捨去為 `0`，導致彙總計算失真，且下游查詢通常會自行決定顯示位數。

```sql
-- ✗ 有捨入風險
ROUND(fee_income_m / total_revenue_m, 4)   AS fee_share_rev

-- ✓ 保留完整精度
fee_income_m / total_revenue_m             AS fee_share_rev
```
