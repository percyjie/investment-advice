from __future__ import annotations

from ia.config import Secrets, Settings
from ia.notify.base import Notifier
from ia.notify.console import ConsoleNotifier
from ia.notify.dingtalk import DingTalkNotifier
from ia.notify.feishu import FeishuNotifier


def build_notifiers(settings: Settings, secrets: Secrets, dry_run: bool = False) -> list[Notifier]:
    if dry_run:
        return [ConsoleNotifier()]
    notifiers: list[Notifier] = []
    if settings.notify.feishu.enabled and secrets.feishu_webhook:
        notifiers.append(FeishuNotifier(secrets.feishu_webhook, secrets.feishu_secret))
    if settings.notify.dingtalk.enabled and secrets.dingtalk_webhook:
        notifiers.append(DingTalkNotifier(secrets.dingtalk_webhook, secrets.dingtalk_secret))
    return notifiers
