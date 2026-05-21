#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH=/data/options.json

json_get() {
  python3 - "$1" "$2" <<'PY'
import json
import sys
from pathlib import Path

key = sys.argv[1]
default = sys.argv[2]
path = Path("/data/options.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    data = {}
value = data.get(key, default)
if isinstance(value, bool):
    print("true" if value else "false")
elif value is None:
    print("")
else:
    print(value)
PY
}

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Add-on options not found at ${CONFIG_PATH}" >&2
  exit 1
fi

export PYTHON_IN_DOCKER="PYTHON_IN_DOCKER"
export PHONE_NUMBER="$(json_get phone_number "")"
export PASSWORD="$(json_get password "")"
export IGNORE_USER_ID="$(json_get ignore_user_id "")"
export DB_TYPE="$(json_get db_type none)"
export HASS_URL="$(json_get hass_url http://homeassistant:8123/)"
export HASS_TOKEN="$(json_get hass_token "")"
export JOB_START_TIME="$(json_get job_start_time 07:00)"
export RUN_ON_STARTUP="$(json_get run_on_startup false)"
export RETRY_WAIT_TIME_OFFSET_UNIT="$(json_get retry_wait_time_offset_unit 10)"
export DATA_RETENTION_DAYS="$(json_get data_retention_days 365)"
export DAILY_FETCH_DAYS="$(json_get daily_fetch_days 7)"
export LOGIN_FALLBACK="$(json_get login_fallback qrcode)"
export PUSH_TYPE="$(json_get push_type none)"
export BALANCE="$(json_get balance 100)"
export PUSHPLUS_TOKEN="$(json_get pushplus_token "")"
export PUSH_URL="$(json_get push_url "")"
export PUSH_QRCODE_URL="$(json_get push_qrcode_url "")"

echo "========================================="
echo " 国家电网电费数据获取 Add-on"
echo "========================================="
echo "账号: ${PHONE_NUMBER}"
echo "HA地址: ${HASS_URL}"
echo "任务开始时间: ${JOB_START_TIME}"
echo "启动立即抓取: ${RUN_ON_STARTUP}"
echo "数据保留天数: ${DATA_RETENTION_DAYS}"
echo "每日获取天数: ${DAILY_FETCH_DAYS}"
echo "数据库类型: ${DB_TYPE}"
echo "========================================="

cd /app
exec python3 main.py
