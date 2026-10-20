"""Lecture seule de ChatStorage.sqlite (WhatsApp Desktop macOS) via une copie locale."""
import os
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from config import Config

SNAPSHOT = Path("~/Library/Caches/whatsapp-mcp/snapshot.sqlite").expanduser()
SNAPSHOT_LOG = Path("~/Library/Logs/whatsapp-mcp/snapshot.log").expanduser()
LAUNCHD_LABEL = "com.whatsapp-mcp.snapshot"
MAX_AGE = 60  # secondes avant de redemander une copie
WAIT = 20  # secondes d'attente maximum pour une nouvelle copie

APPLE_EPOCH = 978307200  # 2001-01-01 en secondes Unix

REQUIRED = {
    "ZWACHATSESSION": {"Z_PK", "ZCONTACTJID", "ZPARTNERNAME", "ZLASTMESSAGEDATE", "ZUNREADCOUNT", "ZSESSIONTYPE"},
    "ZWAMESSAGE": {"Z_PK", "ZCHATSESSION", "ZISFROMME", "ZMESSAGEDATE", "ZTEXT", "ZMESSAGETYPE", "ZFROMJID", "ZGROUPMEMBER", "ZMEDIAITEM", "ZPUSHNAME"},
    "ZWAGROUPMEMBER": {"Z_PK", "ZMEMBERJID", "ZCONTACTNAME", "ZFIRSTNAME"},
    "ZWAMEDIAITEM": {"Z_PK", "ZTITLE"},
    "ZWAPROFILEPUSHNAME": {"ZJID", "ZPUSHNAME"},
}

MESSAGE_TYPES = {
    1: "image", 2: "vidéo", 3: "vocal", 4: "contact", 5: "position",
    6: "événement de groupe", 7: "lien", 8: "document", 11: "gif",
    14: "message supprimé", 15: "sticker",
}

# Statuts (status@broadcast, <numéro>@status, <id>@lid.status) : pas des discussions.
HIDDEN_JID_PATTERN = "%status%"


# Marques de direction que WhatsApp met autour des numéros (‪+33 6 …‬).
_BIDI = dict.fromkeys(map(ord, "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"))


def clean_name(name: str | None) -> str | None:
    return name.translate(_BIDI).strip() if name else name


class SchemaError(RuntimeError):
    pass


def to_datetime(apple_ts: float | None) -> datetime | None:
    if apple_ts is None:
        return None
    return datetime.fromtimestamp(apple_ts + APPLE_EPOCH).astimezone()


def to_apple(dt: datetime) -> float:
    return dt.timestamp() - APPLE_EPOCH


def parse_date(value: str | None) -> float | None:
    """ISO (2026-10-01 ou 2026-10-01T14:00) en heure locale -> horodatage Apple."""
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return to_apple(dt)


def copy_database(source: Path, snapshot: Path) -> None:
    """Copie cohérente via l'API backup, sans jamais écrire dans l'original (utilisé par les tests ;
    en production c'est wa-snapshot qui fait la copie)."""
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    tmp = snapshot.with_suffix(".tmp")
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    tmp.replace(snapshot)


def _last_log_line() -> str:
    try:
        lines = SNAPSHOT_LOG.read_text().strip().splitlines()
        return lines[-1] if lines else ""
    except OSError:
        return ""


