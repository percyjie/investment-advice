from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse

import httpx

from ia.models import Report
from ia.notify.base import NotifyResult
from ia.report.render_dingtalk import render_dingtalk

log = logging.getLogger(__name__)


def sign(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    return urllib.parse.quote_plus(base64.b64encode(digest))


class DingTalkNotifier:
    name = "dingtalk"

    def __init__(self, webhook: str, secret: str = ""):
        self.webhook = webhook
        self.secret = secret

    def _url(self) -> str:
        if not self.secret:
            return self.webhook
        ts = str(round(time.time() * 1000))
        sep = "&" if "?" in self.webhook else "?"
        return f"{self.webhook}{sep}timestamp={ts}&sign={sign(ts, self.secret)}"

    def send(self, report: Report) -> NotifyResult:
        payload = {"msgtype": "markdown", "markdown": {"title": report.title, "text": render_dingtalk(report)}}
        try:
            resp = httpx.post(self._url(), json=payload, timeout=10)
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("钉钉推送失败: %s", exc)
            return NotifyResult(False, self.name, str(exc)[:200])
        if data.get("errcode") == 0:
            return NotifyResult(True, self.name)
        log.warning("钉钉返回异常: %s", data)
        return NotifyResult(False, self.name, str(data)[:200])
