import logging
import os
import re
import shutil
import json
import time

import random
import base64
from datetime import datetime
from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.edge.service import Service as EdgeService
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from sensor_updator import SensorUpdator
from error_watcher import ErrorWatcher
from typing import Optional

from const import *

from io import BytesIO
from PIL import Image
from captcha_solver.tencent import TencentCaptchaHandler
from fetchers import vue_state
import platform
import numpy as np


class DataFetcher:

    def __init__(self, username: str, password: str):
        if 'PYTHON_IN_DOCKER' not in os.environ:
            from const import load_project_env
            load_project_env()
        self._username = username
        self._password = password

        self.tencent_captcha = TencentCaptchaHandler()
        self._captcha_solver = os.getenv("CAPTCHA_SOLVER", "local").lower()
        if self._captcha_solver not in ("local", "llm"):
            logging.warning("CAPTCHA_SOLVER 无效值 '%s'，回退为 local", self._captcha_solver)
            self._captcha_solver = "local"
        logging.info("验证码识别模式: %s", "LLM 大模型" if self._captcha_solver == "llm" else "本地 OCR/图像匹配")

        self.DRIVER_IMPLICITY_WAIT_TIME = int(os.getenv("DRIVER_IMPLICITY_WAIT_TIME", 60))
        self.RETRY_TIMES_LIMIT = int(os.getenv("RETRY_TIMES_LIMIT", 5))
        self.LOGIN_EXPECTED_TIME = int(os.getenv("LOGIN_EXPECTED_TIME", 10))
        self.RETRY_WAIT_TIME_OFFSET_UNIT = int(os.getenv("RETRY_WAIT_TIME_OFFSET_UNIT", 10))
        self.IGNORE_USER_ID = [uid.strip() for uid in os.getenv("IGNORE_USER_ID", "xxxxx,xxxxx").split(",") if uid.strip()]
        self.QR_CODE_LOGIN_WAIT_COUNT = int(os.getenv("QR_CODE_LOGIN_WAIT_COUNT", 7))
        self.QR_CODE_LOGIN_WAIT_TIME_INTERVAL_UNIT = int(os.getenv("QR_CODE_LOGIN_WAIT_TIME_INTERVAL_UNIT", 10))
        # 用户名映射: 由 _get_user_ids 从网站下拉框自动填充
        self._user_name_map = {}
        # 本地运行用更短的步骤等待
        self._step_wait = 2 if 'PYTHON_IN_DOCKER' not in os.environ else self.RETRY_WAIT_TIME_OFFSET_UNIT
        logging.info(f"数据抓取器初始化完成: 用户={username}, 步骤等待={self._step_wait}s, "
                     f"隐式等待={self.DRIVER_IMPLICITY_WAIT_TIME}s, 重试次数={self.RETRY_TIMES_LIMIT}")
        self._init_db()
    
    def _init_db(self):
        self.db_type = os.getenv("DB_TYPE", "sqlite").lower()
        if self.db_type == 'mysql':
            from db import MysqlDB
            self.db = MysqlDB()
            logging.info("使用 MySQL 数据库存储数据")
        elif self.db_type == 'sqlite':
            from db import SqliteDB
            self.db = SqliteDB()
            logging.info("使用 SQLite 数据库存储数据")
        else:
            self.db = None
            logging.info("未配置数据库，不存储数据")

    # @staticmethod
    def _click_button(self, driver, button_search_type, button_search_key):
        '''wrapped click function, click only when the element is clickable'''
        click_element = driver.find_element(button_search_type, button_search_key)
        # logging.info(f"click_element:{button_search_key}.is_displayed() = {click_element.is_displayed()}\r")
        # logging.info(f"click_element:{button_search_key}.is_enabled() = {click_element.is_enabled()}\r")
        WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.element_to_be_clickable(click_element))
        driver.execute_script("arguments[0].click();", click_element)

    def _wait_for_post_login_state(self, driver, timeout=12) -> str:
        """Wait after password submit and return the detected state."""
        try:
            WebDriverWait(driver, timeout).until(
                lambda d: d.current_url != LOGIN_URL
                or self.tencent_captcha.has_captcha(d)
                or bool(self._get_error_message(d, "//div[@class='errmsg-tip']//span"))
            )
        except Exception:
            pass

        if driver.current_url != LOGIN_URL:
            return "success"
        if self.tencent_captcha.has_captcha(driver):
            return "captcha"
        if self._get_error_message(driver, "//div[@class='errmsg-tip']//span"):
            return "error"
        return "unknown"

    # Stealth JS: 完整覆盖自动化检测特征
    _STEALTH_JS = """
    // navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

    # languages & platform（运行时由 _get_stealth_js 注入）
    Object.defineProperty(navigator, 'languages', {get: () => __NAV_LANGUAGES__});
    Object.defineProperty(navigator, 'platform', {get: () => '__NAV_PLATFORM__'});

    // plugins (模拟有真实插件)
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const arr = [
                {name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',description:'Portable Document Format',
                 length:1,0:{type:'application/x-google-chrome-pdf',suffixes:'pdf'}},
                {name:'Chrome PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai',description:'',
                 length:1,0:{type:'application/pdf',suffixes:'pdf'}},
                {name:'Native Client',filename:'internal-nacl-plugin',description:'',
                 length:2,0:{type:'application/x-nacl',suffixes:''},1:{type:'application/x-pnacl',suffixes:''}}
            ];
            arr.item = i => arr[i];
            arr.namedItem = n => arr.find(p => p.name === n);
            arr.refresh = () => {};
            return arr;
        }
    });

    // mimeTypes
    Object.defineProperty(navigator, 'mimeTypes', {
        get: () => {
            const arr = [
                {type:'application/pdf',suffixes:'pdf',description:'Portable Document Format',
                 enabledPlugin:{name:'Chrome PDF Plugin'}},
                {type:'application/x-google-chrome-pdf',suffixes:'pdf',description:'Portable Document Format',
                 enabledPlugin:{name:'Chrome PDF Plugin'}}
            ];
            arr.item = i => arr[i];
            arr.namedItem = n => arr.find(m => m.type === n);
            return arr;
        }
    });

    // chrome runtime (模拟真实 Chrome 对象)
    if (!window.chrome) window.chrome = {};
    if (!window.chrome.runtime) {
        window.chrome.runtime = {
            connect: function(){return {onMessage:{addListener:function(){}},postMessage:function(){},disconnect:function(){}}},
            sendMessage: function(){},
            onMessage: {addListener:function(){}},
            id: undefined
        };
    }
    window.chrome.csi = function(){};
    window.chrome.loadTimes = function(){return {commitLoadTime:Date.now()/1000,requestTime:Date.now()/1000}};

    // 修复 iframe contentWindow 检测
    const originalContentWindow = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype,'contentWindow');
    Object.defineProperty(HTMLIFrameElement.prototype,'contentWindow',{
        get: function(){
            const result = originalContentWindow.get.call(this);
            if (result) {
                try {
                    Object.defineProperty(result.navigator,'webdriver',{get:()=>undefined});
                } catch(e){}
            }
            return result;
        }
    });

    // permissions query (Notification.permission 检测)
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = function(params){
        if (params.name === 'notifications') {
            return Promise.resolve({state: Notification.permission});
        }
        return originalQuery.call(this, params);
    };

    // WebGL vendor & renderer (避免暴露 SwiftShader)
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param){
        if (param === 37445) return 'Google Inc. (Intel)';
        if (param === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 630, OpenGL 4.5)';
        return getParameter.call(this, param);
    };
    """

    @staticmethod
    def _get_stealth_js() -> str:
        """按运行环境生成 CDP 反检测脚本，避免 Linux Docker 伪装成 Win32。"""
        if platform.system() == "Windows":
            nav_platform = "Win32"
            languages = ["zh-CN", "zh", "en-US", "en"]
        else:
            nav_platform = os.getenv("BROWSER_NAV_PLATFORM", "Linux x86_64")
            lang_env = os.getenv("BROWSER_LANGUAGE", "zh-CN,zh,en-US,en")
            languages = [part.strip() for part in lang_env.split(",") if part.strip()]
        langs_json = json.dumps(languages, ensure_ascii=False)
        return DataFetcher._STEALTH_JS.replace("__NAV_LANGUAGES__", langs_json).replace(
            "__NAV_PLATFORM__", nav_platform
        )

    @staticmethod
    def _inject_stealth_cdp(driver) -> None:
        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": DataFetcher._get_stealth_js()},
            )
            logging.info("已注入反 webdriver 检测脚本 (CDP)")
        except Exception as exc:
            logging.warning("CDP 注入失败 (非致命): %s", exc)

    @staticmethod
    def _human_delay(min_s=0.3, max_s=1.2):
        """生成随机人类行为延迟"""
        time.sleep(random.uniform(min_s, max_s))

    @staticmethod
    def _human_type(element, text, min_delay=0.05, max_delay=0.15):
        """模拟人类逐字输入"""
        for char in text:
            element.send_keys(char)
            time.sleep(random.uniform(min_delay, max_delay))

    @staticmethod
    def _find_chromedriver() -> ChromeService:
        """Linux 桌面环境查找 chromedriver（含 CloakBrowser 缓存）。"""
        path = shutil.which("chromedriver") or shutil.which("chromedriver.exe")
        if path:
            return ChromeService(executable_path=path)

        for base in [
            os.path.expanduser("~/.cloakbrowser"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), ".cloakbrowser"),
        ]:
            if not base or not os.path.isdir(base):
                continue
            try:
                for root, dirs, files in os.walk(base):
                    fname = "chromedriver.exe" if "chromedriver.exe" in files else (
                        "chromedriver" if "chromedriver" in files else None
                    )
                    if fname:
                        candidate = os.path.join(root, fname)
                        if os.path.isfile(candidate):
                            return ChromeService(executable_path=candidate)
                    if root.count(os.sep) - base.count(os.sep) > 2:
                        dirs.clear()
            except Exception:
                pass

        try:
            return ChromeService()
        except Exception:
            pass
        raise RuntimeError("ChromeDriver 未找到，请安装 chromedriver 或 chromium-driver")

    @staticmethod
    def _build_chrome_options(in_docker: bool) -> tuple:
        """构建 Chrome 反检测参数（对齐 upstream 精华 + 保留 CDP 伪装）。"""
        browser_window_size = os.getenv("BROWSER_WINDOW_SIZE", "1158,848")
        browser_language = os.getenv("BROWSER_LANGUAGE", "zh-HK,zh,en-US,en,zh-CN")
        browser_ua = os.getenv("BROWSER_USER_AGENT", "").strip()
        browser_device_scale_factor = os.getenv("BROWSER_DEVICE_SCALE_FACTOR", "2")
        browser_language_primary = browser_language.split(",")[0].strip()

        if in_docker and not browser_ua:
            browser_ua = (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
            )

        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument(f"--window-size={browser_window_size}")
        chrome_options.add_argument(f"--lang={browser_language_primary}")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)
        if browser_ua:
            chrome_options.add_argument(f"user-agent={browser_ua}")
        chrome_options.add_argument(f"--force-device-scale-factor={browser_device_scale_factor}")
        chrome_options.add_argument("--high-dpi-support=1")
        chrome_options.add_experimental_option(
            "prefs",
            {
                "intl.accept_languages": browser_language,
                "credentials_enable_service": False,
                "profile.password_manager_enabled": False,
            },
        )
        return chrome_options, browser_language_primary

    def _get_webdriver(self):
        logging.info(f"正在初始化 WebDriver, 平台: {platform.system()}")
        if platform.system() == 'Windows':
            from selenium.webdriver.edge.options import Options as EdgeOptions
            edge_options = EdgeOptions()
            edge_options.add_argument("--start-maximized")
            edge_options.add_argument("--disable-blink-features=AutomationControlled")
            edge_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            edge_options.add_experimental_option("useAutomationExtension", False)
            edge_options.add_argument(
                "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0")
            logging.info("使用 Edge 浏览器 (Windows 模式)")
            driver = webdriver.Edge(
                service=EdgeService(EdgeChromiumDriverManager(
                    url="https://msedgedriver.microsoft.com/",
                    latest_release_url="https://msedgedriver.microsoft.com/LATEST_RELEASE"
                ).install()),
                options=edge_options
            )
            driver.implicitly_wait(self.DRIVER_IMPLICITY_WAIT_TIME)
            self._inject_stealth_cdp(driver)
        else:
            in_docker = "PYTHON_IN_DOCKER" in os.environ
            chrome_options, _ = self._build_chrome_options(in_docker)
            if in_docker:
                chrome_options.add_argument("--headless=new")
                chrome_options.binary_location = "/usr/bin/chromium"
                service = ChromeService(executable_path="/usr/bin/chromedriver")
                logging.info("使用 Chromium 浏览器 (Docker headless 模式)")
            else:
                service = self._find_chromedriver()
                logging.info("使用 Chrome 浏览器 (Linux 桌面模式)")

            driver = webdriver.Chrome(options=chrome_options, service=service)
            driver.implicitly_wait(self.DRIVER_IMPLICITY_WAIT_TIME)
            self._inject_stealth_cdp(driver)

        logging.info("WebDriver 初始化完成")
        return driver

    def _solve_captcha_local(self, driver) -> bool:
        """本地 OCR/图像匹配解算点选验证码。"""
        self.tencent_captcha.wait_for_captcha(driver, timeout=15)
        captcha_info = self.tencent_captcha.get_info(driver)
        _mode_label = {"point_click": "点选", "slider": "滑块", "unknown": "未知"}
        logging.info(
            "本地验证码检测: 类型=%s, 提示=%s",
            _mode_label.get(captcha_info.get("mode"), captcha_info.get("mode")),
            captcha_info.get("prompt", ""),
        )

        if captcha_info.get("mode") not in ("point_click", "unknown"):
            logging.error("当前验证码非点选类型: %s", captcha_info.get("mode"))
            return False

        for retry_times in range(1, self.RETRY_TIMES_LIMIT + 1):
            logging.info("开始第 %s 次本地点选验证码识别...", retry_times)
            if self.tencent_captcha.solve_point_click_captcha(driver, self.DRIVER_IMPLICITY_WAIT_TIME):
                if driver.current_url != LOGIN_URL or not self.tencent_captcha.has_captcha(driver):
                    return True
            logging.info("第 %s 次本地点选验证码识别失败, 正在刷新...", retry_times)
            self.tencent_captcha.refresh_captcha(driver)
            self._human_delay(1.0, 2.5)
        return False

    def _solve_captcha(self, driver) -> bool:
        """根据 CAPTCHA_SOLVER 环境变量选择 LLM 或本地识别。"""
        if self._captcha_solver == "llm":
            if not os.getenv("ARK_API_KEY", "").strip():
                logging.error("CAPTCHA_SOLVER=llm 但未配置 ARK_API_KEY")
                return False
            logging.info("使用 LLM 大模型识别验证码")
            from captcha_solver.browser_llm import solve_captcha_in_browser
            try:
                return solve_captcha_in_browser(
                    driver,
                    max_retries=self.RETRY_TIMES_LIMIT,
                    timeout=min(15, self.DRIVER_IMPLICITY_WAIT_TIME),
                )
            except Exception as exc:
                logging.error("LLM 验证码识别失败: %s", exc)
                return False
        return self._solve_captcha_local(driver)

    @ErrorWatcher.watch
    def _login(self, driver, phone_code = False):
        logging.info(f"开始登录流程, 账号: {self._username}, 手机验证码模式: {'是' if phone_code else '否'}")
        try:
            driver.get(LOGIN_URL)
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME * 3).until(EC.visibility_of_element_located((By.CLASS_NAME, "user")))
        except:
            logging.debug(f"打开登录页面失败: {LOGIN_URL}")
        logging.info(f"已打开登录页面: {LOGIN_URL}")
        time.sleep(self._step_wait * 2)
        # swtich to username-password login page
        # 临时关闭隐式等待，避免与 WebDriverWait 叠加导致超时
        driver.implicitly_wait(0)
        try:
            WebDriverWait(driver, 10).until(
                EC.invisibility_of_element_located((By.CLASS_NAME, 'el-loading-mask')))
        finally:
            driver.implicitly_wait(self.DRIVER_IMPLICITY_WAIT_TIME)  # 恢复隐式等待

        element = WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(
            EC.presence_of_element_located((By.CLASS_NAME, 'user')))
        driver.execute_script("arguments[0].click();", element)
        logging.info("点击「账号密码登录」切换")
        self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[2]/span')
        time.sleep(self._step_wait)
        # click agree button
        self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[1]/form/div[1]/div[3]/div/span[2]')
        logging.info("已勾选「同意」协议")
        time.sleep(self._step_wait)
        if phone_code:
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[3]/span')
            input_elements = driver.find_elements(By.CLASS_NAME, "el-input__inner")
            input_elements[2].send_keys(self._username)
            logging.info(f"已输入手机号: {self._username}")
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[2]/form/div[1]/div[2]/div[2]/div/a')
            code = input("Input your phone verification code: ")
            input_elements[3].send_keys(code)
            logging.info(f"已输入手机验证码: {code}")
            # click login button
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[2]/form/div[2]/div/button/span')
            time.sleep(self._step_wait * 2)
            logging.info("已点击登录按钮")

            return True
        # 增加判空校验便于测试fallback
        elif self._password is not None and len(self._password) > 0:
            # input username and password (模拟人类逐字输入)
            input_elements = driver.find_elements(By.CLASS_NAME, "el-input__inner")
            self._human_delay(0.5, 1.0)
            self._human_type(input_elements[0], self._username)
            self._human_delay(0.3, 0.8)
            self._human_type(input_elements[1], self._password)
            logging.info(f"已输入账号密码, 账号: {self._username}")

            rk001_backoff = 60  # RK001 首次退避等待秒数
            for login_attempt in range(1, self.RETRY_TIMES_LIMIT + 1):
                # click login button
                self._human_delay(0.5, 1.5)
                self._click_button(driver, By.CLASS_NAME, "el-button.el-button--primary")
                time.sleep(self._step_wait * 2)
                logging.info(f"已点击登录按钮 (第 {login_attempt}/{self.RETRY_TIMES_LIMIT} 次)")

                # Wait for post-login state: success, captcha, or error
                post_login_state = self._wait_for_post_login_state(driver)
                _state_label = {"success": "成功", "captcha": "验证码", "error": "错误", "timeout": "超时"}
                logging.info(f"登录后页面状态: {_state_label.get(post_login_state, post_login_state)}")

                if post_login_state == "success":
                    logging.info("密码登录成功!")
                    return True

                if post_login_state == "captcha":
                    if self._solve_captcha(driver):
                        time.sleep(self._step_wait)
                        if driver.current_url != LOGIN_URL:
                            logging.info("验证码识别成功, 已通过验证!")
                            return True
                    logging.error("验证码识别多次失败, 尝试备选登录方案")
                    return self._fallback_login(driver)
                elif post_login_state == "error":
                    error = self._get_error_message(driver, "//div[@class='errmsg-tip']//span")
                    logging.info(f"登录错误信息: {error}")
                    # RK001 (网络连接超时) or similar transient errors: backoff and retry
                    if "RK001" in (error or "") or "超时" in (error or "") or "重试" in (error or ""):
                        logging.warning(f"检测到风控错误 [{error}], 退避等待 {rk001_backoff}s 后重试 ({login_attempt}/{self.RETRY_TIMES_LIMIT})...")
                        time.sleep(rk001_backoff)
                        rk001_backoff = min(rk001_backoff * 2, 300)  # 指数退避, 最大 5 分钟
                        # 刷新页面重新登录
                        try:
                            driver.get(LOGIN_URL)
                            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME * 2).until(
                                EC.visibility_of_element_located((By.CLASS_NAME, "user")))
                            self._human_delay(1.0, 2.0)
                            # 重新切换到账号密码登录
                            element = WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(
                                EC.presence_of_element_located((By.CLASS_NAME, 'user')))
                            driver.execute_script("arguments[0].click();", element)
                            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[2]/span')
                            self._human_delay(0.5, 1.0)
                            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[1]/form/div[1]/div[3]/div/span[2]')
                            self._human_delay(0.5, 1.0)
                            input_elements = driver.find_elements(By.CLASS_NAME, "el-input__inner")
                            self._human_type(input_elements[0], self._username)
                            self._human_delay(0.3, 0.8)
                            self._human_type(input_elements[1], self._password)
                        except Exception:
                            pass
                        continue

            return self._fallback_login(driver)

    def _get_error_message(self, driver, path) -> Optional[str]:
        """获取错误信息，如果不存在则返回 None"""
        # 关闭隐式等待
        driver.implicitly_wait(0)
        try:
            element = driver.find_element(By.XPATH, path)
            return element.text
        except Exception:
            return None
        finally:
            driver.implicitly_wait(self.DRIVER_IMPLICITY_WAIT_TIME)  # 恢复隐式等待

    def _fallback_login(self, driver) -> bool:
        """使用 fallback 登录"""
        fallback = os.getenv("LOGIN_FALLBACK", "").strip().lower()
        if fallback == "qrcode":
            logging.info("密码登录失败，切换为扫码登录 (LOGIN_FALLBACK=qrcode)")
            return self._qr_login(driver, reason="密码登录失败，已切换扫码登录")
        return False

    def _qr_login(self, driver, reason: str = "扫码登录") -> bool:
        logging.info("切换到二维码登录模式")
        # 尝试切换到二维码标签（如果已经在二维码页面则跳过）
        try:
            qr_tab = driver.find_element(By.CLASS_NAME, 'qr_code')
            if qr_tab.is_displayed():
                driver.execute_script("arguments[0].click();", qr_tab)
                logging.info("已切换到二维码登录标签")
        except Exception:
            logging.info("当前已在二维码登录页面或无需切换")

        time.sleep(self._step_wait)
        # 获取登录二维码
        qrElement = WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(
            EC.visibility_of_element_located((By.XPATH, "//div[@class='sweepCodePic']//img")))
        logging.info("找到二维码图片元素")
        img_src = qrElement.get_attribute('src')

        if img_src.startswith('data:image'):
            base64_data = img_src.split(',')[1]
            img_screenshot = base64.b64decode(base64_data)
        else:
          logging.info('二维码图片非 base64 格式, 使用截图方式获取')
          img_screenshot = qrElement.screenshot_as_png

        from const import get_data_dir
        qr_path = os.path.join(get_data_dir(), 'login_qr_code.png')
        with open(qr_path, "wb") as f:
            f.write(img_screenshot)
            logging.info(f"二维码已保存到 {qr_path}, 请扫描登录")

        try:
            from notify import push_login_qrcode
            if push_login_qrcode(img_screenshot, reason=reason):
                logging.info("登录二维码已推送到通知渠道")
            else:
                logging.warning(
                    "登录二维码推送失败或未配置渠道，请检查 WEWORK_WEBHOOK_URL 或 PUSH_QRCODE_URL"
                )
        except Exception as e:
            logging.warning(f"二维码推送失败 (不影响扫码登录): {e}")
        logging.info(f"等待扫码登录, 最长等待 {self.QR_CODE_LOGIN_WAIT_COUNT * self.QR_CODE_LOGIN_WAIT_TIME_INTERVAL_UNIT} 秒...")
        for i in range(1, self.QR_CODE_LOGIN_WAIT_COUNT + 1):
            logging.info(f'等待扫码... [{i}/{self.QR_CODE_LOGIN_WAIT_COUNT}] (每 {self.QR_CODE_LOGIN_WAIT_TIME_INTERVAL_UNIT}s 检查一次)')
            time.sleep(self.QR_CODE_LOGIN_WAIT_TIME_INTERVAL_UNIT)
            if (driver.current_url != LOGIN_URL):
                logging.info("扫码登录成功!")
                return True
            else:
                error = self._get_error_message(driver, "//div[@class='sweepCodePic']//div[@class='erwBg']//p")
                if error is not None:
                    logging.error(f'二维码登录失败: {error}')
                    return False

        logging.warning("扫码登录超时, 未在规定时间内完成扫码")

        return False
        
    def fetch(self):

        """main logic here"""

        driver = self._get_webdriver()
        ErrorWatcher.instance().set_driver(driver)
        
        driver.maximize_window() 
        time.sleep(self._step_wait)
        logging.info("WebDriver 已就绪")
        updator = SensorUpdator()
        
        try:
            login_method = os.getenv("LOGIN_METHOD", "password").lower()
            if login_method == "qrcode":
                # 直接扫码登录模式
                logging.info("登录方式: 扫码登录")
                driver.get(LOGIN_URL)
                WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME * 3).until(
                    EC.visibility_of_element_located((By.CLASS_NAME, "user")))
                time.sleep(self._step_wait)
                if self._qr_login(driver, reason="直接扫码登录"):
                    logging.info("扫码登录成功!")
                else:
                    raise Exception("扫码登录失败")
            elif os.getenv("DEBUG_MODE", "false").lower() == "true":
                if self._login(driver,phone_code=True):
                    logging.info("登录成功")
                else:
                    logging.info("登录失败")
                    raise Exception("登录失败")
            else:
                if self._login(driver):
                    logging.info("登录成功")
                else:
                    logging.info("登录失败")
                    raise Exception("登录失败")
        except Exception as e:
            logging.error(
                f"浏览器异常退出: {e}，剩余重试次数 {self.RETRY_TIMES_LIMIT}")
            driver.quit()
            return

        logging.info(f"登录成功! 当前页面: {driver.current_url}")
        time.sleep(self._step_wait)
        # 导航到电费余额页面（某些版本有 el-dropdown 用户切换）
        driver.get(BALANCE_URL)
        time.sleep(self._step_wait)
        logging.info("正在获取用户 ID 列表...")
        user_id_list = self._get_user_ids(driver)
        if not user_id_list:
            logging.error("获取用户 ID 列表失败")
            driver.quit()
            return
        logging.info(f"共获取到 {len(user_id_list)} 个用户: {user_id_list}, 其中 {self.IGNORE_USER_ID} 将被忽略")
        time.sleep(self._step_wait)

        fetch_results = []

        for userid_index, user_id in enumerate(user_id_list):           
            logging.info(f"===== 开始处理第 {userid_index + 1}/{len(user_id_list)} 个用户: {user_id} =====")
            try:
                if user_id in self.IGNORE_USER_ID:
                    logging.info(f"用户 {user_id} 在忽略列表中, 跳过")
                    continue

                driver.get(ELECTRIC_USAGE_URL)
                time.sleep(self._step_wait)
                logging.info(f"正在切换到用户 [{user_id}]...")
                if not self._switch_to_user(driver, user_id, userid_index):
                    logging.warning(f"用户 [{user_id}] 切换失败, 跳过")
                    continue

                current_userid = self._get_current_userid(driver)
                logging.info(f"当前用户: {current_userid}, 开始获取用电数据...")
                driver.get(BALANCE_URL)
                time.sleep(self._step_wait)
                balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage, tou_data, enhanced_balance, step_data, last_month_period = self._get_all_data(driver, user_id, userid_index)
                logging.info(f"用户 [{user_id}] 数据获取完成: 余额={balance}CNY, 最近日用电={last_daily_usage}kWh({last_daily_date}), "
                             f"年度用电={yearly_usage}kWh, 年度电费={yearly_charge}CNY, 月用电={month_usage}kWh, 月电费={month_charge}CNY")
                updator.update_one_userid(
                    user_id, balance, last_daily_date, last_daily_usage,
                    yearly_charge, yearly_usage, month_charge, month_usage,
                    tou_data=tou_data, enhanced_balance=enhanced_balance,
                    step_data=step_data,
                    user_name=self._user_name_map.get(user_id, ""),
                )
                fetch_results.append({
                    "user_id": user_id,
                    "user_name": self._user_name_map.get(user_id, user_id),
                    "balance": balance,
                    "last_daily_date": last_daily_date,
                    "last_daily_usage": last_daily_usage,
                    "yearly_charge": yearly_charge,
                    "yearly_usage": yearly_usage,
                    "month_charge": month_charge,
                    "month_usage": month_usage,
                    "last_month_period": last_month_period,
                    "tou_data": tou_data,
                    "enhanced_balance": enhanced_balance,
                })
                time.sleep(self._step_wait)
            except Exception as e:
                if (userid_index != len(user_id_list)):
                    logging.info(f"用户 {user_id} 数据获取失败: {e}, 继续处理下一个用户")
                else:
                    logging.info(f"用户 {user_id} 数据获取失败: {e}")
                    logging.info("数据获取完成后关闭浏览器")
                continue

        logging.info("所有用户数据处理完成, 关闭浏览器")
        try:
            from notify import push_fetch_summary
            push_fetch_summary(fetch_results)
        except Exception as exc:
            logging.warning("数据汇总推送失败 (非致命): %s", exc)
        driver.quit()


    def _get_current_userid(self, driver) -> str:
        """读取当前页面的用户户号（兼容多种页面布局，不使用隐式等待阻塞）"""
        # 方式一：从页面源码正则匹配（最快，不等待 DOM）
        try:
            page_source = driver.page_source or ""
            match = re.search(r"用电户号[:：\s]*([0-9]{13})", page_source)
            if match:
                return match.group(1)
            ids = re.findall(r"\b(\d{13})\b", page_source)
            if ids:
                return ids[0]
        except Exception:
            pass
        # 方式二：从 houseNum 区域读取（阶梯页/用电量页）
        try:
            for span in driver.find_elements(By.CSS_SELECTOR, ".houseNum li.righ span, .houseNum .righ span"):
                matches = re.findall(r"\b\d{13}\b", span.text or "")
                if matches:
                    return matches[-1]
        except Exception:
            pass
        # 方式三：从"用电户号"标签中读取
        try:
            for label in driver.find_elements(By.XPATH, "//*[contains(normalize-space(.), '用电户号')]"):
                matches = re.findall(r"\b\d{13}\b", label.text or "")
                if matches:
                    return matches[-1]
        except Exception:
            pass
        # 方式四：从 el-select 当前值读取
        try:
            for inp in driver.find_elements(By.CSS_SELECTOR, ".houseNum .el-select .el-input__inner"):
                matches = re.findall(r"\b\d{13}\b", inp.get_attribute("value") or inp.text or "")
                if matches:
                    return matches[-1]
        except Exception:
            pass
        return ""

    def _navigate_spa(self, driver, url: str, timeout: int = 15) -> str:
        """通过 SPA 路由或菜单跳转，避免 driver.get 重置当前户号"""
        path = url.replace("https://95598.cn", "").split("?")[0]
        page_key = path.rstrip("/").split("/")[-1]
        script = """
        const path = arguments[0];
        const pageKey = arguments[2];
        const matchText = (el) => ((el.innerText || el.textContent || '') + '').trim();
        const clickMenu = () => {
          const el = Array.from(document.querySelectorAll('a, li, span, strong, div'))
            .find(e => e.offsetParent !== null && /阶梯/.test(matchText(e)));
          if (el) { el.click(); return true; }
          const link = document.querySelector('a[href*="' + pageKey + '"]');
          if (link) { link.click(); return true; }
          return false;
        };
        if (clickMenu()) return 'menu';
        const findRouter = () => {
          const app = document.querySelector('#app');
          if (app && app.__vue__ && app.__vue__.$router) return app.__vue__.$router;
          for (const el of document.querySelectorAll('*')) {
            let vm = el.__vue__;
            while (vm) {
              if (vm.$router) return vm.$router;
              vm = vm.$parent;
            }
          }
          return null;
        };
        const router = findRouter();
        const paths = [
          '/stepElectricityConsumption',
          '/osgweb/stepElectricityConsumption',
          'stepElectricityConsumption',
          path,
        ];
        if (router) {
          for (const p of paths) {
            try {
              router.push(p);
              return 'router:' + p;
            } catch (e) {}
            try {
              router.push({ path: p });
              return 'router-obj:' + p;
            } catch (e) {}
          }
        }
        clickMenu();
        return 'menu-retry';
        """
        try:
            method = driver.execute_script(script, path, url, page_key) or "unknown"
            for i in range(timeout):
                time.sleep(1)
                if page_key in (driver.current_url or ""):
                    logging.info(f"SPA 跳转成功 {path} (方式={method}, 耗时={i + 1}s)")
                    return method
            logging.warning(f"SPA 跳转未生效 (方式={method}), 当前 URL={driver.current_url}")
            return "failed"
        except Exception as e:
            logging.warning(f"SPA 跳转异常: {e}")
            return "error"

    def _set_session_user(self, driver, user_id: str) -> None:
        """尝试将会话中的当前户号写入 storage / Vue state"""
        try:
            driver.execute_script("""
                const uid = arguments[0];
                for (const key of ['consNo', 'cons_no', 'selectedConsNo', 'userNo', 'houseNo']) {
                  try { sessionStorage.setItem(key, uid); } catch (e) {}
                  try { localStorage.setItem(key, uid); } catch (e) {}
                }
                for (const el of document.querySelectorAll('*')) {
                  let vm = el.__vue__;
                  while (vm) {
                    if (vm.consInfoobj && typeof vm.consInfoobj === 'object') vm.consInfoobj.consNo = uid;
                    if (vm.consInfo && typeof vm.consInfo === 'object') vm.consInfo.consNo = uid;
                    vm = vm.$parent;
                  }
                }
            """, user_id)
        except Exception as e:
            logging.debug(f"写入会话户号失败: {e}")

    def _switch_to_user(self, driver, user_id: str, userid_index: int = 0) -> bool:
        """切换到指定户号（与 _get_user_ids 相同的 el-select 方式）"""
        current = self._get_current_userid(driver)
        if current == user_id:
            logging.info(f"用户 [{user_id}] 已是当前选中用户, 无需切换")
            return True

        user_name = self._user_name_map.get(user_id, "")

        try:
            select_inputs = driver.find_elements(By.CSS_SELECTOR, ".houseNum .el-select .el-input__inner")
            if not select_inputs:
                logging.warning("未找到 el-select 用户下拉框")
                return False

            driver.execute_script("arguments[0].click();", select_inputs[0])
            time.sleep(self._step_wait)
            options = driver.find_elements(By.CSS_SELECTOR, ".el-select-dropdown__item")
            # 过滤掉年份选项（如 2024/2025/2026），只保留用户选项
            user_options = [
                o for o in options
                if not re.match(r"^\d{4}$", self._option_text(driver, o).strip())
            ]
            logging.info(f"用户下拉框共 {len(user_options)} 个用户选项")

            for opt in user_options:
                text = self._option_text(driver, opt)
                if user_id in text or user_id in re.findall(r"\b\d{13}\b", text):
                    driver.execute_script("arguments[0].click();", opt)
                    time.sleep(self._step_wait)
                    if self._get_current_userid(driver) == user_id:
                        logging.info(f"已切换到用户 [{user_id}]")
                        return True
                if user_name and user_name in text:
                    driver.execute_script("arguments[0].click();", opt)
                    time.sleep(self._step_wait)
                    if self._get_current_userid(driver) == user_id:
                        logging.info(f"已切换到用户 [{user_id}] ({user_name})")
                        return True

            if 0 <= userid_index < len(user_options):
                driver.execute_script("arguments[0].click();", user_options[userid_index])
                time.sleep(self._step_wait)
                if self._get_current_userid(driver) == user_id:
                    logging.info(f"已通过索引 {userid_index} 切换到用户 [{user_id}]")
                    return True

            texts = [self._option_text(driver, o) for o in user_options]
            logging.warning(f"切换用户 [{user_id}] 失败, 可选: {texts}")
        except Exception as e:
            logging.warning(f"切换用户 [{user_id}] 异常: {e}")
        return False

    def _choose_current_userid(self, driver, userid_index, user_id=None):
        """切换到指定用户，优先按户号匹配，失败则按索引"""
        for btn in driver.find_elements(By.CLASS_NAME, "button_confirm"):
            try:
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(0.5)
                break
            except Exception:
                pass

        if user_id:
            current = self._get_current_userid(driver)
            if current == user_id:
                logging.info(f"用户 [{user_id}] 已是当前选中用户, 无需切换")
                return True

        options = self._open_user_selector_and_get_options(driver)
        if not options:
            current = self._get_current_userid(driver)
            if user_id and current == user_id:
                logging.info(f"用户 [{user_id}] 已是当前选中用户, 无需切换")
                return True
            logging.warning(f"用户 [{user_id}] 切换失败: 未找到下拉选项 (当前户号={current or '未知'})")
            return False

        if user_id:
            for opt in options:
                text = self._option_text(driver, opt)
                if user_id in text or user_id in re.findall(r"\b\d{13}\b", text):
                    driver.execute_script("arguments[0].click();", opt)
                    logging.info(f"已切换到用户 [{user_id}]")
                    time.sleep(self._step_wait)
                    return True

        if 0 <= userid_index < len(options):
            driver.execute_script("arguments[0].click();", options[userid_index])
            logging.info(f"已切换到用户索引 {userid_index}")
            time.sleep(self._step_wait)
            return True

        logging.warning(f"用户 [{user_id}] 切换失败: 索引 {userid_index} 超出范围, 共 {len(options)} 个选项")
        return False

    def _open_user_selector_and_get_options(self, driver):
        """打开用户选择下拉框并返回可见选项"""
        triggers = [
            (By.CSS_SELECTOR, ".houseNum .el-select .el-input__inner"),
            (By.XPATH, "//div[@class='el-dropdown']/span"),
            (By.XPATH, "//span[contains(normalize-space(.), '切换用户')]"),
            (By.XPATH, "//div[contains(@class,'houseNum')]//span[contains(@class,'el-input__suffix')]"),
        ]
        for by, selector in triggers:
            try:
                trigger = WebDriverWait(driver, 2).until(EC.element_to_be_clickable((by, selector)))
                driver.execute_script("arguments[0].click();", trigger)
                time.sleep(1)
                options = self._get_visible_user_options(driver)
                if options:
                    return options
            except Exception:
                continue
        return []

    def _option_text(self, driver, opt):
        text = opt.text.strip()
        if not text:
            text = driver.execute_script(
                "return arguments[0].innerText || arguments[0].textContent || '';", opt
            ).strip()
        return text

    def _get_visible_user_options(self, driver):
        """获取可见的用户下拉选项（兼容 el-dropdown 和 el-select）"""
        return [
            option
            for option in driver.find_elements(
                By.XPATH,
                "//ul[contains(@class,'el-dropdown-menu')]//li"
                " | //div[contains(@class,'el-select-dropdown')]//li"
                " | //li[contains(@class,'el-select-dropdown__item')]",
            )
            if option.is_displayed()
            and "is-disabled" not in (option.get_attribute("class") or "")
            and "disabled" not in (option.get_attribute("class") or "")
        ]
        

    def _get_all_data(self, driver, user_id, userid_index):
        logging.info(f"[{user_id}] 正在获取电费余额...")
        balance = self._get_electric_balance(driver)
        if balance is None:
            logging.error(f"[{user_id}] 获取电费余额失败")
        else:
            logging.info(f"[{user_id}] 电费余额: {balance} 元")

        # 尝试通过 Vue state 获取增强余额和用户名
        enhanced_balance = None
        user_name = self._user_name_map.get(user_id, "")
        if self.db is not None:
            try:
                components = vue_state.selected_vue_data(driver)
                enhanced_balance = vue_state.normalize_balance(components)
                # 如果用户名为空，从 Vue state 补充
                if not user_name:
                    user_info = vue_state.normalize_user_info(components)
                    user_name = user_info.get("user_name", "")
                    if user_name:
                        self._user_name_map[user_id] = user_name
                        logging.info(f"[{user_id}] 从 Vue state 获取用户名: {user_name}")
            except Exception as e:
                logging.warning(f"[{user_id}] 增强余额获取失败: {e}")
        if user_name:
            logging.info(f"[{user_id}] 用户名: {user_name}")

        # 阶梯用电查询（仅住宅用户，余额获取后立即查询）
        step_data = None
        if user_name and '住宅' in user_name:
            step_data = self._get_step_electricity(driver, user_id, user_name, userid_index)
        elif user_name:
            logging.info(f"[{user_id}] 非住宅用户({user_name}), 跳过阶梯用电查询")

        logging.info(f"[{user_id}] 正在切换到用电量页面...")
        driver.get(ELECTRIC_USAGE_URL)
        time.sleep(self._step_wait)
        if not self._switch_to_user(driver, user_id, userid_index):
            logging.warning(f"[{user_id}] 用电量页面用户切换失败, 当前户号={self._get_current_userid(driver) or '未知'}")
        time.sleep(self._step_wait)

        fetch_days = int(os.getenv("DAILY_FETCH_DAYS", 30))
        if fetch_days not in (7, 30):
            fetch_days = 7

        # ---- Vue state 提取年度/月度数据（日用电留给 _get_daily_usage_data 按配置拉取）----
        tou_data = None
        vue_daily_date, vue_daily_usage = None, None
        vue_yearly_usage, vue_yearly_charge = None, None
        vue_months, vue_month_usages, vue_month_charges = None, None, None

        if self.db is not None:
            try:
                components = vue_state.selected_vue_data(driver)
                usage_info = vue_state.normalize_usage(components, fetch_days=fetch_days)
                tou_data = {k: v for k, v in usage_info.items() if k != "daily"}
                tou_data["daily"] = []

                logging.info(f"[{user_id}] [分时电量] 年度={usage_info.get('year')}, "
                             f"已获取 {len(usage_info.get('months', []))} 个月的月度分时数据")

                vue_yearly_usage = usage_info.get("yearly_usage")
                vue_yearly_charge = usage_info.get("yearly_charge")

                months_info = usage_info.get("months", [])
                if months_info:
                    vue_months = [m.get("month", "") for m in months_info]
                    vue_month_usages = [m.get("total_usage") for m in months_info]
                    vue_month_charges = [m.get("total_charge") for m in months_info]
            except Exception as e:
                logging.warning(f"[{user_id}] 分时电量数据获取失败: {e}")

        # 从电费电量查询页面尝试获取余额 (作为补充)
        if self.db is not None and (balance is None or enhanced_balance is None):
            try:
                components = vue_state.selected_vue_data(driver)
                elec_bal = vue_state.normalize_electric_balance(components)
                if elec_bal.get("balance") is not None and balance is None:
                    balance = elec_bal["balance"]
                    logging.info(f"[{user_id}] 从用电量页面获取余额: {balance} 元")
            except Exception as e:
                logging.debug(f"[{user_id}] 用电量页面余额获取失败: {e}")

        # ---- DOM 方式获取数据 (Vue state 的补充) ----
        logging.info(f"[{user_id}] 正在获取年度用电数据...")
        yearly_usage, yearly_charge = self._get_yearly_data(driver)
        if yearly_usage is None:
            yearly_usage = vue_yearly_usage
        if yearly_charge is None:
            yearly_charge = vue_yearly_charge
        if yearly_usage is not None:
            logging.info(f"[{user_id}] 年度用电量: {yearly_usage} kWh")
        else:
            logging.warning(f"[{user_id}] 年度用电量获取失败")
        if yearly_charge is not None:
            logging.info(f"[{user_id}] 年度电费: {yearly_charge} 元")
        else:
            logging.warning(f"[{user_id}] 年度电费获取失败")

        logging.info(f"[{user_id}] 正在获取月度用电数据...")
        month, month_usage, month_charge = self._get_month_usage(driver)
        if month is None:
            month = vue_months
            month_usage = vue_month_usages
            month_charge = vue_month_charges
        if month is not None:
            for m in range(len(month)):
                logging.info(f"[{user_id}] {month[m]}: 用电 {month_usage[m]} kWh, 电费 {month_charge[m]} 元")
        else:
            logging.warning(f"[{user_id}] 月度用电数据获取失败")

        logging.info(f"[{user_id}] 正在获取最近一日用电（Home Assistant 传感器）...")
        last_daily_date, last_daily_usage = self._get_yesterday_usage(driver, user_id)
        if last_daily_usage is None:
            # DOM 失败时使用 Vue state 数据
            last_daily_date = vue_daily_date
            last_daily_usage = vue_daily_usage
        if last_daily_usage is not None:
            logging.info(f"[{user_id}] 最近用电: {last_daily_date} 用电 {last_daily_usage} kWh")
        else:
            logging.warning(f"[{user_id}] 最近一日用电获取失败 (DOM 和 Vue state 均未获取到)")

        # 尝试获取电费账单明细（月度分时，供 HA 传感器与数据库使用）
        bill_tou_data = None
        try:
            bill_tou_data = self._get_bill_detail(driver, user_id)
        except Exception as e:
            logging.warning(f"[{user_id}] 电费账单分时数据获取失败: {e}")

        if bill_tou_data:
            if tou_data is None:
                tou_data = {}
            tou_data["bill_month_tou"] = bill_tou_data

        # 数据库存储：先拉取 N 天日用电，再统一写入
        if self.db is not None:
            daily_records = self._get_daily_usage_data(driver, user_id)
            if daily_records:
                if tou_data is None:
                    tou_data = {}
                tou_data["daily"] = daily_records
                date_list = [r.get("date", "") for r in daily_records if r.get("date")]
                usage_list = [str(r.get("total_usage", "")) for r in daily_records if r.get("date")]
                if daily_records:
                    latest = daily_records[0]
                    last_daily_date = latest.get("date")
                    last_daily_usage = latest.get("total_usage")
            else:
                date_list, usage_list = [], []

            logging.info(f"[{user_id}] 日用电拉取完成，开始写入 {self.db_type.upper()} 数据库")
            self._save_user_data(
                user_id, balance, enhanced_balance,
                last_daily_date, last_daily_usage,
                date_list, usage_list,
                month, month_usage, month_charge,
                yearly_charge, yearly_usage,
                tou_data, bill_tou_data, user_name,
                step_data=step_data,
            )
        else:
            logging.info(f"[{user_id}] 未配置数据库, 跳过数据存储")

        last_month_period = month[-1] if month else None
        if month_charge:
            month_charge = month_charge[-1]
        else:
            month_charge = None
        if month_usage:
            month_usage = month_usage[-1]
        else:
            month_usage = None

        return balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage, tou_data, enhanced_balance, step_data, last_month_period

    def _db_insert(self, user_id: str, label: str, func, *args, **kwargs) -> bool:
        """执行数据库写入并记录结果，避免静默失败"""
        try:
            ok = func(*args, **kwargs)
        except Exception as e:
            logging.warning(f"[{user_id}] {label} 写入异常: {e}")
            return False
        if ok:
            return True
        logging.warning(f"[{user_id}] {label} 写入失败")
        return False

    def _get_step_electricity(self, driver, user_id, user_name, userid_index):
        """获取阶梯用电数据（仅住宅用户）。充电桩用户没有阶梯信息。"""
        if not user_name or '住宅' not in user_name:
            logging.info(f"[{user_id}] 非住宅用户({user_name}), 跳过阶梯用电查询")
            return None

        try:
            from const import STEP_ELECTRICITY_URL
            logging.info(f"[{user_id}] 住宅用户，开始查询阶梯用电...")
            driver.get(STEP_ELECTRICITY_URL)
            time.sleep(self._step_wait * 2)

            current = self._get_current_userid(driver)
            if current != user_id:
                logging.info(f"[{user_id}] 阶梯页当前户号={current or '未知'}, 正在切换用户...")
                if not self._switch_to_user(driver, user_id, userid_index):
                    logging.warning(f"[{user_id}] 阶梯页用户切换失败, 当前户号={self._get_current_userid(driver) or '未知'}")
                    return None
            else:
                logging.info(f"[{user_id}] 阶梯页户号已正确, 无需切换")

            self._wait_step_data_loaded(driver)
            step_data = self._extract_step_data_from_dom(driver)
            if step_data:
                step_data["user_name"] = user_name
                logging.info(f"[{user_id}] 阶梯用电: {step_data.get('year_month')}, "
                             f"一阶已用={step_data.get('used_step1')}kWh, "
                             f"一阶剩余={step_data.get('remain_step1')}kWh, "
                             f"二阶已用={step_data.get('used_step2')}kWh, "
                             f"二阶剩余={step_data.get('remain_step2')}kWh, "
                             f"三阶已用={step_data.get('used_step3')}kWh, "
                             f"阶段={step_data.get('step_stage')}")
                return step_data

            logging.warning(f"[{user_id}] 阶梯用电数据获取失败 (DOM 未解析到数据)")
            return None
        except Exception as e:
            logging.warning(f"[{user_id}] 阶梯用电查询异常: {e}")
            return None

    def _wait_step_data_loaded(self, driver, timeout: int = 20) -> None:
        """等待阶梯页切换用户后数据刷新"""
        def _loaded(d):
            return d.execute_script("""
                const tips = document.querySelector('.jietilist .tips');
                if (tips && tips.offsetParent !== null) return true;
                const el = document.querySelector('.jietilist .njt1sydl');
                if (!el) return false;
                const n = parseFloat((el.innerText || el.textContent || '0').replace(/[^0-9.]/g, '')) || 0;
                return n > 0;
            """)

        try:
            WebDriverWait(driver, timeout).until(_loaded)
        except Exception:
            logging.debug("阶梯数据加载等待超时, 继续尝试解析")
        time.sleep(self._step_wait)

    def _parse_step_from_page_source(self, page_source: str) -> Optional[dict]:
        """从 page_source 正则解析阶梯数据（与保存的 HTML 结构一致）"""
        if not page_source:
            return None
        if re.search(r"无阶梯用电|暂无.*阶梯", page_source):
            return None
        s1 = [float(x) for x in re.findall(r'class="njt1sydl">([0-9.]+)', page_source)]
        s2 = [float(x) for x in re.findall(r'class="njt2sydl">([0-9.]+)', page_source)]
        s3 = [float(x) for x in re.findall(r'class="surplusthree">([0-9.]+)', page_source)]
        total_m = re.search(r'class="yell">([0-9.]+)', page_source)
        if not s1 and not s2 and not total_m:
            return None
        total = float(total_m.group(1)) if total_m else (s1[0] if s1 else 0.0)
        stage_m = re.search(r"第([一二三])阶段", page_source)
        stage_map = {"一": 1, "二": 2, "三": 3}
        step_stage = stage_map.get(stage_m.group(1), 1) if stage_m else 1
        if not stage_m and s3 and s3[0] > 0:
            step_stage = 3
        elif not stage_m and s2 and s2[0] > 0:
            step_stage = 2
        return {
            "used_step1": s1[0] if len(s1) > 0 else 0.0,
            "remain_step1": s1[1] if len(s1) > 1 else 0.0,
            "used_step2": s2[0] if len(s2) > 0 else 0.0,
            "remain_step2": s2[1] if len(s2) > 1 else 0.0,
            "used_step3": s3[0] if len(s3) > 0 else 0.0,
            "total_usage": total,
            "step_stage": step_stage,
        }

    def _extract_step_data_from_dom(self, driver):
        """从阶梯用电页面 DOM 提取数据（进入页面即为最新月份，无需选月）"""
        try:
            try:
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".jietilist"))
                )
            except Exception:
                pass
            time.sleep(self._step_wait)

            tips_els = driver.find_elements(By.CSS_SELECTOR, ".jietilist .tips")
            if any(el.is_displayed() for el in tips_els):
                logging.warning("该用户无阶梯用电信息")
                return None

            data = driver.execute_script("""
                const list = Array.from(document.querySelectorAll('.jietilist'))
                  .find(el => el.querySelector('.njt1sydl') && el.offsetParent !== null)
                  || document.querySelector('.jietilist');
                if (!list || list.querySelector('.tips')) return null;
                const parseNums = (sel) => Array.from(list.querySelectorAll(sel)).map(el => {
                  const t = (el.innerText || el.textContent || '').replace(/[^0-9.]/g, '');
                  return t ? parseFloat(t) : 0;
                });
                const s1 = parseNums('.njt1sydl');
                const s2 = parseNums('.njt2sydl');
                const s3 = parseNums('.surplusthree');
                const h3 = list.querySelector('h3');
                const stageText = h3 ? (h3.innerText || h3.textContent || '') : '';
                const totalEl = document.querySelector('.addup .yell');
                let total = s1[0] || 0;
                if (totalEl) {
                  const t = (totalEl.innerText || totalEl.textContent || '').replace(/[^0-9.]/g, '');
                  if (t) total = parseFloat(t);
                }
                let stepStage = 1;
                if (/第三|三阶/.test(stageText)) stepStage = 3;
                else if (/第二|二阶/.test(stageText)) stepStage = 2;
                else if (s3[0] > 0) stepStage = 3;
                else if (s2[0] > 0) stepStage = 2;
                if (!s1.length && !s2.length && !total) return null;
                return {
                  used_step1: s1[0] || 0,
                  remain_step1: s1[1] || 0,
                  used_step2: s2[0] || 0,
                  remain_step2: s2[1] || 0,
                  used_step3: s3[0] || 0,
                  total_usage: total,
                  step_stage: stepStage,
                };
            """)

            if not data or (
                data.get("used_step1", 0) == 0
                and data.get("remain_step1", 0) == 0
                and data.get("total_usage", 0) == 0
            ):
                data = self._parse_step_from_page_source(driver.page_source or "")

            if not data:
                logging.warning("阶梯用电页面未解析到数据")
                return None

            has_usage = (
                data.get("used_step1", 0) > 0
                or data.get("remain_step1", 0) > 0
                or data.get("total_usage", 0) > 0
            )
            if not has_usage:
                logging.warning("阶梯用电页面电量数据均为 0")
                return None

            data["year_month"] = datetime.now().strftime("%Y-%m")
            return data
        except Exception as e:
            logging.warning(f"DOM 提取阶梯用电数据失败: {e}")
            return None

    def _get_user_ids(self, driver):
        """获取用户 ID 列表。优先从 el-dropdown 获取（余额页面），
        失败则从 el-select 获取（用电量页面），最后从页面源码正则匹配。
        同时收集用户名映射（从下拉选项文本中提取）。"""
        try:
            # 方式一：经典方式 - 从 el-dropdown 下拉框获取
            time.sleep(self._step_wait)
            dropdowns = driver.find_elements(By.CLASS_NAME, 'el-dropdown')
            if dropdowns:
                self._click_button(driver, By.XPATH, "//div[@class='el-dropdown']/span")
                time.sleep(self._step_wait)
                try:
                    target = driver.find_element(By.CLASS_NAME, "el-dropdown-menu.el-popper").find_element(By.TAG_NAME, "li")
                    WebDriverWait(driver, 10).until(EC.visibility_of(target))
                    WebDriverWait(driver, 10).until(
                        EC.text_to_be_present_in_element((By.XPATH, "//ul[@class='el-dropdown-menu el-popper']/li"), ":"))
                    time.sleep(self._step_wait)
                    userid_elements = driver.find_element(By.CLASS_NAME, "el-dropdown-menu.el-popper").find_elements(By.TAG_NAME, "li")
                    userid_list = []
                    for element in userid_elements:
                        el_text = element.text.strip()
                        matches = re.findall("[0-9]+", el_text)
                        if matches:
                            uid = matches[-1]
                            userid_list.append(uid)
                            # 从文本中提取用户名（格式如 "户号:用户名" 或 "户号 用户名"）
                            name = el_text.replace(uid, "").strip().lstrip(":").strip()
                            if name and uid not in self._user_name_map:
                                self._user_name_map[uid] = name
                                logging.info(f"从 el-dropdown 获取到用户名: {uid} -> {name}")
                    if userid_list:
                        logging.info(f"从 el-dropdown 获取到 {len(userid_list)} 个用户: {userid_list}")
                        return userid_list
                except Exception as e:
                    logging.debug(f"el-dropdown 获取失败, 尝试其他方式: {e}")

            # 方式二：从 el-select 下拉框获取（用电量页面）
            try:
                select_inputs = driver.find_elements(By.CSS_SELECTOR, ".houseNum .el-select .el-input__inner")
                if not select_inputs:
                    driver.get(ELECTRIC_USAGE_URL)
                    time.sleep(self._step_wait * 2)
                    select_inputs = driver.find_elements(By.CSS_SELECTOR, ".houseNum .el-select .el-input__inner")
                
                if select_inputs:
                    driver.execute_script("arguments[0].click();", select_inputs[0])
                    time.sleep(self._step_wait)
                    
                    options = driver.find_elements(By.CSS_SELECTOR, ".el-select-dropdown__item")
                    userid_list = []
                    for opt in options:
                        # 尝试多种方式获取选项文本
                        text = opt.text.strip()
                        if not text:
                            text = driver.execute_script("return arguments[0].innerText || arguments[0].textContent || '';", opt).strip()
                        if re.match(r'^\d{4}$', text):
                            continue
                        driver.execute_script("arguments[0].click();", opt)
                        time.sleep(self._step_wait)
                        try:
                            current_id = self._get_current_userid(driver)
                            if current_id and current_id not in userid_list:
                                userid_list.append(current_id)
                                if text and current_id not in self._user_name_map:
                                    self._user_name_map[current_id] = text
                                logging.info(f"从 el-select 获取到用户: {current_id} ({text})")
                        except Exception:
                            pass
                        select_inputs = driver.find_elements(By.CSS_SELECTOR, ".houseNum .el-select .el-input__inner")
                        if select_inputs:
                            driver.execute_script("arguments[0].click();", select_inputs[0])
                            time.sleep(self._step_wait)
                    
                    if userid_list:
                        logging.info(f"从 el-select 获取到 {len(userid_list)} 个用户: {userid_list}")
                        return userid_list
            except Exception as e:
                logging.debug(f"el-select 获取失败: {e}")

            # 方式三：从页面源码正则匹配所有13位户号
            page_source = driver.page_source or ""
            all_ids = list(set(re.findall(r'\b(\d{13})\b', page_source)))
            if all_ids:
                logging.info(f"从页面源码正则匹配到 {len(all_ids)} 个用户: {all_ids}")
                return all_ids

            logging.error("所有方式均未能获取用户 ID 列表")
            return []
        except Exception as e:
            logging.error(f"获取用户 ID 列表异常: {e}")
            return []

    def _get_electric_balance(self, driver):
        try:
            try:
                # 定位是否有"应交金额"标题（确认是后缴费账户）
                title_text = driver.find_element(By.XPATH, "//p[contains(@class, 'balance_title') and contains(text(), '应交金额')]").text
                if "应交金额" in title_text:
                    # 后缴费账户：需要查找"账户余额"，而不是"应交金额"
                    # 查找包含"账户余额"的balance_title元素，然后获取其内部的金额
                    balance_content = driver.find_element(By.XPATH, "//p[contains(@class, 'balance_title') and contains(text(), '账户余额')]")
                    # 提取数字部分
                    balance_text = re.sub(r'[^\d.]', '', balance_content.text)
                    if balance_text:
                        return float(balance_text)
            except Exception as e:
                # 后缴费账户解析失败，继续尝试预缴费账户逻辑
                pass

            # 2. 预缴费账户的"账户余额"（原逻辑）
            balance_text = driver.find_element(By.CLASS_NAME, "cff8").text
            balance = balance_text.replace("元", "")
            if "欠费" in balance_text:
                return -float(balance)
            else:
                return float(balance)
        except Exception as e:
            logging.error(f"获取余额失败: {e}")
            return None

    def _get_yearly_data(self, driver):

        try:
            if datetime.now().month == 1:
                self._click_button(driver, By.XPATH, '//*[@id="pane-first"]/div[1]/div/div[1]/div/div/input')
                time.sleep(self._step_wait)
                span_element = driver.find_element(By.XPATH, f"//span[text() = '{datetime.now().year - 1}']")
                span_element.click()
                time.sleep(self._step_wait)
            self._click_button(driver, By.XPATH, "//div[@class='el-tabs__nav is-top']/div[@id='tab-first']")
            time.sleep(self._step_wait)
            # wait for data displayed
            target = driver.find_element(By.CLASS_NAME, "total")
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(target))
        except Exception as e:
            logging.error(f"获取年度数据失败: {e}")
            return None, None

        # get data
        try:
            yearly_usage = driver.find_element(By.XPATH, "//ul[@class='total']/li[1]/span").text
        except Exception as e:
            logging.error(f"获取年度用电量失败: {e}")
            yearly_usage = None

        try:
            yearly_charge = driver.find_element(By.XPATH, "//ul[@class='total']/li[2]/span").text
        except Exception as e:
            logging.error(f"获取年度电费失败: {e}")
            yearly_charge = None

        return yearly_usage, yearly_charge

    def _get_yesterday_usage(self, driver, user_id=""):
        """获取最近一次用电量"""
        try:
            # 点击日用电量 tab
            self._click_button(driver, By.XPATH, "//div[@class='el-tabs__nav is-top']/div[@id='tab-second']")
            time.sleep(self._step_wait * 3)
            # 等待数据表格出现（兼容多种滚动类名）
            usage_element = WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(
                EC.visibility_of_element_located((
                    By.XPATH,
                    "//div[contains(@class,'el-tab-pane')]//div[contains(@class,'el-table__body-wrapper')]"
                    "/table/tbody/tr[1]/td[2]/div"
                ))
            )
            date_element = driver.find_element(By.XPATH,
                                                "//div[contains(@class,'el-tab-pane')]//div[contains(@class,'el-table__body-wrapper')]"
                                                "/table/tbody/tr[1]/td[1]/div")
            last_daily_date = date_element.text
            return last_daily_date, float(usage_element.text)
        except Exception as e:
            tag = f"[{user_id}] " if user_id else ""
            logging.warning(f"{tag}DOM 获取最近一日用电失败 (将尝试 Vue state 或后续批量拉取补充): {e}")
            return None, None

    def _get_month_usage(self, driver):
        """获取每月用电量"""

        try:
            self._click_button(driver, By.XPATH, "//div[@class='el-tabs__nav is-top']/div[@id='tab-first']")
            time.sleep(self._step_wait)
            if datetime.now().month == 1:
                self._click_button(driver, By.XPATH, '//*[@id="pane-first"]/div[1]/div/div[1]/div/div/input')
                time.sleep(self._step_wait)
                span_element = driver.find_element(By.XPATH, f"//span[text() = '{datetime.now().year - 1}']")
                span_element.click()
                time.sleep(self._step_wait)
            # wait for month displayed
            target = driver.find_element(By.CLASS_NAME, "total")
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(target))
            month_element = driver.find_element(By.XPATH, "//*[@id='pane-first']/div[1]/div[2]/div[2]/div/div[3]/table/tbody").text
            month_element = month_element.split("\n")
            month_element = [x for x in month_element if x != "MAX"]
            if len(month_element) % 3 != 0:
                month_element = month_element[:-(len(month_element) % 3)]
            month_element = np.array(month_element).reshape(-1, 3)
            # 将每月的用电量保存为List
            month = []
            usage = []
            charge = []
            for i in range(len(month_element)):
                month.append(month_element[i][0])
                usage.append(month_element[i][1])
                charge.append(month_element[i][2])
            return month, usage, charge
        except Exception as e:
            logging.error(f"获取月度数据失败: {e}")
            return None,None,None

    # 增加获取每日用电量的函数
    def _get_daily_usage_data(self, driver, user_id=""):
        """获取每日用电量完整记录（含峰谷分时），返回 dict 列表"""
        fetch_days = int(os.getenv("DAILY_FETCH_DAYS", 30))
        if fetch_days not in (7, 30):
            fetch_days = 7

        try:
            logging.info(f"[{user_id}] 正在获取每日用电量数据 (最近 {fetch_days} 天)")
            self._click_button(driver, By.XPATH, "//div[@class='el-tabs__nav is-top']/div[@id='tab-second']")
            time.sleep(self._step_wait * 3)

            if fetch_days == 30:
                self._click_30day_radio(driver)
                time.sleep(self._step_wait * 4)
                self._scroll_table_to_load_all(driver)
            else:
                self._click_7day_radio(driver)
                time.sleep(self._step_wait * 2)

            records_by_date = {}

            # Vue state 获取总用量和分时（30天模式优先 thirtyEleList）
            try:
                components = vue_state.selected_vue_data(driver)
                usage_info = vue_state.normalize_usage(components, fetch_days=fetch_days)
                for row in usage_info.get("daily", []):
                    if row.get("date"):
                        records_by_date[row["date"]] = dict(row)
                if records_by_date:
                    logging.info(f"[{user_id}] [Vue state] 获取 {len(records_by_date)} 天日用电数据")
            except Exception as e:
                logging.warning(f"[{user_id}] Vue state 日用电获取失败: {e}")

            # DOM 表格展开行获取峰谷分时（仅补全 Vue state 缺失分时的日期）
            dates_need_tou = {
                d for d, r in records_by_date.items()
                if (r.get("total_usage") or 0) > 0
                and sum(r.get(k, 0) or 0 for k in ("valley_usage", "flat_usage", "peak_usage", "tip_usage")) == 0
            }
            if dates_need_tou:
                logging.info(f"[{user_id}] 有 {len(dates_need_tou)} 天缺少峰谷分时, 尝试 DOM 展开行补全...")
                dom_records = self._extract_daily_tou_from_dom(driver, target_dates=dates_need_tou)
            elif fetch_days == 7 and records_by_date:
                logging.info(f"[{user_id}] 7天模式, 通过 DOM 展开行获取峰谷分时...")
                dom_records = self._extract_daily_tou_from_dom(driver)
            else:
                dom_records = []
            if dom_records:
                merged = 0
                for row in dom_records:
                    date = row.get("date")
                    if not date:
                        continue
                    existing = records_by_date.get(date, {})
                    merged_row = {**existing, **row}
                    for k in ("valley_usage", "flat_usage", "peak_usage", "tip_usage"):
                        if row.get(k, 0) > 0:
                            merged_row[k] = row[k]
                    if row.get("total_usage") is not None:
                        merged_row["total_usage"] = row["total_usage"]
                    if any(row.get(k, 0) > 0 for k in ("valley_usage", "flat_usage", "peak_usage", "tip_usage")):
                        merged += 1
                    records_by_date[date] = merged_row
                logging.info(f"[{user_id}] [DOM展开行] 获取 {len(dom_records)} 天, 其中 {merged} 天含峰谷分时")

            if not records_by_date:
                date, usages = self._extract_daily_table_data(driver)
                for i in range(len(date)):
                    records_by_date[date[i]] = {
                        "date": date[i],
                        "total_usage": float(usages[i]) if usages[i] else 0.0,
                        "valley_usage": 0.0, "flat_usage": 0.0,
                        "peak_usage": 0.0, "tip_usage": 0.0,
                    }

            daily_records = sorted(records_by_date.values(), key=lambda x: x.get("date", ""), reverse=True)
            for d in daily_records:
                logging.info(f"  [每日用电] {d.get('date')}: 总={d.get('total_usage')}kWh, "
                             f"谷={d.get('valley_usage', 0)}, 平={d.get('flat_usage', 0)}, "
                             f"峰={d.get('peak_usage', 0)}, 尖={d.get('tip_usage', 0)}")
            logging.info(f"[{user_id}] 每日用电量共 {len(daily_records)} 条")
            return daily_records
        except Exception as e:
            logging.warning(f"[{user_id}] 获取每日用电量数据失败: {e}")
            return []

    def _click_7day_radio(self, driver):
        """点击 '近7天' radio 按钮"""
        try:
            radio = driver.find_element(By.XPATH,
                "//span[contains(@class,'el-radio__label') and contains(text(),'近7天')]"
                "/preceding-sibling::span//input[@class='el-radio__original']")
            driver.execute_script("arguments[0].click();", radio)
            logging.info("已点击 '近7天' radio 按钮")
        except Exception:
            try:
                self._click_button(driver, By.XPATH,
                    "//*[@id='pane-second']//label[1]//span[@class='el-radio__input']")
                logging.info("已点击「近7天」（备用方式）")
            except Exception:
                logging.debug("未找到 '近7天' radio, 使用默认数据")

    def _click_30day_radio(self, driver):
        """点击 '近30天' radio 按钮"""
        try:
            radio = driver.find_element(By.XPATH,
                "//span[contains(@class,'el-radio__label') and contains(text(),'近30天')]"
                "/preceding-sibling::span//input[@class='el-radio__original']")
            driver.execute_script("arguments[0].click();", radio)
            logging.info("已点击 '近30天' radio 按钮")
        except Exception:
            try:
                self._click_button(driver, By.XPATH,
                    "//*[@id='pane-second']//label[2]//span[@class='el-radio__input']")
                logging.info("已点击「近30天」（备用方式）")
            except Exception:
                logging.warning("未找到 '近30天' radio, 使用默认数据")

    def _scroll_table_to_load_all(self, driver):
        """滚动表格区域以触发懒加载（30天数据可能需要滚动）"""
        try:
            scroll_container = driver.find_element(By.XPATH,
                "//*[@id='pane-second']//div[contains(@class,'el-table__body-wrapper')]")
            # 分多次滚动到底部
            for _ in range(3):
                driver.execute_script(
                    "arguments[0].scrollTop = arguments[0].scrollHeight", scroll_container)
                time.sleep(self._step_wait)
        except Exception:
            pass

    def _extract_daily_table_data(self, driver):
        """从 DOM 表格提取每日用电量数据"""
        date = []
        usages = []
        try:
            # 等待数据出现
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(
                EC.visibility_of_element_located((
                    By.XPATH,
                    "//div[contains(@class,'el-tab-pane')]//div[contains(@class,'el-table__body-wrapper')]"
                    "//table/tbody/tr[1]/td[2]/div"
                ))
            )
            days_element = driver.find_elements(By.XPATH,
                "//*[@id='pane-second']//div[contains(@class,'el-table__body-wrapper')]"
                "/table/tbody/tr")
            for i in days_element:
                try:
                    day = i.find_element(By.XPATH, "td[1]/div").text
                    usage = i.find_element(By.XPATH, "td[2]/div").text
                    if usage != "":
                        usages.append(usage)
                        date.append(day)
                except Exception:
                    pass
        except Exception:
            pass
        return date, usages

    def _extract_daily_tou_from_dom(self, driver, target_dates=None):
        """展开日用电量表格行，按日期获取峰谷分时电量"""
        records = []
        seen_dates = set()
        target_dates = set(target_dates or [])
        try:
            scroll_container = None
            try:
                scroll_container = driver.find_element(By.XPATH,
                    "//*[@id='pane-second']//div[contains(@class,'el-table__body-wrapper')]")
                driver.execute_script("arguments[0].scrollTop = 0", scroll_container)
                time.sleep(0.5)
            except Exception:
                pass

            for _ in range(12):
                rows = driver.find_elements(By.CSS_SELECTOR,
                    "#pane-second .el-table__body-wrapper tbody tr.el-table__row")
                for row in rows:
                    try:
                        date = row.find_element(By.CSS_SELECTOR, "td:nth-child(1) .cell").text.strip()
                        if not date or date in seen_dates:
                            continue
                        if target_dates and date not in target_dates:
                            continue
                        if target_dates and len(seen_dates) >= len(target_dates):
                            break
                        total_text = row.find_element(By.CSS_SELECTOR, "td:nth-child(2) .cell").text.strip()
                        total = float(total_text) if total_text not in ("", "-", "—") else 0.0
                        tou = {"valley_usage": 0.0, "flat_usage": 0.0, "peak_usage": 0.0, "tip_usage": 0.0}

                        try:
                            expand_icon = row.find_element(By.CSS_SELECTOR, ".el-table__expand-icon")
                            if "el-table__expand-icon--expanded" not in (expand_icon.get_attribute("class") or ""):
                                driver.execute_script("arguments[0].click();", expand_icon)
                                time.sleep(0.2)
                            expanded_row = row.find_element(By.XPATH, "following-sibling::tr[contains(@class,'el-table__expanded-row')]")
                            cell = expanded_row.find_element(By.CSS_SELECTOR, ".drop-box-left")
                            for p in cell.find_elements(By.TAG_NAME, "p"):
                                text = p.text or ""
                                try:
                                    val = float(p.find_element(By.CSS_SELECTOR, ".num").text)
                                except Exception:
                                    continue
                                if "尖" in text:
                                    tou["tip_usage"] = val
                                elif "峰" in text:
                                    tou["peak_usage"] = val
                                elif "谷" in text:
                                    tou["valley_usage"] = val
                                elif "平" in text:
                                    tou["flat_usage"] = val
                        except Exception:
                            pass

                        records.append({"date": date, "total_usage": total, **tou})
                        seen_dates.add(date)
                    except Exception:
                        continue

                if target_dates and len(seen_dates) >= len(target_dates):
                    break
                if scroll_container is None:
                    break
                prev = driver.execute_script("return arguments[0].scrollTop", scroll_container)
                driver.execute_script(
                    "arguments[0].scrollTop = Math.min(arguments[0].scrollTop + arguments[0].clientHeight, arguments[0].scrollHeight)",
                    scroll_container)
                time.sleep(0.5)
                curr = driver.execute_script("return arguments[0].scrollTop", scroll_container)
                if curr <= prev:
                    break
        except Exception as e:
            logging.warning(f"DOM 展开行获取分时电量失败: {e}")
        return records

    def _extract_daily_vue_data(self, driver):
        """从 Vue state 提取每日用电量数据 (作为 DOM 方式的 fallback)"""
        try:
            components = vue_state.selected_vue_data(driver)
            fetch_days = int(os.getenv("DAILY_FETCH_DAYS", 30))
            usage_info = vue_state.normalize_usage(components, fetch_days=fetch_days)
            daily_list = usage_info.get("daily", [])
            if daily_list:
                date = [d.get("date", "") for d in daily_list if d.get("date")]
                usages = [str(d.get("total_usage", "")) for d in daily_list if d.get("date")]
                logging.info(f"[分时数据] 成功获取 {len(date)} 天的每日用电量数据")
                for d in daily_list:
                    if d.get("date"):
                        logging.info(f"  [每日用电] {d.get('date')}: "
                                     f"总={d.get('total_usage', 0)}kWh, "
                                     f"谷={d.get('valley_usage', 0)}, 平={d.get('flat_usage', 0)}, "
                                     f"峰={d.get('peak_usage', 0)}, 尖={d.get('tip_usage', 0)}")
                return date, usages
        except Exception as e:
            logging.warning(f"[分时数据] Vue state 每日用电量获取失败: {e}")
        return [], []

    def _get_daily_tou_data(self, driver):
        """通过展开日用电量表格行获取每日分时电量（谷/平/峰/尖）"""
        tou_rows = []
        try:
            # 找到所有展开图标并逐个点击
            expand_icons = driver.find_elements(By.CSS_SELECTOR,
                ".el-table__expand-icon")
            for icon in expand_icons:
                try:
                    driver.execute_script("arguments[0].click();", icon)
                    time.sleep(0.5)
                except Exception:
                    continue

            time.sleep(1)

            # 读取展开行中的分时电量
            expanded_cells = driver.find_elements(By.CSS_SELECTOR,
                ".el-table__expanded-cell .drop-box-left")
            for cell in expanded_cells:
                tou = {"valley_usage": 0.0, "flat_usage": 0.0, "peak_usage": 0.0, "tip_usage": 0.0}
                paragraphs = cell.find_elements(By.TAG_NAME, "p")
                for p in paragraphs:
                    text = p.text
                    try:
                        num_el = p.find_element(By.CSS_SELECTOR, ".num")
                        val = float(num_el.text)
                    except Exception:
                        continue
                    if "谷" in text:
                        tou["valley_usage"] = val
                    elif "平" in text:
                        tou["flat_usage"] = val
                    elif "峰" in text:
                        tou["peak_usage"] = val
                    elif "尖" in text:
                        tou["tip_usage"] = val
                tou_rows.append(tou)
            logging.info(f"通过展开行获取到 {len(tou_rows)} 条分时电量数据")
        except Exception as e:
            logging.warning(f"获取展开行分时电量失败: {e}")
        return tou_rows

    def _get_bill_detail(self, driver, user_id):
        """从用电量页面通过 Vue state 获取月度分时电量"""
        logging.info(f"[{user_id}] 尝试从当前页面获取电费账单分时数据...")
        try:
            # 不再跳转到 403 的 BILL_SUMMARY_URL, 直接从当前页面提取
            components = vue_state.selected_vue_data(driver)
            bill = vue_state.normalize_bill_detail(components)
            if bill.get("month"):
                logging.info(f"[{user_id}] 账单分时数据: {bill['month']}, "
                             f"谷={bill.get('valley_usage')}, 平={bill.get('flat_usage')}, "
                             f"峰={bill.get('peak_usage')}, 尖={bill.get('tip_usage')}")
                return bill
            logging.info(f"[{user_id}] Vue state 中未找到账单数据, 跳过")
            return None
        except Exception as e:
            logging.warning(f"[{user_id}] 获取账单分时数据异常: {e}")
            return None

    def _save_user_data(self, user_id, balance, enhanced_balance,
                        last_daily_date, last_daily_usage,
                        date_list, usage_list,
                        month, month_usage, month_charge,
                        yearly_charge, yearly_usage,
                        tou_data=None, bill_tou_data=None, user_name="",
                        step_data=None):
        if not self.db.connect_user_db(user_id):
            logging.error(f"[{user_id}] 数据库连接失败, 数据未写入")
            return

        try:
            self._db_insert(user_id, "用户信息", self.db.upsert_user, user_id, self._username, user_name)
            logging.info(f"[{user_id}] 用户信息已更新 (user_name={user_name})")

            # 写入余额日志
            if balance is not None:
                bal_data = {"balance": balance, "user_name": user_name}
                if enhanced_balance:
                    bal_data.update({
                        "as_of": enhanced_balance.get("as_of"),
                        "amount_due": enhanced_balance.get("amount_due"),
                    })
                if self._db_insert(user_id, "余额日志", self.db.insert_balance_log, bal_data):
                    logging.info(f"[{user_id}] 余额日志已写入: {balance} 元")

            # 写入每日用电量（含峰谷分时，单一来源避免重复）
            daily_written = False
            if tou_data and tou_data.get("daily"):
                tou_count = 0
                for row in tou_data["daily"]:
                    if self._db_insert(user_id, f"日用电 {row.get('date')}", self.db.insert_daily_data, {**row, "user_name": user_name}):
                        tou_count += 1
                logging.info(f"[{user_id}] 每日用电量已写入 {tou_count} 条 (含峰谷分时)")
                daily_written = tou_count > 0
            elif date_list:
                ok_count = 0
                for i in range(len(date_list)):
                    if self._db_insert(user_id, f"日用电 {date_list[i]}", self.db.insert_daily_data, {
                        "date": date_list[i],
                        "total_usage": float(usage_list[i]),
                        "user_name": user_name,
                    }):
                        ok_count += 1
                logging.info(f"[{user_id}] 每日用电量已写入 {ok_count} 条")
                daily_written = ok_count > 0

            # 日数据入库后，汇总当前自然月分时并写入 monthly_usage，供 HA 传感器使用
            if daily_written:
                current_month = datetime.now().strftime("%Y-%m")
                if self.db.sync_monthly_from_daily(current_month):
                    logging.info(f"[{user_id}] 当月 {current_month} 分时已汇总写入 monthly_usage")
                summary = self.db.query_month_tou_from_daily(user_id, current_month)
                if summary:
                    if tou_data is not None:
                        tou_data["month_tou_summary"] = summary
                    logging.info(
                        f"[{user_id}] 当月 {current_month} 分时汇总 ({summary['day_count']} 天): "
                        f"谷={summary['valley_usage']}, 平={summary['flat_usage']}, "
                        f"峰={summary['peak_usage']}, 尖={summary['tip_usage']} kWh"
                    )
                else:
                    logging.warning(f"[{user_id}] 当月 {current_month} 日数据已写入，但 SQL 汇总为空")

            # 写入月度用电量：优先 Vue state 分时数据，DOM 作补充
            if tou_data and tou_data.get("months"):
                ok_count = 0
                for m_row in tou_data["months"]:
                    m_row["user_name"] = user_name
                    if self._db_insert(user_id, f"月度 {m_row.get('month')}", self.db.insert_monthly_data, m_row):
                        ok_count += 1
                logging.info(f"[{user_id}] Vue state 分时月用电已写入 {ok_count} 条")
            elif month:
                ok_count = 0
                cur_year = str(datetime.now().year)
                for i in range(len(month)):
                    m_text = month[i]
                    m_num = re.search(r'(\d+)月', m_text)
                    m_formatted = f"{cur_year}-{int(m_num.group(1)):02d}" if m_num else m_text
                    if self._db_insert(user_id, f"月度 {m_formatted}", self.db.insert_monthly_data, {
                        "month": m_formatted,
                        "total_usage": float(month_usage[i]) if month_usage[i] else None,
                        "total_charge": float(month_charge[i]) if month_charge[i] else None,
                        "user_name": user_name,
                    }):
                        ok_count += 1
                logging.info(f"[{user_id}] 月度用电量已写入 {ok_count} 条")

            # 写入账单分时月用电量（补充 TOU 明细，与上方月度 upsert 合并）
            if bill_tou_data and bill_tou_data.get("month"):
                if self._db_insert(user_id, f"账单分时 {bill_tou_data['month']}", self.db.insert_monthly_data, {
                    "month": bill_tou_data["month"],
                    "total_usage": bill_tou_data.get("usage"),
                    "total_charge": bill_tou_data.get("charge"),
                    "valley_usage": bill_tou_data.get("valley_usage", 0),
                    "flat_usage": bill_tou_data.get("flat_usage", 0),
                    "peak_usage": bill_tou_data.get("peak_usage", 0),
                    "tip_usage": bill_tou_data.get("tip_usage", 0),
                    "user_name": user_name,
                }):
                    logging.info(f"[{user_id}] 账单分时月度数据已写入: {bill_tou_data['month']}")

            # 写入年度用电量：优先 Vue state，DOM 作补充
            if tou_data and tou_data.get("year") and (
                tou_data.get("yearly_usage") is not None or tou_data.get("yearly_charge") is not None
            ):
                if self._db_insert(user_id, f"年度 {tou_data['year']}", self.db.insert_yearly_data, {
                    "year": tou_data["year"],
                    "total_usage": tou_data.get("yearly_usage"),
                    "total_charge": tou_data.get("yearly_charge"),
                    "user_name": user_name,
                }):
                    logging.info(f"[{user_id}] Vue state 年度数据已写入: {tou_data['year']}")
            elif yearly_usage is not None or yearly_charge is not None:
                year = str(datetime.now().year)
                year_data = {"year": year, "user_name": user_name}
                if yearly_usage is not None:
                    year_data["total_usage"] = float(yearly_usage)
                if yearly_charge is not None:
                    year_data["total_charge"] = float(yearly_charge)
                if self._db_insert(user_id, f"年度 {year}", self.db.insert_yearly_data, year_data):
                    logging.info(f"[{user_id}] 年度用电量已写入: {year}")

            # 写入阶梯用电数据
            if step_data:
                if self._db_insert(user_id, f"阶梯用电 {step_data.get('year_month')}", self.db.insert_step_data, step_data):
                    logging.info(f"[{user_id}] 阶梯用电数据已写入: {step_data.get('year_month')}")

            # 数据清理（仅过期日数据/余额，不删阶梯和其他汇总）
            self.db.cleanup_old_data()
            logging.info(f"[{user_id}] 数据清理完成")

        except Exception as e:
            logging.error(f"[{user_id}] 数据保存过程出错: {e}")
        finally:
            self.db.close_connect()
