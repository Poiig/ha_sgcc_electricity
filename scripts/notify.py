import hashlib
import base64
import io
import logging
import os
import typing as typ

import requests


def _balance_threshold() -> float:
    return float(os.getenv("BALANCE", 10.0))


def _should_notify(balance: float) -> bool:
    threshold = _balance_threshold()
    logging.info("检查电费余额，低于 %s 元时将发送通知", threshold)
    return balance < threshold


def _format_user_label(user_id, user_name: str = "") -> str:
    name = (user_name or "").strip()
    if name and name != str(user_id):
        return f"{name}（{user_id}）"
    return str(user_id)


def _post_wework(webhook: str, payload: dict) -> bool:
    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        if resp.status_code != 200:
            logging.warning("企业微信推送 HTTP 失败: %s %s", resp.status_code, resp.text[:200])
            return False
        data = resp.json()
        if data.get("errcode", 0) != 0:
            logging.warning("企业微信推送返回错误: %s", data)
            return False
        return True
    except Exception as exc:
        logging.warning("企业微信推送异常: %s", exc)
        return False


class PushplusNotify(typ.NamedTuple):

    def __call__(self, user_id, balance, user_name: str = ""):
        if not _should_notify(balance):
            return False
        label = _format_user_label(user_id, user_name)
        tokens = os.getenv("PUSHPLUS_TOKEN", "").split(",")
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            title = "电费余额不足提醒"
            content = f"用户 {label} 的当前电费余额为：{balance}元，请及时充值。"
            url = f"http://www.pushplus.plus/send?token={token}&title={title}&content={content}"
            resp = requests.get(url, timeout=10)
            logging.info("已发送 PushPlus 余额提醒: %s 余额=%s", label, balance)
            return resp.status_code == 200
        return False


class UrlPushNotify(typ.NamedTuple):

    def __call__(self, user_id, balance, user_name: str = ""):
        if not _should_notify(balance):
            return False
        label = _format_user_label(user_id, user_name)
        url = os.getenv("PUSH_URL", "").strip()
        if not url:
            logging.warning("PUSH_URL 未配置")
            return False
        resp = requests.post(
            url,
            json={"user_id": user_id, "user_name": user_name, "balance": balance},
            timeout=10,
        )
        logging.info("已发送 URL 余额提醒: %s 余额=%s", label, balance)
        return resp.status_code == 200


class WeworkNotify(typ.NamedTuple):
    """企业微信群机器人 webhook 余额提醒。"""

    def __call__(self, user_id, balance, user_name: str = ""):
        if not _should_notify(balance):
            return False
        webhook = os.getenv("WEWORK_WEBHOOK_URL", "").strip()
        if not webhook:
            logging.warning("WEWORK_WEBHOOK_URL 未配置")
            return False
        label = _format_user_label(user_id, user_name)
        threshold = _balance_threshold()
        content = (
            "**电费余额不足提醒**\n"
            f"> 户名：<font color=\"comment\">{(user_name or user_id)}</font>\n"
            f"> 户号：<font color=\"comment\">{user_id}</font>\n"
            f"> 当前余额：<font color=\"warning\">{balance} 元</font>\n"
            f"> 提醒阈值：{threshold} 元\n"
            "请及时登录国网 APP 或网站充值。"
        )
        payload = {"msgtype": "markdown", "markdown": {"content": content}}
        ok = _post_wework(webhook, payload)
        if ok:
            logging.info("已发送企业微信余额提醒: %s 余额=%s", label, balance)
        return ok


