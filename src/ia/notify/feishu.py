from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

import httpx

from ia.models import Report
from ia.notify.base import NotifyResult
from ia.report.render_feishu import render_feishu

log = logging.getLogger(__name__)


def sign(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class FeishuNotifier:
    name = "feishu"

    def __init__(self, webhook: str, secret: str = ""):
        self.webhook = webhook
        self.secret = secret

    def send(self, report: Report) -> NotifyResult:
        payload: dict = {"msg_type": "interactive", "card": render_feishu(report)}
        if self.secret:
            ts = str(int(time.time()))
            payload["timestamp"] = ts
            payload["sign"] = sign(ts, self.secret)
        try:
            resp = httpx.post(self.webhook, json=payload, timeout=10)
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("飞书推送失败: %s", exc)
            return NotifyResult(False, self.name, str(exc)[:200])
        code = data.get("code", data.get("StatusCode", -1))
        if code == 0:
            return NotifyResult(True, self.name)
        log.warning("飞书返回异常: %s", data)
        return NotifyResult(False, self.name, str(data)[:200])