def request_snapshot(snapshot: Path = SNAPSHOT) -> None:
    """Demande à launchd de relancer wa-snapshot et attend la nouvelle copie."""
    before = snapshot.stat().st_mtime if snapshot.exists() else 0
    subprocess.run(
        ["launchctl", "kickstart", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
        capture_output=True, check=False,
    )
    deadline = time.time() + WAIT
    while time.time() < deadline:
        if snapshot.exists() and snapshot.stat().st_mtime > before:
            return
        last = _last_log_line()
        if "ERREUR" in last and before == 0:
            break
        time.sleep(0.3)
    if snapshot.exists():
        return  # copie plus ancienne, mais utilisable
    last = _last_log_line()
    hint = (
        " Le programme wa-snapshot n'a pas l'accès complet au disque : ajoutez "
        "~/Library/Application Support/whatsapp-mcp/wa-snapshot dans Réglages Système > "
        "Confidentialité et sécurité > Accès complet au disque."
        if "authorization denied" in last or "unable to open" in last
        else " Vérifiez que install.sh a été lancé (launchctl print gui/$UID/" + LAUNCHD_LABEL + ")."
    )
    raise PermissionError(f"Aucune copie de la base WhatsApp disponible.{hint} Dernier journal : {last or 'vide'}")


def ensure_fresh(snapshot: Path = SNAPSHOT) -> None:
    if not snapshot.exists() or time.time() - snapshot.stat().st_mtime > MAX_AGE:
        request_snapshot(snapshot)


def check_schema(conn: sqlite3.Connection) -> None:
    missing = []
    for table, cols in REQUIRED.items():
        found = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not found:
            missing.append(f"table {table}")
        else:
            missing += [f"{table}.{c}" for c in sorted(cols - found)]
    if missing:
        raise SchemaError(
            "Le schéma WhatsApp a changé (mise à jour de l'app ?). Manquant : " + ", ".join(missing)
        )


class WhatsAppDB:
    def __init__(self, config: Config, snapshot: Path = SNAPSHOT,
                 refresh: Callable[[Path], None] = ensure_fresh):
        self.config = config
        self.snapshot = snapshot
        self.refresh = refresh

    def _connect(self) -> sqlite3.Connection:
        self.refresh(self.snapshot)
        conn = sqlite3.connect(f"file:{self.snapshot}?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        conn.create_function("clean_name", 1, clean_name, deterministic=True)
        check_schema(conn)
        return conn

    # --- discussions ---------------------------------------------------------

    def _chats(self, conn: sqlite3.Connection) -> list[sqlite3.Row]:
        rows = conn.execute(
            f"""SELECT Z_PK, ZCONTACTJID, clean_name(ZPARTNERNAME) AS ZPARTNERNAME, ZLASTMESSAGEDATE, ZUNREADCOUNT, ZSESSIONTYPE
                FROM ZWACHATSESSION
                WHERE ZCONTACTJID NOT LIKE ?
                ORDER BY ZLASTMESSAGEDATE DESC""",
            (HIDDEN_JID_PATTERN,),
        ).fetchall()
        return [r for r in rows if self.config.allows(r["ZPARTNERNAME"], r["ZCONTACTJID"])]

    def _resolve_chat(self, conn: sqlite3.Connection, chat: str) -> sqlite3.Row:
        chats = self._chats(conn)
        needle = clean_name(chat).lower()
        # numéro saisi librement (06 63…, +33 6 63…) comparé à la fin du JID, sans les zéros de tête
        digits = "".join(ch for ch in needle if ch.isdigit()).lstrip("0")
        for test in (
            lambda r: (r["ZCONTACTJID"] or "").lower() == needle,
            lambda r: len(digits) >= 8 and (
                (r["ZCONTACTJID"] or "").split("@")[0].endswith(digits)
                # JID en @lid : le numéro n'apparaît que dans le nom affiché
                or "".join(ch for ch in r["ZPARTNERNAME"] or "" if ch.isdigit()).endswith(digits)
            ),
            lambda r: (r["ZPARTNERNAME"] or "").lower() == needle,
            lambda r: needle in (r["ZPARTNERNAME"] or "").lower(),
        ):
            hits = [r for r in chats if test(r)]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                names = ", ".join(f"{r['ZPARTNERNAME']} ({r['ZCONTACTJID']})" for r in hits[:10])
                raise ValueError(f"Plusieurs discussions correspondent à « {chat} » : {names}")
        raise ValueError(f"Aucune discussion autorisée ne correspond à « {chat} ».")

    @staticmethod
    def _is_group(row: sqlite3.Row) -> bool:
        return (row["ZCONTACTJID"] or "").endswith("@g.us")

    # --- messages ------------------------------------------------------------

    _MESSAGE_SELECT = """
        SELECT m.Z_PK, m.ZCHATSESSION, m.ZISFROMME, m.ZMESSAGEDATE, m.ZTEXT, m.ZMESSAGETYPE, mi.ZTITLE AS media_title,
               clean_name(c.ZPARTNERNAME) AS chat_name, c.ZCONTACTJID AS chat_jid,
               clean_name(COALESCE(NULLIF(gm.ZCONTACTNAME, ''), NULLIF(gm.ZFIRSTNAME, ''), NULLIF(pn.ZPUSHNAME, ''),
                        NULLIF(m.ZPUSHNAME, ''), gm.ZMEMBERJID, m.ZFROMJID)) AS group_sender
        FROM ZWAMESSAGE m
        JOIN ZWACHATSESSION c ON c.Z_PK = m.ZCHATSESSION
        LEFT JOIN ZWAGROUPMEMBER gm ON gm.Z_PK = m.ZGROUPMEMBER
        LEFT JOIN ZWAPROFILEPUSHNAME pn ON pn.ZJID = COALESCE(gm.ZMEMBERJID, m.ZFROMJID)
        LEFT JOIN ZWAMEDIAITEM mi ON mi.Z_PK = m.ZMEDIAITEM
    """

    @staticmethod
    def _format_message(r: sqlite3.Row) -> dict:
        if r["ZISFROMME"]:
            sender = "moi"
        elif (r["chat_jid"] or "").endswith("@g.us"):
            sender = r["group_sender"] or "?"
        else:
            sender = r["chat_name"] or r["chat_jid"]
        mtype = r["ZMESSAGETYPE"] or 0
        # légende d'une photo ou d'une vidéo, nom d'un document
        text = r["ZTEXT"] or r["media_title"]
        if mtype != 0:
            label = MESSAGE_TYPES.get(mtype, f"autre contenu, type {mtype}")
            text = f"[{label}: {text}]" if text else f"[{label}]"
        return {
            "id": r["Z_PK"],
            "date": to_datetime(r["ZMESSAGEDATE"]).isoformat(timespec="minutes"),
            "chat": r["chat_name"],
            "from": sender,
            "text": text or "",
        }

    def _allowed_ids(self, conn: sqlite3.Connection) -> list[int]:
        return [r["Z_PK"] for r in self._chats(conn)]

    # --- outils --------------------------------------------------------------

    def list_chats(self, query: str | None = None, limit: int = 30) -> list[dict]:
        with self._connect() as conn:
            chats = self._chats(conn)
            if query:
                q = query.lower()
                chats = [r for r in chats if q in (r["ZPARTNERNAME"] or "").lower() or q in (r["ZCONTACTJID"] or "")]
            out = []
            for r in chats[:limit]:
                last = conn.execute(
                    self._MESSAGE_SELECT + " WHERE m.ZCHATSESSION = ? ORDER BY m.ZMESSAGEDATE DESC LIMIT 1",
                    (r["Z_PK"],),
                ).fetchone()
                out.append({
                    "name": r["ZPARTNERNAME"],
                    "jid": r["ZCONTACTJID"],
                    "group": self._is_group(r),
                    "unread": r["ZUNREADCOUNT"] or 0,
                    "last_date": to_datetime(r["ZLASTMESSAGEDATE"]).isoformat(timespec="minutes") if r["ZLASTMESSAGEDATE"] else None,
                    "last_message": self._format_message(last) if last else None,
                })
            return out

    def get_messages(self, chat: str, since: str | None = None, until: str | None = None, limit: int = 200) -> dict:
        with self._connect() as conn:
            c = self._resolve_chat(conn, chat)
            sql = self._MESSAGE_SELECT + " WHERE m.ZCHATSESSION = ?"
            params: list = [c["Z_PK"]]
            if (s := parse_date(since)) is not None:
                sql += " AND m.ZMESSAGEDATE >= ?"
                params.append(s)
            if (u := parse_date(until)) is not None:
                sql += " AND m.ZMESSAGEDATE < ?"
                params.append(u)
            # les N plus récents, remis dans l'ordre chronologique
            sql += " ORDER BY m.ZMESSAGEDATE DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()[::-1]
            return {
                "chat": c["ZPARTNERNAME"],
                "jid": c["ZCONTACTJID"],
                "count": len(rows),
                "truncated": len(rows) == limit,
                "messages": [self._format_message(r) for r in rows],
            }

    def search_messages(self, query: str, chat: str | None = None, since: str | None = None,
                        until: str | None = None, limit: int = 50, context: int = 2) -> list[dict]:
        with self._connect() as conn:
            if chat:
                ids = [self._resolve_chat(conn, chat)["Z_PK"]]
            else:
                ids = self._allowed_ids(conn)
            if not ids:
                return []
            sql = (self._MESSAGE_SELECT
                   + f" WHERE m.ZCHATSESSION IN ({','.join('?' * len(ids))}) AND m.ZTEXT LIKE ? ESCAPE '\\'")
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params: list = [*ids, f"%{escaped}%"]
            if (s := parse_date(since)) is not None:
                sql += " AND m.ZMESSAGEDATE >= ?"
                params.append(s)
            if (u := parse_date(until)) is not None:
                sql += " AND m.ZMESSAGEDATE < ?"
                params.append(u)
            sql += " ORDER BY m.ZMESSAGEDATE DESC LIMIT ?"
            params.append(limit)
            out = []
            for hit in conn.execute(sql, params).fetchall():
                before = conn.execute(
                    self._MESSAGE_SELECT + " WHERE m.ZCHATSESSION = ? AND m.ZMESSAGEDATE < ? ORDER BY m.ZMESSAGEDATE DESC LIMIT ?",
                    (hit["ZCHATSESSION"], hit["ZMESSAGEDATE"], context),
                ).fetchall()[::-1]
                after = conn.execute(
                    self._MESSAGE_SELECT + " WHERE m.ZCHATSESSION = ? AND m.ZMESSAGEDATE > ? ORDER BY m.ZMESSAGEDATE ASC LIMIT ?",
                    (hit["ZCHATSESSION"], hit["ZMESSAGEDATE"], context),
                ).fetchall()
                out.append({
                    "match": self._format_message(hit),
                    "context_before": [self._format_message(r) for r in before],
                    "context_after": [self._format_message(r) for r in after],
                })
            return out

    def pending_replies(self, min_hours: float = 24, max_days: float = 30, include_groups: bool = False,
                        now: datetime | None = None) -> list[dict]:
        now_apple = to_apple(now or datetime.now().astimezone())
        with self._connect() as conn:
            out = []
            for c in self._chats(conn):
                if self._is_group(c) and not include_groups:
                    continue
                rows = conn.execute(
                    self._MESSAGE_SELECT
                    + " WHERE m.ZCHATSESSION = ? AND COALESCE(m.ZMESSAGETYPE, 0) NOT IN (6, 14)"
                      " ORDER BY m.ZMESSAGEDATE DESC LIMIT 20",
                    (c["Z_PK"],),
                ).fetchall()
                if not rows or rows[0]["ZISFROMME"]:
                    continue
                age_h = (now_apple - rows[0]["ZMESSAGEDATE"]) / 3600
                if not (min_hours <= age_h <= max_days * 24):
                    continue
                waiting = []
                for r in rows:
                    if r["ZISFROMME"]:
                        break
                    waiting.append(r)
                out.append({
                    "chat": c["ZPARTNERNAME"],
                    "jid": c["ZCONTACTJID"],
                    "group": self._is_group(c),
                    "hours_waiting": round(age_h, 1),
                    "unanswered_messages": [self._format_message(r) for r in waiting[::-1]],
                })
            return sorted(out, key=lambda x: -x["hours_waiting"])

    def chat_activity(self, chat: str, days: int = 90, now: datetime | None = None) -> dict:
        now_apple = to_apple(now or datetime.now().astimezone())
        since = now_apple - days * 86400
        with self._connect() as conn:
            c = self._resolve_chat(conn, chat)
            stats = conn.execute(
                """SELECT MIN(ZMESSAGEDATE) AS first, MAX(ZMESSAGEDATE) AS last,
                          SUM(ZISFROMME = 1) AS mine, SUM(ZISFROMME = 0) AS theirs
                   FROM ZWAMESSAGE WHERE ZCHATSESSION = ? AND ZMESSAGEDATE >= ?""",
                (c["Z_PK"], since),
            ).fetchone()
            overall = conn.execute(
                """SELECT MIN(ZMESSAGEDATE) AS first,
                          MAX(CASE WHEN ZISFROMME = 1 THEN ZMESSAGEDATE END) AS my_last,
                          MAX(CASE WHEN ZISFROMME = 0 THEN ZMESSAGEDATE END) AS their_last
                   FROM ZWAMESSAGE WHERE ZCHATSESSION = ?""",
                (c["Z_PK"],),
            ).fetchone()

            def iso(ts):
                return to_datetime(ts).isoformat(timespec="minutes") if ts is not None else None

            return {
                "chat": c["ZPARTNERNAME"],
                "jid": c["ZCONTACTJID"],
                "first_message_ever": iso(overall["first"]),
                "my_last_message": iso(overall["my_last"]),
                "their_last_message": iso(overall["their_last"]),
                f"last_{days}_days": {
                    "messages_from_me": stats["mine"] or 0,
                    "messages_from_them": stats["theirs"] or 0,
                    "first": iso(stats["first"]),
                    "last": iso(stats["last"]),
                },
            }
