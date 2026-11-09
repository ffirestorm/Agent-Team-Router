from typing import Protocol

from atr.core import ATRException, Plan


class Adapter(Protocol):
    def render(self, plan: Plan, current: dict) -> dict[str, bytes | None]: ...


def get_adapter(platform: str):
    if platform != "codex":
        raise ATRException(f"平台 {platform} 尚未支持；V0.1 仅支持 codex")
    from atr.adapters.codex import CodexAdapter
    return CodexAdapter()
