"""Serveur MCP WhatsApp, lecture seule, local (stdio)."""
import functools

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from config import load_config
from db import SchemaError, WhatsAppDB

mcp = MCPServer("whatsapp")
db = WhatsAppDB(load_config())


def explain_errors(fn):
    """Transmet à Claude le message des erreurs prévues au lieu d'une erreur générique."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, SchemaError, FileNotFoundError, PermissionError) as exc:
            raise ToolError(str(exc)) from exc
    return wrapper


@mcp.tool()
@explain_errors
def list_chats(query: str | None = None, limit: int = 30) -> list[dict]:
    """Liste les discussions WhatsApp, de la plus récente à la plus ancienne.

    query : filtre facultatif sur le nom ou le numéro.
    """
    return db.list_chats(query, limit)


@mcp.tool()
@explain_errors
def get_messages(chat: str, since: str | None = None, until: str | None = None, limit: int = 200) -> dict:
    """Messages d'une discussion dans l'ordre chronologique, pour la lire ou la résumer.

    chat : nom du contact ou du groupe (ou une partie unique du nom), ou JID.
    since / until : dates ISO en heure locale, par exemple 2026-10-01 ou 2026-10-01T14:00.
    Renvoie les `limit` messages les plus récents de la période.
    """
    return db.get_messages(chat, since, until, limit)


@mcp.tool()
@explain_errors
def search_messages(query: str, chat: str | None = None, since: str | None = None,
                    until: str | None = None, limit: int = 50) -> list[dict]:
    """Recherche un texte dans les messages (sans tenir compte de la casse), avec 2 messages de contexte avant et après."""
    return db.search_messages(query, chat, since, until, limit)


@mcp.tool()
@explain_errors
def pending_replies(min_hours: float = 24, max_days: float = 30, include_groups: bool = False) -> list[dict]:
    """Discussions où le dernier message vient de l'autre personne et attend une réponse
    depuis au moins `min_hours` heures (et au plus `max_days` jours). Sert à préparer les relances."""
    return db.pending_replies(min_hours, max_days, include_groups)


@mcp.tool()
@explain_errors
def chat_activity(chat: str, days: int = 90) -> dict:
    """Résumé chiffré d'une relation : premier échange, dernier message de chaque côté, volume sur la période."""
    return db.chat_activity(chat, days)


if __name__ == "__main__":
    mcp.run()
