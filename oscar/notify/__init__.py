from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oscar.config import Settings
    from oscar.notify.base import Notifier

def make_notifier(settings: "Settings") -> "Notifier | None":
    if settings.pushover_token and settings.pushover_user_key:
        from oscar.notify.pushover import PushoverNotifier
        return PushoverNotifier(settings.pushover_token, settings.pushover_user_key)
    return None
