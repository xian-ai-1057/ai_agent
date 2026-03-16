# SQL Style Guide — 詳細範例

## 目錄
1. [撰寫模式：簡單查詢](#1-撰寫模式簡單查詢)
2. [撰寫模式：資料庫設計](#2-撰寫模式資料庫設計)
3. [撰寫模式：複雜報表查詢](#3-撰寫模式複雜報表查詢)
4. [撰寫模式：進階功能迭代](#4-撰寫模式進階功能迭代)
5. [分析模式：簡單查詢分析](#5-分析模式簡單查詢分析)
6. [分析模式：複雜查詢分析](#6-分析模式複雜查詢分析)
7. [分析模式：Oracle 特有語法分析](#7-分析模式oracle-特有語法分析)

---

## 1. 撰寫模式：簡單查詢（直接撰寫，不需 Planning）

**使用者：**「寫一個查詢找出所有訂單金額超過 1000 元的客戶」

**回應：**

這個查詢使用 JOIN 關聯客戶和訂單表，並以 HAVING 篩選符合條件的客戶。

```sql
/* Find customers with total order amount exceeding 1000 */
SELECT c.customer_id,
       c.customer_name,
       SUM(o.order_total) AS total_amount
  FROM customer c
  JOIN order_header o
    ON c.customer_id = o.customer_id
 GROUP BY c.customer_id,
          c.customer_name
HAVING SUM(o.order_total) > 1000
 ORDER BY total_amount DESC;
```

**使用注意：** 此查詢假設訂單表名為 `order_header`，如表名不同請適當調整。

---
**可選的進階功能：**
1. **時間範圍篩選** — 加入訂單日期範圍條件
2. **分頁支援** — 加入 `FETCH FIRST n ROWS ONLY` 支援大量資料
3. **客戶詳細資訊** — 加入更多客戶欄位如聯絡方式

如需實作請告知。

---

## 2. 撰寫模式：中等複雜度（先 Planning，確認後撰寫）

**使用者：**「設計一個簡單的部落格系統資料庫」

**回應（Planning 階段）：**

## 📋 開發計畫

### 1. 需求確認
你需要一個部落格系統的資料庫，核心功能包含：
- 使用者註冊和管理
- 使用者可以發表文章
- 其他使用者可以在文章下方留言

### 2. 設計方案
- **涉及的資料表**：
  - `user_account`：儲存使用者資訊（避免使用 Oracle 保留字 `USER`）
  - `blog_post`：儲存文章內容和狀態
  - `comment`：儲存留言，關聯到文章和留言者
- **關聯策略**：
  - `blog_post` → `user_account`：多對一（INNER JOIN，每篇文章必有作者）
  - `comment` → `blog_post`：多對一（INNER JOIN，每則留言必屬於一篇文章）
  - `comment` → `user_account`：多對一（INNER JOIN，每則留言必有留言者）
- **演算法選擇**：使用 `GENERATED AS IDENTITY`（Oracle 12c+）自動產生主鍵，`ON DELETE CASCADE` 處理關聯刪除

### 3. 欄位規劃
| 表名 | 欄位名 | 資料型別 | 說明 |
|------|--------|---------|------|
| user_account | user_id | NUMBER(10) IDENTITY | 主鍵 |
| user_account | user_name | VARCHAR2(50) | 使用者名稱 |
| user_account | email_addr | VARCHAR2(255) | 電子郵件（UNIQUE） |
| user_account | created_date | TIMESTAMP | 建立時間 |
| blog_post | post_id | NUMBER(10) IDENTITY | 主鍵 |
| blog_post | author_id | NUMBER(10) | FK → user_account |
| blog_post | title | VARCHAR2(200) | 文章標題 |
| blog_post | content | CLOB | 文章內容 |
| blog_post | post_status | VARCHAR2(20) | draft/published/archived |
| blog_post | created_date | TIMESTAMP | 建立時間 |
| blog_post | updated_date | TIMESTAMP | 最後更新時間 |
| comment | comment_id | NUMBER(10) IDENTITY | 主鍵 |
| comment | post_id | NUMBER(10) | FK → blog_post |
| comment | commenter_id | NUMBER(10) | FK → user_account |
| comment | content | VARCHAR2(1000) | 留言內容 |
| comment | created_date | TIMESTAMP | 建立時間 |

### 4. 效能考量
- 在 `blog_post.author_id`、`blog_post.post_status`、`comment.post_id` 建立索引
- 文章內容使用 `CLOB`，查詢時避免在 `content` 上做全文搜尋（如需要可另建全文索引）
- 預估資料量不大的情況下，此設計足夠應對

請確認以上計畫是否符合需求，確認後我會開始撰寫 SQL。

---

**使用者：**「確認，請開始」

**回應（撰寫階段）：**

設計三個核心表：`user_account`（使用者）、`blog_post`（文章）和 `comment`（留言）。

```sql
/* ============================================
   Blog system core tables (Oracle 12c+)
   ============================================ */

/* User account table */
CREATE TABLE user_account (
    PRIMARY KEY (user_id),
    user_id        NUMBER(10)    GENERATED AS IDENTITY,
    user_name      VARCHAR2(50)  NOT NULL,
    email_addr     VARCHAR2(255) NOT NULL,
    created_date   TIMESTAMP     DEFAULT CURRENT_TIMESTAMP NOT NULL,
                   CONSTRAINT uq_user_account_email
                   UNIQUE (email_addr)
);

/* Blog post table */
CREATE TABLE blog_post (
    PRIMARY KEY (post_id),
    post_id        NUMBER(10)    GENERATED AS IDENTITY,
    author_id      NUMBER(10)    NOT NULL,
    title          VARCHAR2(200) NOT NULL,
    content        CLOB          NOT NULL,
    post_status    VARCHAR2(20)  DEFAULT 'draft' NOT NULL,
    created_date   TIMESTAMP     DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_date   TIMESTAMP,
                   CONSTRAINT fk_blog_post_author
                   FOREIGN KEY (author_id)
                   REFERENCES user_account (user_id)
                   ON DELETE CASCADE,
                   CONSTRAINT chk_blog_post_status
                   CHECK (post_status IN ('draft', 'published', 'archived'))
);

/* Comment table */
CREATE TABLE comment (
    PRIMARY KEY (comment_id),
    comment_id     NUMBER(10)    GENERATED AS IDENTITY,
    post_id        NUMBER(10)    NOT NULL,
    commenter_id   NUMBER(10)    NOT NULL,
    content        VARCHAR2(1000) NOT NULL,
    created_date   TIMESTAMP     DEFAULT CURRENT_TIMESTAMP NOT NULL,
                   CONSTRAINT fk_comment_post
                   FOREIGN KEY (post_id)
                   REFERENCES blog_post (post_id)
                   ON DELETE CASCADE,
                   CONSTRAINT fk_comment_user
                   FOREIGN KEY (commenter_id)
                   REFERENCES user_account (user_id)
                   ON DELETE CASCADE
);

/* Basic indexes for common queries */
CREATE INDEX idx_blog_post_author
    ON blog_post (author_id);

CREATE INDEX idx_blog_post_status
    ON blog_post (post_status);

CREATE INDEX idx_comment_post
    ON comment (post_id);
```

**設計考量：**
- 使用 `user_account` 避免與 Oracle 保留字 `USER` 衝突
- 使用 `GENERATED AS IDENTITY`（Oracle 12c+）自動產生主鍵
- 文章狀態使用 `CHECK` 約束確保資料有效性
- 外鍵使用 `ON DELETE CASCADE` 自動處理關聯資料刪除

---

## 3. 撰寫模式：複雜報表查詢

**使用者：**「月度銷售報表，含銷售額、訂單數和平均訂單金額」

**回應：**

```sql
/* ============================================
   Monthly sales report
   Includes: revenue, order count, avg order value
   ============================================ */
SELECT EXTRACT(YEAR FROM o.order_date) AS sale_year,
       EXTRACT(MONTH FROM o.order_date) AS sale_month,
       COUNT(o.order_id) AS order_count,
       SUM(o.order_total) AS total_revenue,
       ROUND(AVG(o.order_total), 2) AS average_order_value
  FROM order_header o
 WHERE o.order_status = 'completed'
 GROUP BY EXTRACT(YEAR FROM o.order_date),
          EXTRACT(MONTH FROM o.order_date)
 ORDER BY sale_year DESC,
          sale_month DESC;
```

---

## 4. 撰寫模式：進階功能迭代

**使用者：**「幫我加上同期比較和累計銷售」

**回應：**

在原有報表基礎上加入 YoY 比較和 YTD 累計。

```sql
/* ============================================
   Monthly sales report with YoY and YTD
   ============================================ */
WITH monthly_sales AS (
    SELECT EXTRACT(YEAR FROM o.order_date) AS sale_year,
           EXTRACT(MONTH FROM o.order_date) AS sale_month,
           COUNT(o.order_id) AS order_count,
           SUM(o.order_total) AS total_revenue,
           ROUND(AVG(o.order_total), 2) AS average_order_value
      FROM order_header o
     WHERE o.order_status = 'completed'
     GROUP BY EXTRACT(YEAR FROM o.order_date),
              EXTRACT(MONTH FROM o.order_date)
)
SELECT ms.sale_year,
       ms.sale_month,
       ms.order_count,
       ms.total_revenue,
       ms.average_order_value,
       /* Year-over-year comparison */
       prev.total_revenue AS prev_year_revenue,
       CASE
           WHEN prev.total_revenue IS NULL
                OR prev.total_revenue = 0
           THEN NULL
           ELSE ROUND(
               (ms.total_revenue - prev.total_revenue)
               / prev.total_revenue * 100,
               2
           )
       END AS yoy_growth_pct,
       /* Year-to-date cumulative revenue */
       SUM(ms.total_revenue) OVER (
           PARTITION BY ms.sale_year
           ORDER BY ms.sale_month
       ) AS ytd_revenue
  FROM monthly_sales ms
  LEFT JOIN monthly_sales prev
    ON ms.sale_year = prev.sale_year + 1
   AND ms.sale_month = prev.sale_month
 ORDER BY ms.sale_year DESC,
          ms.sale_month DESC;
```

---

## 5. 分析模式：簡單查詢分析

**使用者貼上：**
```sql
SELECT d.department_name, COUNT(e.employee_id) cnt,
AVG(e.salary) avg_sal FROM departments d
LEFT JOIN employees e ON d.department_id = e.department_id
WHERE e.hire_date >= TO_DATE('2020-01-01','YYYY-MM-DD')
GROUP BY d.department_name HAVING COUNT(e.employee_id) > 5
ORDER BY avg_sal DESC;
```

**回應：**

### 總覽
這段查詢統計 2020 年以後入職、且人數超過 5 人的各部門員工數和平均薪資。

### 逐段拆解
- **FROM / JOIN**：以 `departments` 為主表，LEFT JOIN `employees`
- **WHERE**：篩選 2020-01-01 以後入職的員工（使用 Oracle 的 `TO_DATE`）
- **GROUP BY**：按部門名稱分組
- **HAVING**：只保留員工數 > 5 的部門
- **ORDER BY**：按平均薪資由高到低排序

### 潛在問題
1. **LEFT JOIN 與 WHERE 衝突**：`WHERE e.hire_date >= ...` 會過濾掉 `employees` 為 NULL 的列，導致 LEFT JOIN 實際等同於 INNER JOIN。如果目的是保留無員工的部門，條件應移至 `ON` 子句
2. **別名可讀性**：`cnt` 和 `avg_sal` 建議改為更明確的名稱

### 改善建議
```sql
/* Department headcount and avg salary since 2020 */
SELECT d.department_name,
       COUNT(e.employee_id) AS employee_count,
       ROUND(AVG(e.salary), 2) AS average_salary
  FROM departments d
  JOIN employees e
    ON d.department_id = e.department_id
 WHERE e.hire_date >= TO_DATE('2020-01-01', 'YYYY-MM-DD')
 GROUP BY d.department_name
HAVING COUNT(e.employee_id) > 5
 ORDER BY average_salary DESC;
```

改善項目：LEFT JOIN 改為 JOIN（反映實際邏輯）、別名語義化、River 風格對齊。

---

## 6. 分析模式：複雜查詢分析

**使用者貼上含有多層 CTE 和 Window Function 的查詢時**，應：

1. 先列出所有 CTE 的名稱和各自的用途
2. 逐個 CTE 拆解邏輯
3. 說明最終 SELECT 如何組合各 CTE 的結果
4. 用文字描述資料流：`table_a → CTE_1（篩選 + 聚合）→ CTE_2（加入排名）→ 最終結果`
5. 指出 Window Function 的分區和排序邏輯

---

## 7. 分析模式：Oracle 特有語法分析

當遇到 Oracle 特有語法時，額外說明：

**階層查詢（CONNECT BY）**：
- 說明 `START WITH` 的起始條件
- 說明 `CONNECT BY PRIOR` 的父子關係方向
- 說明 `LEVEL` 偽欄位的含義
- 提供標準 SQL 的遞迴 CTE 替代方案

**MERGE 語句**：
- 拆解 USING、ON、WHEN MATCHED、WHEN NOT MATCHED 各區塊
- 說明與 INSERT + UPDATE 分開寫的差異

**分析函式**：
- 明確說明 `PARTITION BY` 和 `ORDER BY` 的分組與排序邏輯
- 說明 Window Frame（`ROWS BETWEEN`）的範圍效果
