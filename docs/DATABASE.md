# 数据库表结构

启用数据库（`DB_TYPE=sqlite` 或 `mysql`）后，程序自动创建以下 5 张表。

> 所有表均包含 `user_id` 和 `user_name`（自动从网站获取）字段，`user_name` 会在每次更新时自动补充。

---

## `users` — 用户户号信息

| 字段 | 类型 | 说明 |
|------|------|------|
| user_id | TEXT | 用户户号（主键） |
| phone_number | TEXT | 登录手机号 |
| user_name | TEXT | 用户名（自动从网站获取） |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

---

## `daily_usage` — 每日用电量（含分时）

主键：`(user_id, date)`

| 字段 | 类型 | 说明 |
|------|------|------|
| user_id | TEXT | 用户户号 |
| user_name | TEXT | 用户名 |
| date | TEXT | 日期（YYYY-MM-DD） |
| total_usage | REAL | 总用电量（kWh） |
| valley_usage | REAL | 谷时用电量（kWh） |
| flat_usage | REAL | 平时用电量（kWh） |
| peak_usage | REAL | 峰时用电量（kWh） |
| tip_usage | REAL | 尖时用电量（kWh） |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

---

## `monthly_usage` — 月度用电量（含分时和电费）

主键：`(user_id, month)`

| 字段 | 类型 | 说明 |
|------|------|------|
| user_id | TEXT | 用户户号 |
| user_name | TEXT | 用户名 |
| month | TEXT | 月份（YYYY-MM） |
| total_usage | REAL | 总用电量（kWh） |
| total_charge | REAL | 总电费（CNY） |
| valley_usage | REAL | 谷时用电量（kWh） |
| flat_usage | REAL | 平时用电量（kWh） |
| peak_usage | REAL | 峰时用电量（kWh） |
| tip_usage | REAL | 尖时用电量（kWh） |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

---

## `yearly_usage` — 年度用电量汇总

主键：`(user_id, year)`

| 字段 | 类型 | 说明 |
|------|------|------|
| user_id | TEXT | 用户户号 |
| user_name | TEXT | 用户名 |
| year | TEXT | 年份（YYYY） |
| total_usage | REAL | 总用电量（kWh） |
| total_charge | REAL | 总电费（CNY） |
| valley_usage | REAL | 谷时用电量（kWh） |
| flat_usage | REAL | 平时用电量（kWh） |
| peak_usage | REAL | 峰时用电量（kWh） |
| tip_usage | REAL | 尖时用电量（kWh） |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

---

## `balance_log` — 电费余额日志

主键：`(user_id, as_of)`

| 字段 | 类型 | 说明 |
|------|------|------|
| user_id | TEXT | 用户户号 |
| user_name | TEXT | 用户名 |
| as_of | TEXT | 记录日期（YYYY-MM-DD，按天去重） |
| balance | REAL | 电费余额（CNY） |
| amount_due | REAL | 应交金额（CNY） |
| created_at | DATETIME | 创建时间 |

---

通过 `DATA_RETENTION_DAYS` 环境变量控制数据保留天数（默认 365 天），自动清理过期数据。
`IGNORE_USER_ID` 中配置的用户数据会在每次运行时自动清理。