class WeworkSummaryNotify(typ.NamedTuple):
    """企业微信推送户号数据汇总（拉取成功后）。"""

    def __call__(self, records: list) -> bool:
        webhook = os.getenv("WEWORK_WEBHOOK_URL", "").strip()
        if not webhook or not records:
            return False

        now = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "## 国家电网数据同步完成",
            f"> 同步时间：<font color=\"comment\">{now}</font>",
            f"> 成功户号：<font color=\"info\">{len(records)}</font> 个",
            "",
        ]
        for item in records:
            user_id = item.get("user_id", "")
            user_name = item.get("user_name") or user_id
            balance = item.get("balance")
            balance_text = f"{balance} 元" if balance is not None else "—"
            balance_color = "warning" if balance is not None and balance < _balance_threshold() else "info"
            lines.extend([
                f"### {user_name}",
                f"> 户号：<font color=\"comment\">{user_id}</font>",
                f"> 余额：<font color=\"{balance_color}\">{balance_text}</font>",
            ])
            if item.get("last_daily_date") and item.get("last_daily_usage") is not None:
                lines.append(
                    f"> 最近用电：{item['last_daily_usage']} kWh ({item['last_daily_date']})"
                )
            if item.get("month_usage") is not None or item.get("month_charge") is not None:
                lines.append(
                    f"> 本月：{item.get('month_usage', '—')} kWh / {item.get('month_charge', '—')} 元"
                )
            if item.get("yearly_usage") is not None or item.get("yearly_charge") is not None:
                lines.append(
                    f"> 年度：{item.get('yearly_usage', '—')} kWh / {item.get('yearly_charge', '—')} 元"
                )
            enhanced = item.get("enhanced_balance") or {}
            if enhanced.get("amount_due") is not None:
                lines.append(f"> 应交金额：{enhanced['amount_due']} 元")
            lines.append("")

        content = "\n".join(lines).strip()
        payload = {"msgtype": "markdown", "markdown": {"content": content}}
        ok = _post_wework(webhook, payload)
        if ok:
            logging.info("已发送企业微信数据汇总，共 %s 个户号", len(records))
        return ok


def push_fetch_summary(records: list) -> bool:
    """拉取成功后推送汇总（仅 wework 且 WEWORK_PUSH_SUMMARY 启用）。"""
    if not records:
        return False
    enabled = os.getenv("WEWORK_PUSH_SUMMARY", "true").lower() in ("true", "1", "yes")
    push_type = os.getenv("PUSH_TYPE", "none").strip().lower()
    if not enabled or push_type != "wework":
        return False
    if not os.getenv("WEWORK_WEBHOOK_URL", "").strip():
        logging.warning("WEWORK_WEBHOOK_URL 未配置，跳过汇总推送")
        return False
    return WeworkSummaryNotify()(records)


class UrlLoginQrCodeNotify(typ.NamedTuple):

    def __call__(self, qrcode) -> bool:
        url = os.getenv("PUSH_QRCODE_URL", "").strip()
        if not url:
            return False
        files = {"file": ("qrcode.png", io.BytesIO(qrcode), "image/png")}
        resp = requests.post(url, files=files, timeout=15)
        logging.info("已推送二维码到自定义 URL")
        return resp.status_code == 200


class WeworkQrCodeNotify(typ.NamedTuple):
    """企业微信群机器人推送登录二维码（image 类型）。"""

    def __call__(self, qrcode) -> bool:
        webhook = os.getenv("WEWORK_WEBHOOK_URL", "").strip()
        if not webhook:
            return False
        if isinstance(qrcode, str):
            qrcode = qrcode.encode("utf-8")
        image_md5 = hashlib.md5(qrcode).hexdigest()
        image_base64 = base64.b64encode(qrcode).decode("ascii")
        payload = {
            "msgtype": "image",
            "image": {"base64": image_base64, "md5": image_md5},
        }
        ok = _post_wework(webhook, payload)
        if ok:
            logging.info("已推送登录二维码到企业微信")
        return ok


def get_qrcode_notifier():
    """按配置选择二维码推送方式。PUSH_TYPE=wework 时优先走企微 webhook。"""
    push_type = os.getenv("PUSH_TYPE", "none").strip().lower()
    wework = os.getenv("WEWORK_WEBHOOK_URL", "").strip()
    if push_type == "wework" and wework:
        return WeworkQrCodeNotify()
    if os.getenv("PUSH_QRCODE_URL", "").strip():
        return UrlLoginQrCodeNotify()
    if wework:
        return WeworkQrCodeNotify()
    return None
