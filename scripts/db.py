"""Unified database module with 5 tables for SGCC electricity data.

Tables:
    users         - user account info (user_id, phone_number, user_name, timestamps)
    daily_usage   - daily electricity usage with TOU breakdown (valley/flat/peak/tip)
    monthly_usage - monthly electricity usage with TOU breakdown
    yearly_usage  - yearly electricity usage with TOU breakdown
    balance_log   - balance history with enhanced info (prepay, estimated, owe, penalty)

Field naming conventions:
    user_name     - user/consumer name from State Grid
    total_usage   - total electricity usage in kWh
    total_charge  - total charge in CNY
    valley_usage  - valley/low period usage in kWh
    flat_usage    - flat/normal period usage in kWh
    peak_usage    - peak period usage in kWh
    tip_usage     - tip/sharp period usage in kWh
"""

import logging
import os
import sqlite3
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Any, Optional

import mysql.connector


def _month_date_bounds(month: str) -> tuple[str, str]:
    """自然月 YYYY-MM 的起止日期（含首尾）。"""
    month_key = str(month).strip()[:7]
    year, mon = map(int, month_key.split("-"))
    last_day = monthrange(year, mon)[1]
    return f"{month_key}-01", f"{month_key}-{last_day:02d}"


def _row_to_month_tou_summary(month_key: str, row) -> Optional[dict]:
    if row is None or row[5] == 0:
        return None
    return {
        "month": month_key,
        "total_usage": round(float(row[0]), 2),
        "valley_usage": round(float(row[1]), 2),
        "flat_usage": round(float(row[2]), 2),
        "peak_usage": round(float(row[3]), 2),
        "tip_usage": round(float(row[4]), 2),
        "day_count": int(row[5]),
    }


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class DB:
    def connect_user_db(self, user_id: str) -> bool:
        raise NotImplementedError

    def insert_daily_data(self, data: dict) -> bool:
        raise NotImplementedError

    def insert_monthly_data(self, data: dict) -> bool:
        raise NotImplementedError

    def insert_yearly_data(self, data: dict) -> bool:
        raise NotImplementedError

    def insert_balance_log(self, data: dict) -> bool:
        raise NotImplementedError

    def upsert_user(self, user_id: str, phone_number: str = "", user_name: str = "") -> bool:
        raise NotImplementedError

    def cleanup_old_data(self) -> None:
        raise NotImplementedError

    def delete_user_data(self, user_id: str) -> None:
        raise NotImplementedError

    def insert_step_data(self, data: dict) -> bool:
        raise NotImplementedError

    def query_month_tou_from_daily(self, user_id: str, month: str) -> Optional[dict]:
        """按自然月汇总 daily_usage 表中的谷/平/峰/尖电量。"""
        raise NotImplementedError

    def close_connect(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# SQLite implementation
# ---------------------------------------------------------------------------

class SqliteDB(DB):
    USERS_TABLE = "users"
    DAILY_TABLE = "daily_usage"
    MONTHLY_TABLE = "monthly_usage"
    YEARLY_TABLE = "yearly_usage"
    BALANCE_TABLE = "balance_log"
    STEP_TABLE = "step_usage"

    def __init__(self) -> None:
        self.connect: Optional[sqlite3.Connection] = None
        self.user_id: Optional[str] = None

    def connect_user_db(self, user_id: str) -> bool:
        try:
            self.user_id = str(user_id).strip()
            if not self.user_id:
                raise ValueError("user_id cannot be empty")

            db_name = os.getenv("DB_NAME", "homeassistant.db")
            from const import get_data_dir
            db_path = os.path.join(get_data_dir(), db_name)

            self.connect = sqlite3.connect(db_path, timeout=30)
            self._configure()
            self._create_schema()
            logging.info("SQLite 已就绪: %s，户号 %s", db_path, self.user_id)
            return True
        except (sqlite3.Error, ValueError) as exc:
            logging.error("SQLite 初始化失败: %s", exc)
            return False

    def _configure(self) -> None:
        assert self.connect is not None
        self.connect.execute("PRAGMA journal_mode=WAL")
        self.connect.execute("PRAGMA synchronous=NORMAL")
        self.connect.execute("PRAGMA busy_timeout=5000")

    def _create_schema(self) -> None:
        assert self.connect is not None
        self.connect.executescript(f"""
            CREATE TABLE IF NOT EXISTS {self.USERS_TABLE} (
                user_id TEXT PRIMARY KEY NOT NULL,
                phone_number TEXT NOT NULL DEFAULT '',
                user_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS {self.DAILY_TABLE} (
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL DEFAULT '',
                date TEXT NOT NULL,
                total_usage REAL NOT NULL DEFAULT 0,
                valley_usage REAL NOT NULL DEFAULT 0,
                flat_usage REAL NOT NULL DEFAULT 0,
                peak_usage REAL NOT NULL DEFAULT 0,
                tip_usage REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, date)
            );

            CREATE TABLE IF NOT EXISTS {self.MONTHLY_TABLE} (
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL DEFAULT '',
                month TEXT NOT NULL,
                total_usage REAL NOT NULL DEFAULT 0,
                total_charge REAL,
                valley_usage REAL NOT NULL DEFAULT 0,
                flat_usage REAL NOT NULL DEFAULT 0,
                peak_usage REAL NOT NULL DEFAULT 0,
                tip_usage REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, month)
            );

            CREATE TABLE IF NOT EXISTS {self.YEARLY_TABLE} (
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL,
                total_usage REAL NOT NULL DEFAULT 0,
                total_charge REAL,
                valley_usage REAL NOT NULL DEFAULT 0,
                flat_usage REAL NOT NULL DEFAULT 0,
                peak_usage REAL NOT NULL DEFAULT 0,
                tip_usage REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, year)
            );

            CREATE TABLE IF NOT EXISTS {self.BALANCE_TABLE} (
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL DEFAULT '',
                as_of TEXT NOT NULL,
                balance REAL,
                amount_due REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, as_of)
            );

            CREATE TABLE IF NOT EXISTS {self.STEP_TABLE} (
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL DEFAULT '',
                year_month TEXT NOT NULL,
                used_step1 REAL NOT NULL DEFAULT 0,
                remain_step1 REAL NOT NULL DEFAULT 0,
                used_step2 REAL NOT NULL DEFAULT 0,
                remain_step2 REAL NOT NULL DEFAULT 0,
                used_step3 REAL NOT NULL DEFAULT 0,
                total_usage REAL NOT NULL DEFAULT 0,
                step_stage INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, year_month)
            );

            CREATE INDEX IF NOT EXISTS idx_daily_user_date ON {self.DAILY_TABLE}(user_id, date);
            CREATE INDEX IF NOT EXISTS idx_monthly_user_month ON {self.MONTHLY_TABLE}(user_id, month);
            CREATE INDEX IF NOT EXISTS idx_yearly_user_year ON {self.YEARLY_TABLE}(user_id, year);
            CREATE INDEX IF NOT EXISTS idx_balance_user_asof ON {self.BALANCE_TABLE}(user_id, as_of);
        """)
        self.connect.commit()

    def upsert_user(self, user_id: str, phone_number: str = "", user_name: str = "") -> bool:
        return self._execute(
            f"INSERT OR REPLACE INTO {self.USERS_TABLE} (user_id, phone_number, user_name, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (str(user_id).strip(), str(phone_number), str(user_name)),
        )

    def insert_daily_data(self, data: dict) -> bool:
        date = str(data["date"]).strip()
        return self._execute(
            f"""INSERT INTO {self.DAILY_TABLE} (user_id, user_name, date, total_usage, valley_usage, flat_usage, peak_usage, tip_usage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, date) DO UPDATE SET
                    user_name = CASE WHEN excluded.user_name != '' THEN excluded.user_name ELSE {self.DAILY_TABLE}.user_name END,
                    total_usage = excluded.total_usage,
                    valley_usage = CASE WHEN excluded.valley_usage > 0 THEN excluded.valley_usage ELSE {self.DAILY_TABLE}.valley_usage END,
                    flat_usage = CASE WHEN excluded.flat_usage > 0 THEN excluded.flat_usage ELSE {self.DAILY_TABLE}.flat_usage END,
                    peak_usage = CASE WHEN excluded.peak_usage > 0 THEN excluded.peak_usage ELSE {self.DAILY_TABLE}.peak_usage END,
                    tip_usage = CASE WHEN excluded.tip_usage > 0 THEN excluded.tip_usage ELSE {self.DAILY_TABLE}.tip_usage END,
                    updated_at = CURRENT_TIMESTAMP""",
            (self.user_id, data.get("user_name", ""),
             date,
             _sf(data.get("total_usage"), 0.0), _sf(data.get("valley_usage"), 0.0),
             _sf(data.get("flat_usage"), 0.0), _sf(data.get("peak_usage"), 0.0),
             _sf(data.get("tip_usage"), 0.0)),
        )

    def insert_monthly_data(self, data: dict) -> bool:
        month = str(data["month"]).strip()
        return self._execute(
            f"""INSERT INTO {self.MONTHLY_TABLE} (user_id, user_name, month, total_usage, total_charge, valley_usage, flat_usage, peak_usage, tip_usage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, month) DO UPDATE SET
                    user_name = CASE WHEN excluded.user_name != '' THEN excluded.user_name ELSE {self.MONTHLY_TABLE}.user_name END,
                    total_usage = COALESCE(excluded.total_usage, {self.MONTHLY_TABLE}.total_usage),
                    total_charge = COALESCE(excluded.total_charge, {self.MONTHLY_TABLE}.total_charge),
                    valley_usage = CASE WHEN excluded.valley_usage > 0 THEN excluded.valley_usage ELSE {self.MONTHLY_TABLE}.valley_usage END,
                    flat_usage = CASE WHEN excluded.flat_usage > 0 THEN excluded.flat_usage ELSE {self.MONTHLY_TABLE}.flat_usage END,
                    peak_usage = CASE WHEN excluded.peak_usage > 0 THEN excluded.peak_usage ELSE {self.MONTHLY_TABLE}.peak_usage END,
                    tip_usage = CASE WHEN excluded.tip_usage > 0 THEN excluded.tip_usage ELSE {self.MONTHLY_TABLE}.tip_usage END,
                    updated_at = CURRENT_TIMESTAMP""",
            (self.user_id, data.get("user_name", ""),
             month,
             _sf(data.get("total_usage"), 0.0), _sf(data.get("total_charge")),
             _sf(data.get("valley_usage"), 0.0), _sf(data.get("flat_usage"), 0.0),
             _sf(data.get("peak_usage"), 0.0), _sf(data.get("tip_usage"), 0.0)),
        )

    def insert_yearly_data(self, data: dict) -> bool:
        year = str(data["year"]).strip()
        return self._execute(
            f"""INSERT INTO {self.YEARLY_TABLE} (user_id, user_name, year, total_usage, total_charge, valley_usage, flat_usage, peak_usage, tip_usage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, year) DO UPDATE SET
                    user_name = CASE WHEN excluded.user_name != '' THEN excluded.user_name ELSE {self.YEARLY_TABLE}.user_name END,
                    total_usage = COALESCE(excluded.total_usage, {self.YEARLY_TABLE}.total_usage),
                    total_charge = COALESCE(excluded.total_charge, {self.YEARLY_TABLE}.total_charge),
                    valley_usage = CASE WHEN excluded.valley_usage > 0 THEN excluded.valley_usage ELSE {self.YEARLY_TABLE}.valley_usage END,
                    flat_usage = CASE WHEN excluded.flat_usage > 0 THEN excluded.flat_usage ELSE {self.YEARLY_TABLE}.flat_usage END,
                    peak_usage = CASE WHEN excluded.peak_usage > 0 THEN excluded.peak_usage ELSE {self.YEARLY_TABLE}.peak_usage END,
                    tip_usage = CASE WHEN excluded.tip_usage > 0 THEN excluded.tip_usage ELSE {self.YEARLY_TABLE}.tip_usage END,
                    updated_at = CURRENT_TIMESTAMP""",
            (self.user_id, data.get("user_name", ""),
             year,
             _sf(data.get("total_usage"), 0.0), _sf(data.get("total_charge")),
             _sf(data.get("valley_usage"), 0.0), _sf(data.get("flat_usage"), 0.0),
             _sf(data.get("peak_usage"), 0.0), _sf(data.get("tip_usage"), 0.0)),
        )

    def insert_balance_log(self, data: dict) -> bool:
        # 默认按天去重：同一天多次运行只保留最新一条
        as_of_raw = data.get("as_of") or datetime.now().strftime("%Y-%m-%d")
        # 统一截断为日期格式 (YYYY-MM-DD)，避免时间戳导致重复
        as_of = str(as_of_raw).strip()[:10]
        return self._execute(
            f"""INSERT OR REPLACE INTO {self.BALANCE_TABLE} (user_id, user_name, as_of, balance, amount_due)
                VALUES (?, ?, ?, ?, ?)""",
            (self.user_id, data.get("user_name", ""),
             as_of,
             _sf(data.get("balance")), _sf(data.get("amount_due"))),
        )

    def sync_yearly_from_monthly(self, year: str) -> bool:
        cursor = self.connect.cursor()
        try:
            cursor.execute(
                f"""SELECT COALESCE(SUM(total_usage),0), COALESCE(SUM(total_charge),0),
                           COALESCE(SUM(valley_usage),0), COALESCE(SUM(flat_usage),0),
                           COALESCE(SUM(peak_usage),0), COALESCE(SUM(tip_usage),0)
                    FROM {self.MONTHLY_TABLE} WHERE user_id=? AND substr(month,1,4)=?""",
                (self.user_id, str(year).strip()),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            return self.insert_yearly_data({
                "year": year,
                "total_usage": float(row[0]), "total_charge": float(row[1]),
                "valley_usage": float(row[2]), "flat_usage": float(row[3]),
                "peak_usage": float(row[4]), "tip_usage": float(row[5]),
            })
        finally:
            cursor.close()

    def query_month_tou_from_daily(self, user_id: str, month: str) -> Optional[dict]:
        uid = str(user_id).strip()
        month_key = str(month).strip()[:7]
        start_date, end_date = _month_date_bounds(month_key)
        cursor = self.connect.cursor()
        try:
            cursor.execute(
                f"""SELECT COALESCE(SUM(total_usage),0), COALESCE(SUM(valley_usage),0),
                           COALESCE(SUM(flat_usage),0), COALESCE(SUM(peak_usage),0),
                           COALESCE(SUM(tip_usage),0), COUNT(*)
                    FROM {self.DAILY_TABLE}
                    WHERE user_id=? AND date >= ? AND date <= ?""",
                (uid, start_date, end_date),
            )
            return _row_to_month_tou_summary(month_key, cursor.fetchone())
        finally:
            cursor.close()

    def sync_monthly_from_daily(self, month: str) -> bool:
        summary = self.query_month_tou_from_daily(self.user_id, month)
        if not summary:
            return False
        return self.insert_monthly_data({
            "month": month,
            "total_usage": summary["total_usage"],
            "valley_usage": summary["valley_usage"],
            "flat_usage": summary["flat_usage"],
            "peak_usage": summary["peak_usage"],
            "tip_usage": summary["tip_usage"],
        })

    def cleanup_old_data(self) -> None:
        retention_days = int(os.getenv("DATA_RETENTION_DAYS", 365))
        if retention_days <= 0:
            return
        cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
        self._execute(f"DELETE FROM {self.DAILY_TABLE} WHERE user_id=? AND date<?", (self.user_id, cutoff))
        self._execute(f"DELETE FROM {self.BALANCE_TABLE} WHERE user_id=? AND as_of<?", (self.user_id, cutoff))
        logging.info("已清理户号 %s 早于 %s 的历史数据", self.user_id, cutoff)

    def insert_step_data(self, data: dict) -> bool:
        year_month = str(data["year_month"]).strip()
        return self._execute(
            f"""INSERT INTO {self.STEP_TABLE} (user_id, user_name, year_month, used_step1, remain_step1, used_step2, remain_step2, used_step3, total_usage, step_stage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, year_month) DO UPDATE SET
                    user_name = CASE WHEN excluded.user_name != '' THEN excluded.user_name ELSE {self.STEP_TABLE}.user_name END,
                    used_step1 = excluded.used_step1,
                    remain_step1 = excluded.remain_step1,
                    used_step2 = excluded.used_step2,
                    remain_step2 = excluded.remain_step2,
                    used_step3 = excluded.used_step3,
                    total_usage = excluded.total_usage,
                    step_stage = excluded.step_stage,
                    updated_at = CURRENT_TIMESTAMP""",
            (self.user_id, data.get("user_name", ""),
             year_month,
             _sf(data.get("used_step1"), 0.0), _sf(data.get("remain_step1"), 0.0),
             _sf(data.get("used_step2"), 0.0), _sf(data.get("remain_step2"), 0.0),
             _sf(data.get("used_step3"), 0.0), _sf(data.get("total_usage"), 0.0),
             int(data.get("step_stage", 1))),
        )

    def delete_user_data(self, user_id: str) -> None:
        """手动清理指定户号数据（正常运行时不自动调用）"""
        for tbl in [self.DAILY_TABLE, self.MONTHLY_TABLE, self.YEARLY_TABLE,
                     self.BALANCE_TABLE, self.STEP_TABLE, self.USERS_TABLE]:
            self._execute(f"DELETE FROM {tbl} WHERE user_id=?", (str(user_id).strip(),))
        logging.info("已删除户号 %s 的全部数据", user_id)

    def _execute(self, sql: str, params: tuple = ()) -> bool:
        if self.connect is None:
            logging.error("数据库未连接")
            return False
        try:
            self.connect.execute(sql, params)
            self.connect.commit()
            return True
        except (sqlite3.Error, TypeError, ValueError) as exc:
            logging.error("数据库执行失败: %s", exc)
            return False

    def close_connect(self) -> None:
        if self.connect is not None:
            self.connect.close()
            self.connect = None
            logging.info("SQLite 连接已关闭")


# ---------------------------------------------------------------------------
# MySQL implementation
# ---------------------------------------------------------------------------

class MysqlDB(DB):
    USERS_TABLE = "users"
    DAILY_TABLE = "daily_usage"
    MONTHLY_TABLE = "monthly_usage"
    YEARLY_TABLE = "yearly_usage"
    BALANCE_TABLE = "balance_log"
    STEP_TABLE = "step_usage"

    def __init__(self) -> None:
        self.connect = None
        self.user_id: Optional[str] = None

    def connect_user_db(self, user_id: str) -> bool:
        try:
            self.user_id = str(user_id).strip()
            if not self.user_id:
                raise ValueError("user_id cannot be empty")

            self.connect = mysql.connector.connect(
                host=os.getenv("MYSQL_HOST"),
                user=os.getenv("MYSQL_USER"),
                password=os.getenv("MYSQL_PASSWORD"),
                database=os.getenv("MYSQL_DATABASE"),
                port=int(os.getenv("MYSQL_PORT", 3306)),
            )
            if self.connect.is_connected():
                self._create_schema()
                logging.info("MySQL 已连接，户号 %s", self.user_id)
                return True
            return False
        except Exception as exc:
            logging.error("MySQL 连接失败: %s", exc)
            return False

    def _create_schema(self) -> None:
        cursor = self.connect.cursor()
        try:
            cursor.execute(f"""CREATE TABLE IF NOT EXISTS `{self.USERS_TABLE}` (
                `user_id` VARCHAR(50) PRIMARY KEY NOT NULL,
                `phone_number` VARCHAR(50) NOT NULL DEFAULT '',
                `user_name` VARCHAR(100) NOT NULL DEFAULT '',
                `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

            cursor.execute(f"""CREATE TABLE IF NOT EXISTS `{self.DAILY_TABLE}` (
                `user_id` VARCHAR(50) NOT NULL,
                `user_name` VARCHAR(100) NOT NULL DEFAULT '',
                `date` DATE NOT NULL,
                `total_usage` DOUBLE NOT NULL DEFAULT 0,
                `valley_usage` DOUBLE NOT NULL DEFAULT 0,
                `flat_usage` DOUBLE NOT NULL DEFAULT 0,
                `peak_usage` DOUBLE NOT NULL DEFAULT 0,
                `tip_usage` DOUBLE NOT NULL DEFAULT 0,
                `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (`user_id`, `date`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

            cursor.execute(f"""CREATE TABLE IF NOT EXISTS `{self.MONTHLY_TABLE}` (
                `user_id` VARCHAR(50) NOT NULL,
                `user_name` VARCHAR(100) NOT NULL DEFAULT '',
                `month` VARCHAR(7) NOT NULL,
                `total_usage` DOUBLE NOT NULL DEFAULT 0,
                `total_charge` DOUBLE,
                `valley_usage` DOUBLE NOT NULL DEFAULT 0,
                `flat_usage` DOUBLE NOT NULL DEFAULT 0,
                `peak_usage` DOUBLE NOT NULL DEFAULT 0,
                `tip_usage` DOUBLE NOT NULL DEFAULT 0,
                `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (`user_id`, `month`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

            cursor.execute(f"""CREATE TABLE IF NOT EXISTS `{self.YEARLY_TABLE}` (
                `user_id` VARCHAR(50) NOT NULL,
                `user_name` VARCHAR(100) NOT NULL DEFAULT '',
                `year` VARCHAR(4) NOT NULL,
                `total_usage` DOUBLE NOT NULL DEFAULT 0,
                `total_charge` DOUBLE,
                `valley_usage` DOUBLE NOT NULL DEFAULT 0,
                `flat_usage` DOUBLE NOT NULL DEFAULT 0,
                `peak_usage` DOUBLE NOT NULL DEFAULT 0,
                `tip_usage` DOUBLE NOT NULL DEFAULT 0,
                `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (`user_id`, `year`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

            cursor.execute(f"""CREATE TABLE IF NOT EXISTS `{self.BALANCE_TABLE}` (
                `user_id` VARCHAR(50) NOT NULL,
                `user_name` VARCHAR(100) NOT NULL DEFAULT '',
                `as_of` DATETIME NOT NULL,
                `balance` DOUBLE,
                `amount_due` DOUBLE,
                `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (`user_id`, `as_of`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

            cursor.execute(f"""CREATE TABLE IF NOT EXISTS `{self.STEP_TABLE}` (
                `user_id` VARCHAR(50) NOT NULL,
                `user_name` VARCHAR(100) NOT NULL DEFAULT '',
                `year_month` VARCHAR(7) NOT NULL,
                `used_step1` DOUBLE NOT NULL DEFAULT 0,
                `remain_step1` DOUBLE NOT NULL DEFAULT 0,
                `used_step2` DOUBLE NOT NULL DEFAULT 0,
                `remain_step2` DOUBLE NOT NULL DEFAULT 0,
                `used_step3` DOUBLE NOT NULL DEFAULT 0,
                `total_usage` DOUBLE NOT NULL DEFAULT 0,
                `step_stage` INT NOT NULL DEFAULT 1,
                `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (`user_id`, `year_month`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

            self.connect.commit()
        except Exception as exc:
            logging.error("MySQL 表结构创建失败: %s", exc)
        finally:
            cursor.close()

    def upsert_user(self, user_id: str, phone_number: str = "", user_name: str = "") -> bool:
        return self._execute(
            f"REPLACE INTO `{self.USERS_TABLE}` (user_id, phone_number, user_name) VALUES (%s, %s, %s)",
            (str(user_id).strip(), str(phone_number), str(user_name)),
        )

    def insert_daily_data(self, data: dict) -> bool:
        date = str(data["date"]).strip()
        return self._execute(
            f"""INSERT INTO `{self.DAILY_TABLE}` (user_id, user_name, date, total_usage, valley_usage, flat_usage, peak_usage, tip_usage)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    user_name=CASE WHEN VALUES(user_name)!='' THEN VALUES(user_name) ELSE user_name END,
                    total_usage=VALUES(total_usage),
                    valley_usage=CASE WHEN VALUES(valley_usage)>0 THEN VALUES(valley_usage) ELSE valley_usage END,
                    flat_usage=CASE WHEN VALUES(flat_usage)>0 THEN VALUES(flat_usage) ELSE flat_usage END,
                    peak_usage=CASE WHEN VALUES(peak_usage)>0 THEN VALUES(peak_usage) ELSE peak_usage END,
                    tip_usage=CASE WHEN VALUES(tip_usage)>0 THEN VALUES(tip_usage) ELSE tip_usage END""",
            (self.user_id, data.get("user_name", ""),
             date,
             _sf(data.get("total_usage"), 0.0), _sf(data.get("valley_usage"), 0.0),
             _sf(data.get("flat_usage"), 0.0), _sf(data.get("peak_usage"), 0.0),
             _sf(data.get("tip_usage"), 0.0)),
        )

    def insert_monthly_data(self, data: dict) -> bool:
        month = str(data["month"]).strip()
        return self._execute(
            f"""INSERT INTO `{self.MONTHLY_TABLE}` (user_id, user_name, month, total_usage, total_charge, valley_usage, flat_usage, peak_usage, tip_usage)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    user_name=CASE WHEN VALUES(user_name)!='' THEN VALUES(user_name) ELSE user_name END,
                    total_usage=COALESCE(VALUES(total_usage), total_usage),
                    total_charge=COALESCE(VALUES(total_charge), total_charge),
                    valley_usage=CASE WHEN VALUES(valley_usage)>0 THEN VALUES(valley_usage) ELSE valley_usage END,
                    flat_usage=CASE WHEN VALUES(flat_usage)>0 THEN VALUES(flat_usage) ELSE flat_usage END,
                    peak_usage=CASE WHEN VALUES(peak_usage)>0 THEN VALUES(peak_usage) ELSE peak_usage END,
                    tip_usage=CASE WHEN VALUES(tip_usage)>0 THEN VALUES(tip_usage) ELSE tip_usage END""",
            (self.user_id, data.get("user_name", ""),
             month,
             _sf(data.get("total_usage"), 0.0), _sf(data.get("total_charge")),
             _sf(data.get("valley_usage"), 0.0), _sf(data.get("flat_usage"), 0.0),
             _sf(data.get("peak_usage"), 0.0), _sf(data.get("tip_usage"), 0.0)),
        )

    def insert_yearly_data(self, data: dict) -> bool:
        year = str(data["year"]).strip()
        return self._execute(
            f"""INSERT INTO `{self.YEARLY_TABLE}` (user_id, user_name, year, total_usage, total_charge, valley_usage, flat_usage, peak_usage, tip_usage)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    user_name=CASE WHEN VALUES(user_name)!='' THEN VALUES(user_name) ELSE user_name END,
                    total_usage=COALESCE(VALUES(total_usage), total_usage),
                    total_charge=COALESCE(VALUES(total_charge), total_charge),
                    valley_usage=CASE WHEN VALUES(valley_usage)>0 THEN VALUES(valley_usage) ELSE valley_usage END,
                    flat_usage=CASE WHEN VALUES(flat_usage)>0 THEN VALUES(flat_usage) ELSE flat_usage END,
                    peak_usage=CASE WHEN VALUES(peak_usage)>0 THEN VALUES(peak_usage) ELSE peak_usage END,
                    tip_usage=CASE WHEN VALUES(tip_usage)>0 THEN VALUES(tip_usage) ELSE tip_usage END""",
            (self.user_id, data.get("user_name", ""),
             year,
             _sf(data.get("total_usage"), 0.0), _sf(data.get("total_charge")),
             _sf(data.get("valley_usage"), 0.0), _sf(data.get("flat_usage"), 0.0),
             _sf(data.get("peak_usage"), 0.0), _sf(data.get("tip_usage"), 0.0)),
        )

    def insert_balance_log(self, data: dict) -> bool:
        # 默认按天去重：同一天多次运行只保留最新一条
        as_of_raw = data.get("as_of") or datetime.now().strftime("%Y-%m-%d")
        # 统一截断为日期格式 (YYYY-MM-DD)，避免时间戳导致重复
        as_of = str(as_of_raw).strip()[:10]
        return self._execute(
            f"""REPLACE INTO `{self.BALANCE_TABLE}` (user_id, user_name, as_of, balance, amount_due)
                VALUES (%s, %s, %s, %s, %s)""",
            (self.user_id, data.get("user_name", ""),
             as_of,
             _sf(data.get("balance")), _sf(data.get("amount_due"))),
        )

    def query_month_tou_from_daily(self, user_id: str, month: str) -> Optional[dict]:
        uid = str(user_id).strip()
        month_key = str(month).strip()[:7]
        start_date, end_date = _month_date_bounds(month_key)
        cursor = self.connect.cursor()
        try:
            cursor.execute(
                f"""SELECT COALESCE(SUM(total_usage),0), COALESCE(SUM(valley_usage),0),
                           COALESCE(SUM(flat_usage),0), COALESCE(SUM(peak_usage),0),
                           COALESCE(SUM(tip_usage),0), COUNT(*)
                    FROM `{self.DAILY_TABLE}`
                    WHERE user_id=%s AND `date` >= %s AND `date` <= %s""",
                (uid, start_date, end_date),
            )
            return _row_to_month_tou_summary(month_key, cursor.fetchone())
        finally:
            cursor.close()

    def sync_monthly_from_daily(self, month: str) -> bool:
        summary = self.query_month_tou_from_daily(self.user_id, month)
        if not summary:
            return False
        return self.insert_monthly_data({
            "month": month,
            "total_usage": summary["total_usage"],
            "valley_usage": summary["valley_usage"],
            "flat_usage": summary["flat_usage"],
            "peak_usage": summary["peak_usage"],
            "tip_usage": summary["tip_usage"],
        })

    def sync_yearly_from_monthly(self, year: str) -> bool:
        cursor = self.connect.cursor()
        try:
            cursor.execute(
                f"""SELECT COALESCE(SUM(total_usage),0), COALESCE(SUM(total_charge),0),
                           COALESCE(SUM(valley_usage),0), COALESCE(SUM(flat_usage),0),
                           COALESCE(SUM(peak_usage),0), COALESCE(SUM(tip_usage),0)
                    FROM `{self.MONTHLY_TABLE}` WHERE user_id=%s AND LEFT(month,4)=%s""",
                (self.user_id, str(year).strip()),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            return self.insert_yearly_data({
                "year": year,
                "total_usage": float(row[0]), "total_charge": float(row[1]),
                "valley_usage": float(row[2]), "flat_usage": float(row[3]),
                "peak_usage": float(row[4]), "tip_usage": float(row[5]),
            })
        finally:
            cursor.close()

    def cleanup_old_data(self) -> None:
        retention_days = int(os.getenv("DATA_RETENTION_DAYS", 365))
        if retention_days <= 0:
            return
        cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
        self._execute(f"DELETE FROM `{self.DAILY_TABLE}` WHERE user_id=%s AND date<%s", (self.user_id, cutoff))
        self._execute(f"DELETE FROM `{self.BALANCE_TABLE}` WHERE user_id=%s AND as_of<%s", (self.user_id, cutoff))
        logging.info("已清理户号 %s 早于 %s 的历史数据", self.user_id, cutoff)

    def insert_step_data(self, data: dict) -> bool:
        year_month = str(data["year_month"]).strip()
        return self._execute(
            f"""INSERT INTO `{self.STEP_TABLE}` (`user_id`, `user_name`, `year_month`, `used_step1`, `remain_step1`, `used_step2`, `remain_step2`, `used_step3`, `total_usage`, `step_stage`)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    `user_name`=CASE WHEN VALUES(`user_name`)!='' THEN VALUES(`user_name`) ELSE `user_name` END,
                    `used_step1`=VALUES(`used_step1`),
                    `remain_step1`=VALUES(`remain_step1`),
                    `used_step2`=VALUES(`used_step2`),
                    `remain_step2`=VALUES(`remain_step2`),
                    `used_step3`=VALUES(`used_step3`),
                    `total_usage`=VALUES(`total_usage`),
                    `step_stage`=VALUES(`step_stage`)""",
            (self.user_id, data.get("user_name", ""),
             year_month,
             _sf(data.get("used_step1"), 0.0), _sf(data.get("remain_step1"), 0.0),
             _sf(data.get("used_step2"), 0.0), _sf(data.get("remain_step2"), 0.0),
             _sf(data.get("used_step3"), 0.0), _sf(data.get("total_usage"), 0.0),
             int(data.get("step_stage", 1))),
        )

    def delete_user_data(self, user_id: str) -> None:
        """手动清理指定户号数据（正常运行时不自动调用）"""
        for tbl in [self.DAILY_TABLE, self.MONTHLY_TABLE, self.YEARLY_TABLE,
                     self.BALANCE_TABLE, self.STEP_TABLE, self.USERS_TABLE]:
            self._execute(f"DELETE FROM `{tbl}` WHERE user_id=%s", (str(user_id).strip(),))
        logging.info("已删除户号 %s 的全部数据", user_id)

    def _execute(self, sql: str, params: tuple = ()) -> bool:
        if self.connect is None or not self.connect.is_connected():
            logging.error("MySQL 未连接")
            return False
        cursor = None
        try:
            cursor = self.connect.cursor()
            cursor.execute(sql, params)
            self.connect.commit()
            return True
        except Exception as exc:
            logging.error("MySQL 执行失败: %s", exc)
            return False
        finally:
            if cursor:
                cursor.close()

    def close_connect(self) -> None:
        if self.connect and self.connect.is_connected():
            self.connect.close()
            self.connect = None
            logging.info("MySQL 连接已关闭")


def create_db() -> Optional[DB]:
    """按 DB_TYPE 创建数据库实例；未配置时返回 None。"""
    db_type = os.getenv("DB_TYPE", "sqlite").lower()
    if db_type == "mysql":
        return MysqlDB()
    if db_type == "sqlite":
        return SqliteDB()
    return None


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Safe float conversion."""
    try:
        text = str(value).strip()
        if text in ("", "-", "—", "None"):
            return default
        return float(text)
    except (TypeError, ValueError):
        return default
