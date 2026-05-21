"""本地单次抓取测试（不进入定时循环）"""
import os
import sys
import logging

if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from const import load_project_env, get_data_dir
from error_watcher import ErrorWatcher

load_project_env()

ErrorWatcher.init(root_dir=os.path.join(get_data_dir(), "errors"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)-8s] ---- %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)

from data_fetcher import DataFetcher

phone = os.getenv("PHONE_NUMBER")
password = os.getenv("PASSWORD")
logging.info(
    "开始本地测试抓取, 登录方式=%s, DB=%s",
    os.getenv("LOGIN_METHOD"),
    os.getenv("DB_TYPE"),
)
fetcher = DataFetcher(phone, password)
fetcher.fetch()
logging.info("本地测试抓取完成")
