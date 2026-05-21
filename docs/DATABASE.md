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

## `step_usage` — 阶梯用电量

> 仅住宅用户有阶梯用电数据，充电桩用户无阶梯信息。

主键：`(user_id, year_month)`

| 字段 | 类型 | 说明 |
|------|------|------|
| user_id | TEXT | 用户户号 |
| user_name | TEXT | 用户名 |
| year_month | TEXT | 年月（YYYY-MM） |
| used_step1 | REAL | 已用一阶电量（kWh） |
| remain_step1 | REAL | 剩余一阶电量（kWh） |
| used_step2 | REAL | 已用二阶电量（kWh） |
| remain_step2 | REAL | 剩余二阶电量（kWh） |
| used_step3 | REAL | 已用三阶电量（kWh） |
| total_usage | REAL | 累计用电量（kWh） |
| step_stage | INTEGER | 当前阶梯阶段（1/2/3） |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

---

通过 `DATA_RETENTION_DAYS` 环境变量控制数据保留天数（默认 365 天），自动清理过期的日用电和余额记录。
`IGNORE_USER_ID` 中配置的户号仅跳过本次抓取，不会删除数据库中的历史数据。
