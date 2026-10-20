"""Configuration : discussions exclues ou seules autorisées."""
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("WHATSAPP_MCP_CONFIG", "~/.config/whatsapp-mcp/config.toml")).expanduser()


@dataclass
class Config:
    exclude_chats: list[str] = field(default_factory=list)
    only_chats: list[str] = field(default_factory=list)

    def allows(self, name: str | None, jid: str | None) -> bool:
        keys = {k.lower() for k in (name, jid) if k}
        if self.only_chats and not keys & {c.lower() for c in self.only_chats}:
            return False
        return not keys & {c.lower() for c in self.exclude_chats}


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        return Config()
    data = tomllib.loads(path.read_text())
    return Config(
        exclude_chats=list(data.get("exclude_chats", [])),
        only_chats=list(data.get("only_chats", [])),
    )
