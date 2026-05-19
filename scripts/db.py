"""Unified database module with 5 tables for SGCC electricity data.

Tables:
    users         - user account info (user_id, phone_number, timestamps)
    daily_usage   - daily electricity usage with TOU breakdown (valley/flat/peak/tip)
    monthly_usage - monthly electricity usage with TOU breakdown
    yearly_usage  - yearly electricity usage with TOU breakdown
    balance_log   - balance history with enhanced info (prepay, estimated, owe, penalty)

Field naming conventions:
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
from datetime import datetime, timedelta
from typing import Any, Optional

import mysql.connector


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

    def upsert_user(self, user_id: str, phone_number: str = "") -> bool:
        raise NotImplementedError

    def cleanup_old_data(self) -> None:
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
            logging.info("SQLite ready at %s for user %s", db_path, self.user_id)
            return True
        except (sqlite3.Error, ValueError) as exc:
            logging.error("Failed to prepare SQLite: %s", exc)
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
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS {self.DAILY_TABLE} (
                user_id TEXT NOT NULL,
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
                as_of TEXT NOT NULL,
                balance REAL,
                prepay_balance REAL,
                estimated_amount REAL,
                history_owe REAL,
                penalty REAL,
                total_usage REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, as_of)
            );

            CREATE INDEX IF NOT EXISTS idx_daily_user_date ON {self.DAILY_TABLE}(user_id, date);
            CREATE INDEX IF NOT EXISTS idx_monthly_user_month ON {self.MONTHLY_TABLE}(user_id, month);
            CREATE INDEX IF NOT EXISTS idx_yearly_user_year ON {self.YEARLY_TABLE}(user_id, year);
            CREATE INDEX IF NOT EXISTS idx_balance_user_asof ON {self.BALANCE_TABLE}(user_id, as_of);
        """)
        self.connect.commit()

    def upsert_user(self, user_id: str, phone_number: str = "") -> bool:
        return self._execute(
            f"INSERT OR REPLACE INTO {self.USERS_TABLE} (user_id, phone_number, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (str(user_id).strip(), str(phone_number)),
        )

    def insert_daily_data(self, data: dict) -> bool:
        date = str(data["date"]).strip()
        return self._execute(
            f"""INSERT INTO {self.DAILY_TABLE} (user_id, date, total_usage, valley_usage, flat_usage, peak_usage, tip_usage)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, date) DO UPDATE SET
                    total_usage = excluded.total_usage,
                    valley_usage = CASE WHEN excluded.valley_usage > 0 THEN excluded.valley_usage ELSE {self.DAILY_TABLE}.valley_usage END,
                    flat_usage = CASE WHEN excluded.flat_usage > 0 THEN excluded.flat_usage ELSE {self.DAILY_TABLE}.flat_usage END,
                    peak_usage = CASE WHEN excluded.peak_usage > 0 THEN excluded.peak_usage ELSE {self.DAILY_TABLE}.peak_usage END,
                    tip_usage = CASE WHEN excluded.tip_usage > 0 THEN excluded.tip_usage ELSE {self.DAILY_TABLE}.tip_usage END,
                    updated_at = CURRENT_TIMESTAMP""",
            (self.user_id, date,
             _sf(data.get("total_usage"), 0.0), _sf(data.get("valley_usage"), 0.0),
             _sf(data.get("flat_usage"), 0.0), _sf(data.get("peak_usage"), 0.0),
             _sf(data.get("tip_usage"), 0.0)),
        )

    def insert_monthly_data(self, data: dict) -> bool:
        month = str(data["month"]).strip()
        return self._execute(
            f"""INSERT INTO {self.MONTHLY_TABLE} (user_id, month, total_usage, total_charge, valley_usage, flat_usage, peak_usage, tip_usage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, month) DO UPDATE SET
                    total_usage = COALESCE(excluded.total_usage, {self.MONTHLY_TABLE}.total_usage),
                    total_charge = COALESCE(excluded.total_charge, {self.MONTHLY_TABLE}.total_charge),
                    valley_usage = CASE WHEN excluded.valley_usage > 0 THEN excluded.valley_usage ELSE {self.MONTHLY_TABLE}.valley_usage END,
                    flat_usage = CASE WHEN excluded.flat_usage > 0 THEN excluded.flat_usage ELSE {self.MONTHLY_TABLE}.flat_usage END,
                    peak_usage = CASE WHEN excluded.peak_usage > 0 THEN excluded.peak_usage ELSE {self.MONTHLY_TABLE}.peak_usage END,
                    tip_usage = CASE WHEN excluded.tip_usage > 0 THEN excluded.tip_usage ELSE {self.MONTHLY_TABLE}.tip_usage END,
                    updated_at = CURRENT_TIMESTAMP""",
            (self.user_id, month,
             _sf(data.get("total_usage"), 0.0), _sf(data.get("total_charge")),
             _sf(data.get("valley_usage"), 0.0), _sf(data.get("flat_usage"), 0.0),
             _sf(data.get("peak_usage"), 0.0), _sf(data.get("tip_usage"), 0.0)),
        )

    def insert_yearly_data(self, data: dict) -> bool:
        year = str(data["year"]).strip()
        return self._execute(
            f"""INSERT INTO {self.YEARLY_TABLE} (user_id, year, total_usage, total_charge, valley_usage, flat_usage, peak_usage, tip_usage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, year) DO UPDATE SET
                    total_usage = COALESCE(excluded.total_usage, {self.YEARLY_TABLE}.total_usage),
                    total_charge = COALESCE(excluded.total_charge, {self.YEARLY_TABLE}.total_charge),
                    valley_usage = CASE WHEN excluded.valley_usage > 0 THEN excluded.valley_usage ELSE {self.YEARLY_TABLE}.valley_usage END,
                    flat_usage = CASE WHEN excluded.flat_usage > 0 THEN excluded.flat_usage ELSE {self.YEARLY_TABLE}.flat_usage END,
                    peak_usage = CASE WHEN excluded.peak_usage > 0 THEN excluded.peak_usage ELSE {self.YEARLY_TABLE}.peak_usage END,
                    tip_usage = CASE WHEN excluded.tip_usage > 0 THEN excluded.tip_usage ELSE {self.YEARLY_TABLE}.tip_usage END,
                    updated_at = CURRENT_TIMESTAMP""",
            (self.user_id, year,
             _sf(data.get("total_usage"), 0.0), _sf(data.get("total_charge")),
             _sf(data.get("valley_usage"), 0.0), _sf(data.get("flat_usage"), 0.0),
             _sf(data.get("peak_usage"), 0.0), _sf(data.get("tip_usage"), 0.0)),
        )

    def insert_balance_log(self, data: dict) -> bool:
        as_of = str(data.get("as_of") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")).strip()
        return self._execute(
            f"""INSERT OR REPLACE INTO {self.BALANCE_TABLE} (user_id, as_of, balance, prepay_balance, estimated_amount, history_owe, penalty, total_usage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (self.user_id, as_of,
             _sf(data.get("balance")), _sf(data.get("prepay_balance")),
             _sf(data.get("estimated_amount")), _sf(data.get("history_owe")),
             _sf(data.get("penalty")), _sf(data.get("total_usage"))),
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

    def sync_monthly_from_daily(self, month: str) -> bool:
        cursor = self.connect.cursor()
        try:
            cursor.execute(
                f"""SELECT COALESCE(SUM(total_usage),0), COALESCE(SUM(valley_usage),0),
                           COALESCE(SUM(flat_usage),0), COALESCE(SUM(peak_usage),0),
                           COALESCE(SUM(tip_usage),0), COUNT(*)
                    FROM {self.DAILY_TABLE} WHERE user_id=? AND substr(date,1,7)=?""",
                (self.user_id, str(month).strip()),
            )
            row = cursor.fetchone()
            if row is None or row[5] == 0:
                return False
            return self.insert_monthly_data({
                "month": month,
                "total_usage": float(row[0]),
                "valley_usage": float(row[1]), "flat_usage": float(row[2]),
                "peak_usage": float(row[3]), "tip_usage": float(row[4]),
            })
        finally:
            cursor.close()

    def cleanup_old_data(self) -> None:
        retention_days = int(os.getenv("DATA_RETENTION_DAYS", 365))
        if retention_days <= 0:
            return
        cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
        self._execute(f"DELETE FROM {self.DAILY_TABLE} WHERE user_id=? AND date<?", (self.user_id, cutoff))
        self._execute(f"DELETE FROM {self.BALANCE_TABLE} WHERE user_id=? AND as_of<?", (self.user_id, cutoff))
        logging.info("Cleaned up data older than %s for user %s", cutoff, self.user_id)

    def _execute(self, sql: str, params: tuple = ()) -> bool:
        if self.connect is None:
            logging.error("Database not connected.")
            return False
        try:
            self.connect.execute(sql, params)
            self.connect.commit()
            return True
        except (sqlite3.Error, TypeError, ValueError) as exc:
            logging.error("DB execute failed: %s", exc)
            return False

    def close_connect(self) -> None:
        if self.connect is not None:
            self.connect.close()
            self.connect = None
            logging.info("SQLite connection closed.")


# ---------------------------------------------------------------------------
# MySQL implementation
# ---------------------------------------------------------------------------

class MysqlDB(DB):
    USERS_TABLE = "users"
    DAILY_TABLE = "daily_usage"
    MONTHLY_TABLE = "monthly_usage"
    YEARLY_TABLE = "yearly_usage"
    BALANCE_TABLE = "balance_log"

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
                logging.info("MySQL connected for user %s", self.user_id)
                return True
            return False
        except Exception as exc:
            logging.error("MySQL connect failed: %s", exc)
            return False

    def _create_schema(self) -> None:
        cursor = self.connect.cursor()
        try:
            cursor.execute(f"""CREATE TABLE IF NOT EXISTS `{self.USERS_TABLE}` (
                `user_id` VARCHAR(50) PRIMARY KEY NOT NULL,
                `phone_number` VARCHAR(50) NOT NULL DEFAULT '',
                `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

            cursor.execute(f"""CREATE TABLE IF NOT EXISTS `{self.DAILY_TABLE}` (
                `user_id` VARCHAR(50) NOT NULL,
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
                `as_of` DATETIME NOT NULL,
                `balance` DOUBLE,
                `prepay_balance` DOUBLE,
                `estimated_amount` DOUBLE,
                `history_owe` DOUBLE,
                `penalty` DOUBLE,
                `total_usage` DOUBLE,
                `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (`user_id`, `as_of`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

            self.connect.commit()
        except Exception as exc:
            logging.error("MySQL schema creation failed: %s", exc)
        finally:
            cursor.close()

    def upsert_user(self, user_id: str, phone_number: str = "") -> bool:
        return self._execute(
            f"REPLACE INTO `{self.USERS_TABLE}` (user_id, phone_number) VALUES (%s, %s)",
            (str(user_id).strip(), str(phone_number)),
        )

    def insert_daily_data(self, data: dict) -> bool:
        date = str(data["date"]).strip()
        return self._execute(
            f"""INSERT INTO `{self.DAILY_TABLE}` (user_id, date, total_usage, valley_usage, flat_usage, peak_usage, tip_usage)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    total_usage=VALUES(total_usage),
                    valley_usage=CASE WHEN VALUES(valley_usage)>0 THEN VALUES(valley_usage) ELSE valley_usage END,
                    flat_usage=CASE WHEN VALUES(flat_usage)>0 THEN VALUES(flat_usage) ELSE flat_usage END,
                    peak_usage=CASE WHEN VALUES(peak_usage)>0 THEN VALUES(peak_usage) ELSE peak_usage END,
                    tip_usage=CASE WHEN VALUES(tip_usage)>0 THEN VALUES(tip_usage) ELSE tip_usage END""",
            (self.user_id, date,
             _sf(data.get("total_usage"), 0.0), _sf(data.get("valley_usage"), 0.0),
             _sf(data.get("flat_usage"), 0.0), _sf(data.get("peak_usage"), 0.0),
             _sf(data.get("tip_usage"), 0.0)),
        )

    def insert_monthly_data(self, data: dict) -> bool:
        month = str(data["month"]).strip()
        return self._execute(
            f"""INSERT INTO `{self.MONTHLY_TABLE}` (user_id, month, total_usage, total_charge, valley_usage, flat_usage, peak_usage, tip_usage)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    total_usage=COALESCE(VALUES(total_usage), total_usage),
                    total_charge=COALESCE(VALUES(total_charge), total_charge),
                    valley_usage=CASE WHEN VALUES(valley_usage)>0 THEN VALUES(valley_usage) ELSE valley_usage END,
                    flat_usage=CASE WHEN VALUES(flat_usage)>0 THEN VALUES(flat_usage) ELSE flat_usage END,
                    peak_usage=CASE WHEN VALUES(peak_usage)>0 THEN VALUES(peak_usage) ELSE peak_usage END,
                    tip_usage=CASE WHEN VALUES(tip_usage)>0 THEN VALUES(tip_usage) ELSE tip_usage END""",
            (self.user_id, month,
             _sf(data.get("total_usage"), 0.0), _sf(data.get("total_charge")),
             _sf(data.get("valley_usage"), 0.0), _sf(data.get("flat_usage"), 0.0),
             _sf(data.get("peak_usage"), 0.0), _sf(data.get("tip_usage"), 0.0)),
        )

    def insert_yearly_data(self, data: dict) -> bool:
        year = str(data["year"]).strip()
        return self._execute(
            f"""INSERT INTO `{self.YEARLY_TABLE}` (user_id, year, total_usage, total_charge, valley_usage, flat_usage, peak_usage, tip_usage)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    total_usage=COALESCE(VALUES(total_usage), total_usage),
                    total_charge=COALESCE(VALUES(total_charge), total_charge),
                    valley_usage=CASE WHEN VALUES(valley_usage)>0 THEN VALUES(valley_usage) ELSE valley_usage END,
                    flat_usage=CASE WHEN VALUES(flat_usage)>0 THEN VALUES(flat_usage) ELSE flat_usage END,
                    peak_usage=CASE WHEN VALUES(peak_usage)>0 THEN VALUES(peak_usage) ELSE peak_usage END,
                    tip_usage=CASE WHEN VALUES(tip_usage)>0 THEN VALUES(tip_usage) ELSE tip_usage END""",
            (self.user_id, year,
             _sf(data.get("total_usage"), 0.0), _sf(data.get("total_charge")),
             _sf(data.get("valley_usage"), 0.0), _sf(data.get("flat_usage"), 0.0),
             _sf(data.get("peak_usage"), 0.0), _sf(data.get("tip_usage"), 0.0)),
        )

    def insert_balance_log(self, data: dict) -> bool:
        as_of = str(data.get("as_of") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")).strip()
        return self._execute(
            f"""REPLACE INTO `{self.BALANCE_TABLE}` (user_id, as_of, balance, prepay_balance, estimated_amount, history_owe, penalty, total_usage)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (self.user_id, as_of,
             _sf(data.get("balance")), _sf(data.get("prepay_balance")),
             _sf(data.get("estimated_amount")), _sf(data.get("history_owe")),
             _sf(data.get("penalty")), _sf(data.get("total_usage"))),
        )

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
        logging.info("Cleaned up data older than %s for user %s", cutoff, self.user_id)

    def _execute(self, sql: str, params: tuple = ()) -> bool:
        if self.connect is None or not self.connect.is_connected():
            logging.error("MySQL not connected.")
            return False
        cursor = None
        try:
            cursor = self.connect.cursor()
            cursor.execute(sql, params)
            self.connect.commit()
            return True
        except Exception as exc:
            logging.error("MySQL execute failed: %s", exc)
            return False
        finally:
            if cursor:
                cursor.close()

    def close_connect(self) -> None:
        if self.connect and self.connect.is_connected():
            self.connect.close()
            self.connect = None
            logging.info("MySQL connection closed.")


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Safe float conversion."""
    try:
        text = str(value).strip()
        if text in ("", "-", "—", "None"):
            return default
        return float(text)
    except (TypeError, ValueError):
        return default
