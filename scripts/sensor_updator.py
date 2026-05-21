import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import requests
from const import *


class SensorUpdator:

    def __init__(self):
        HASS_URL = os.getenv("HASS_URL")
        HASS_TOKEN = os.getenv("HASS_TOKEN")
        self.base_url = HASS_URL[:-1] if HASS_URL.endswith("/") else HASS_URL
        self.token = HASS_TOKEN
        self._init_balance_notify()

    def _init_balance_notify(self):
        push_type = os.getenv("PUSH_TYPE", "None").strip().lower()
        if push_type == "pushplus":
            from notify import PushplusNotify
            self.balance_notify = PushplusNotify()
        elif push_type == "urlpush":
            from notify import UrlPushNotify
            self.balance_notify = UrlPushNotify()
        elif push_type == "wework":
            from notify import WeworkNotify
            self.balance_notify = WeworkNotify()
        else:
            self.balance_notify = None

    @staticmethod
    def _sensor_name(base: str, postfix: str) -> str:
        return base + postfix

    @staticmethod
    def _sensor_label(base: str) -> str:
        return SENSOR_LABELS.get(base, base)

    def _log_skip(self, base: str, postfix: str):
        name = self._sensor_name(base, postfix)
        label = self._sensor_label(base)
        logging.info(f"跳过更新 {label} 【{name}】，状态一致")

    def _log_updated(self, base: str, postfix: str, value, unit: str):
        name = self._sensor_name(base, postfix)
        label = self._sensor_label(base)
        logging.info(f"{label} 【{name}】 已更新: {value} {unit}")

    def update_one_userid(self, user_id: str, balance: float, last_daily_date: str, last_daily_usage: float, yearly_charge: float, yearly_usage: float, month_charge: float, month_usage: float, tou_data: dict = None, enhanced_balance: dict = None, step_data: dict = None, user_name: str = "", notify=True):
        logging.info(f"[{user_id}] 开始更新 Home Assistant 数据...")
        self._save_to_cache(user_id, balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage, tou_data, enhanced_balance, step_data=step_data, user_name=user_name)
        postfix = f"_{user_id[-4:]}"
        if balance is not None:
            if notify and self.balance_notify is not None:
                self.balance_notify(user_id, balance, user_name)
            self.update_balance(postfix, balance, enhanced_balance)
        if last_daily_usage is not None:
            self.update_last_daily_usage(postfix, last_daily_date, last_daily_usage)
        if yearly_usage is not None:
            self.update_yearly_data(postfix, yearly_usage, usage=True)
        if yearly_charge is not None:
            self.update_yearly_data(postfix, yearly_charge)
        if month_usage is not None:
            self.update_month_data(postfix, month_usage, usage=True)
        if month_charge is not None:
            self.update_month_data(postfix, month_charge)

        self._update_tou_sensors(user_id, postfix, tou_data)

        if step_data:
            self._update_step_sensors(postfix, step_data)

        if enhanced_balance and enhanced_balance.get("amount_due") is not None:
            self.update_prepay_balance(postfix, enhanced_balance["amount_due"])

        logging.info(f"[{user_id}] Home Assistant 数据更新完成")

    def _get_cache_file(self):
        from const import get_data_dir
        return os.path.join(get_data_dir(), 'sgcc_cache.json')

    def _save_to_cache(self, user_id, balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage, tou_data=None, enhanced_balance=None, step_data=None, user_name=""):
        cache_file = self._get_cache_file()
        abs_cache_file = os.path.abspath(cache_file)
        data = {}
        try:
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    data = json.load(f)
        except Exception as e:
            logging.warning(f"加载缓存文件失败: {e}")

        cache_entry = {
            "balance": balance,
            "last_daily_date": last_daily_date,
            "last_daily_usage": last_daily_usage,
            "yearly_charge": yearly_charge,
            "yearly_usage": yearly_usage,
            "month_charge": month_charge,
            "month_usage": month_usage,
            "user_name": user_name or "",
            "timestamp": datetime.now().isoformat()
        }

        if tou_data:
            cache_entry["tou_data"] = tou_data
        if enhanced_balance:
            cache_entry["enhanced_balance"] = enhanced_balance
        if step_data:
            cache_entry["step_data"] = step_data

        data[user_id] = cache_entry

        try:
            with open(cache_file, 'w') as f:
                json.dump(data, f, indent=2)
            logging.debug(f"数据已写入缓存: {abs_cache_file}")
        except Exception as e:
            logging.error(f"保存缓存文件失败 {abs_cache_file}: {e}")

    def republish(self):
        cache_file = self._get_cache_file()
        abs_cache_file = os.path.abspath(cache_file)
        if not os.path.exists(cache_file):
            logging.info(f"未找到缓存文件 {abs_cache_file}，跳过恢复")
            return False

        data = {}
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
        except Exception as e:
            logging.error(f"加载缓存文件失败 {abs_cache_file}: {e}")
            return False

        try:
            for user_id, values in data.items():
                logging.info(f"从缓存恢复户号 {user_id} 的数据")
                clean_values = {k: v for k, v in values.items() if k != 'timestamp'}
                self.update_one_userid(user_id, **clean_values, notify=False)
            return True
        except Exception as e:
            logging.error(f"从缓存恢复数据失败: {e}")
            return False

    @staticmethod
    def _current_month_key() -> str:
        """当前自然月 YYYY-MM（当月分时传感器统计周期）。"""
        return datetime.now().strftime("%Y-%m")

    def _query_month_tou_from_db(self, user_id: str, month: str) -> Optional[dict]:
        from db import create_db
        db = create_db()
        if db is None:
            logging.info(f"[{user_id}] 未配置数据库 (DB_TYPE=none)，跳过当月分时传感器更新")
            return None
        if not db.connect_user_db(user_id):
            logging.warning(f"[{user_id}] 数据库连接失败，无法查询当月分时电量")
            return None
        try:
            return db.query_month_tou_from_daily(user_id, month)
        finally:
            db.close_connect()

    def _should_push(self, sensor_name, new_state, check_attributes=None) -> bool:
        skip = os.getenv("HA_SKIP_UNCHANGED", "false").lower() in ("true", "1", "yes")
        if not skip:
            return True
        return self.should_update(sensor_name, new_state, check_attributes)

    def get_sensor_state(self, sensor_name):
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.token,
        }
        url = self.base_url + API_PATH + sensor_name
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logging.warning(f"获取 {sensor_name} 状态失败: {e}")
            return None

    def should_update(self, sensor_name, new_state, check_attributes=None):
        current_state_obj = self.get_sensor_state(sensor_name)
        if not current_state_obj:
            return True

        try:
            current_state = current_state_obj.get('state')
            if current_state in ['unknown', 'unavailable', None]:
                return True

            curr_val = float(current_state)
            new_val = float(new_state)
            if abs(curr_val - new_val) > 0.001:
                return True
        except (ValueError, TypeError):
            return True

        if check_attributes:
            curr_attrs = current_state_obj.get('attributes', {})
            for k, v in check_attributes.items():
                if str(curr_attrs.get(k)) != str(v):
                    return True

        return False

    def update_last_daily_usage(self, postfix: str, last_daily_date: str, sensorState: float):
        base = DAILY_USAGE_SENSOR_NAME
        sensorName = self._sensor_name(base, postfix)

        if not self._should_push(sensorName, sensorState, {"last_reset": last_daily_date}):
            self._log_skip(base, postfix)
            return

        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "last_reset": last_daily_date,
                "unit_of_measurement": "kWh",
                "icon": "mdi:lightning-bolt",
                "device_class": "energy",
                "state_class": "measurement",
                "friendly_name": self._sensor_label(base),
            },
        }

        self.send_url(sensorName, request_body)
        self._log_updated(base, postfix, sensorState, "kWh")

    def update_balance(self, postfix: str, sensorState: float, enhanced_balance: dict = None):
        base = BALANCE_SENSOR_NAME
        sensorName = self._sensor_name(base, postfix)

        if not self._should_push(sensorName, sensorState):
            self._log_skip(base, postfix)
            return

        last_reset = datetime.now().strftime("%Y-%m-%d, %H:%M:%S")
        attributes = {
            "last_reset": last_reset,
            "unit_of_measurement": "CNY",
            "icon": "mdi:cash",
            "device_class": "monetary",
            "state_class": "total",
            "friendly_name": self._sensor_label(base),
        }
        if enhanced_balance:
            if enhanced_balance.get("amount_due") is not None:
                attributes["amount_due"] = enhanced_balance["amount_due"]

        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": attributes,
        }

        self.send_url(sensorName, request_body)
        self._log_updated(base, postfix, sensorState, "元")

    def update_month_data(self, postfix: str, sensorState: float, usage=False):
        base = MONTH_USAGE_SENSOR_NAME if usage else MONTH_CHARGE_SENSOR_NAME
        sensorName = self._sensor_name(base, postfix)
        current_date = datetime.now()
        first_day_of_current_month = current_date.replace(day=1)
        last_day_of_previous_month = first_day_of_current_month - timedelta(days=1)
        last_reset = last_day_of_previous_month.strftime("%Y-%m")

        if not self._should_push(sensorName, sensorState, {"last_reset": last_reset}):
            self._log_skip(base, postfix)
            return

        unit = "kWh" if usage else "元"
        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "last_reset": last_reset,
                "unit_of_measurement": "kWh" if usage else "CNY",
                "icon": "mdi:lightning-bolt" if usage else "mdi:cash",
                "device_class": "energy" if usage else "monetary",
                "state_class": "measurement",
                "friendly_name": self._sensor_label(base),
            },
        }

        self.send_url(sensorName, request_body)
        self._log_updated(base, postfix, sensorState, unit)

    def update_yearly_data(self, postfix: str, sensorState: float, usage=False):
        base = YEARLY_USAGE_SENSOR_NAME if usage else YEARLY_CHARGE_SENSOR_NAME
        sensorName = self._sensor_name(base, postfix)
        if datetime.now().month == 1:
            last_year = datetime.now().year - 1
            last_reset = datetime.now().replace(year=last_year).strftime("%Y")
        else:
            last_reset = datetime.now().strftime("%Y")

        if not self._should_push(sensorName, sensorState, {"last_reset": last_reset}):
            self._log_skip(base, postfix)
            return

        unit = "kWh" if usage else "元"
        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "last_reset": last_reset,
                "unit_of_measurement": "kWh" if usage else "CNY",
                "icon": "mdi:lightning-bolt" if usage else "mdi:cash",
                "device_class": "energy" if usage else "monetary",
                "state_class": "total_increasing",
                "friendly_name": self._sensor_label(base),
            },
        }
        self.send_url(sensorName, request_body)
        self._log_updated(base, postfix, sensorState, unit)

    def _update_tou_sensors(self, user_id: str, postfix: str, tou_data: dict = None):
        """从数据库汇总当前自然月日用电，更新谷/平/峰/尖传感器。"""
        target_month = self._current_month_key()
        last_reset = target_month

        summary = (tou_data or {}).get("month_tou_summary")
        if not summary:
            summary = self._query_month_tou_from_db(user_id, target_month)
        if not summary:
            logging.info(f"[{user_id}] 数据库中无 {target_month} 月日用电分时数据，跳过谷/平/峰/尖更新")
            return

        logging.info(
            f"[{user_id}] {target_month} 月分时（数据库日用电汇总 {summary['day_count']} 天）: "
            f"谷={summary['valley_usage']}, 平={summary['flat_usage']}, "
            f"峰={summary['peak_usage']}, 尖={summary['tip_usage']} kWh"
        )

        tou_fields = [
            ("valley_usage", MONTH_VALLEY_SENSOR_NAME),
            ("flat_usage", MONTH_FLAT_SENSOR_NAME),
            ("peak_usage", MONTH_PEAK_SENSOR_NAME),
            ("tip_usage", MONTH_TIP_SENSOR_NAME),
        ]

        for field_key, sensor_base in tou_fields:
            value = summary.get(field_key, 0) or 0
            sensorName = self._sensor_name(sensor_base, postfix)
            label = self._sensor_label(sensor_base)

            if not self._should_push(sensorName, value, {"last_reset": last_reset}):
                logging.info(f"跳过更新 {label} 【{sensorName}】，状态一致")
                continue

            request_body = {
                "state": value,
                "unique_id": sensorName,
                "attributes": {
                    "last_reset": last_reset,
                    "unit_of_measurement": "kWh",
                    "icon": "mdi:lightning-bolt",
                    "device_class": "energy",
                    "state_class": "measurement",
                    "friendly_name": label,
                },
            }
            self.send_url(sensorName, request_body)
            logging.info(f"{label} 【{sensorName}】 已更新: {value} kWh")

    def _update_step_sensors(self, postfix: str, step_data: dict):
        """更新阶梯用电传感器（仅住宅用户有数据）"""
        year_month = step_data.get("year_month") or datetime.now().strftime("%Y-%m")
        check_attrs = {"year_month": year_month}

        step_fields = [
            ("used_step1", STEP_USED_STEP1_SENSOR_NAME, "kWh"),
            ("remain_step1", STEP_REMAIN_STEP1_SENSOR_NAME, "kWh"),
            ("used_step2", STEP_USED_STEP2_SENSOR_NAME, "kWh"),
            ("remain_step2", STEP_REMAIN_STEP2_SENSOR_NAME, "kWh"),
            ("used_step3", STEP_USED_STEP3_SENSOR_NAME, "kWh"),
            ("total_usage", STEP_TOTAL_USAGE_SENSOR_NAME, "kWh"),
        ]

        for field_key, sensor_base, unit in step_fields:
            if step_data.get(field_key) is None:
                continue
            value = float(step_data.get(field_key) or 0)
            sensorName = self._sensor_name(sensor_base, postfix)
            label = self._sensor_label(sensor_base)
            if not self._should_push(sensorName, value, check_attrs):
                logging.info(f"跳过更新 {label} 【{sensorName}】，状态一致")
                continue
            request_body = {
                "state": value,
                "unique_id": sensorName,
                "attributes": {
                    "year_month": year_month,
                    "unit_of_measurement": "kWh",
                    "icon": "mdi:stairs",
                    "device_class": "energy",
                    "state_class": "measurement",
                    "friendly_name": label,
                },
            }
            self.send_url(sensorName, request_body)
            logging.info(f"{label} 【{sensorName}】 已更新: {value} {unit}")

        if step_data.get("step_stage") is not None:
            stage = int(step_data.get("step_stage") or 1)
            sensor_base = STEP_STAGE_SENSOR_NAME
            sensorName = self._sensor_name(sensor_base, postfix)
            label = self._sensor_label(sensor_base)
            if not self._should_push(sensorName, stage, check_attrs):
                logging.info(f"跳过更新 {label} 【{sensorName}】，状态一致")
            else:
                request_body = {
                    "state": stage,
                    "unique_id": sensorName,
                    "attributes": {
                        "year_month": year_month,
                        "icon": "mdi:stairs",
                        "state_class": "measurement",
                        "friendly_name": label,
                    },
                }
                self.send_url(sensorName, request_body)
                logging.info(f"{label} 【{sensorName}】 已更新: 第{stage}阶段")

    def update_prepay_balance(self, postfix: str, sensorState: float):
        base = PREPAY_BALANCE_SENSOR_NAME
        sensorName = self._sensor_name(base, postfix)
        if not self._should_push(sensorName, sensorState):
            self._log_skip(base, postfix)
            return
        last_reset = datetime.now().strftime("%Y-%m-%d, %H:%M:%S")
        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "last_reset": last_reset,
                "unit_of_measurement": "CNY",
                "icon": "mdi:cash-check",
                "device_class": "monetary",
                "state_class": "total",
                "friendly_name": self._sensor_label(base),
            },
        }
        self.send_url(sensorName, request_body)
        self._log_updated(base, postfix, sensorState, "元")

    def send_url(self, sensorName, request_body):
        headers = {
            "Content-Type": "application-json",
            "Authorization": "Bearer " + self.token,
        }
        url = self.base_url + API_PATH + sensorName
        try:
            response = requests.post(url, verify=False, json=request_body, headers=headers)
            logging.debug(
                f"Home Assistant REST API POST {url} 响应 [{response.status_code}]: {response.content}"
            )
        except Exception as e:
            logging.error(f"Home Assistant REST API 调用失败: {e}")
