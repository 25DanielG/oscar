from typing import Protocol

class Notifier(Protocol):
    async def send(self, title: str, message: str, priority: int = 0) -> None: ...
