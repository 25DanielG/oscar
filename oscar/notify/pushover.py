from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger()

_URL = "https://api.pushover.net/1/messages.json"

PRIORITY_LOW = -1
PRIORITY_NORMAL = 0
PRIORITY_HIGH = 1

class PushoverNotifier:
    def __init__(self, token: str, user_key: str) -> None:
        self._token = token
        self._user_key = user_key

    async def send(self, title: str, message: str, priority: int = PRIORITY_NORMAL) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(
                    _URL,
                    data={"token": self._token, "user": self._user_key, "title": title, "message": message, "priority": priority},
                )
                resp.raise_for_status()
                log.info("notification_sent", title=title)
            except httpx.HTTPError as exc:
                log.error("notification_failed", title=title, error=str(exc))
