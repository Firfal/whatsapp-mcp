import sqlite3
from datetime import datetime, timedelta

import pytest

from config import Config
from db import SchemaError, WhatsAppDB, copy_database, to_apple

NOW = datetime(2026, 10, 18, 12, 0).astimezone()

SCHEMA = """
CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT, ZPARTNERNAME TEXT,
    ZLASTMESSAGEDATE REAL, ZUNREADCOUNT INTEGER, ZSESSIONTYPE INTEGER);
CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZISFROMME INTEGER,
    ZMESSAGEDATE REAL, ZTEXT TEXT, ZMESSAGETYPE INTEGER, ZFROMJID TEXT, ZGROUPMEMBER INTEGER, ZMEDIAITEM INTEGER, ZPUSHNAME TEXT);
CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZMEMBERJID TEXT, ZCONTACTNAME TEXT, ZFIRSTNAME TEXT);
CREATE TABLE ZWAPROFILEPUSHNAME (Z_PK INTEGER PRIMARY KEY, ZJID TEXT, ZPUSHNAME TEXT);
CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMESSAGE INTEGER, ZTITLE TEXT, ZMEDIALOCALPATH TEXT, ZMOVIEDURATION REAL);
"""


class FakeVoice:
    """Remplace wa-snapshot (copie des vocaux) et whisper."""

    def __init__(self, media_dir):
        self.media_dir = media_dir
        self.fetched, self.transcribed = [], []

    def fetch(self, rels):
        self.fetched += rels
        out = {}
        for rel in rels:
            p = self.media_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"opus")
            out[rel] = p
        return out

    def transcribe(self, paths, model, language):
        self.transcribed += paths
        return {p: f"  transcription de {p.stem} " for p in paths}


def make_db(source, tmp_path, config=None, fake=None):
    snapshot = tmp_path / "cache" / "snap.sqlite"
    fake = fake or FakeVoice(tmp_path / "media")
    return WhatsAppDB(config or Config(), snapshot=snapshot, refresh=lambda snap: copy_database(source, snap),
                      fetch_media=fake.fetch, transcriber=fake.transcribe,
                      transcripts_dir=tmp_path / "transcripts")


def ts(hours_ago: float) -> float:
    return to_apple(NOW - timedelta(hours=hours_ago))


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "ChatStorage.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")  # comme la vraie base
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO ZWACHATSESSION VALUES (?,?,?,?,?,?)", [
        (1, "336399800001@s.whatsapp.net", "Alice Client", ts(48), 2, 0),
        (2, "336399800002@s.whatsapp.net", "Bob", ts(1), 0, 0),
        (3, "12345@g.us", "Équipe projet", ts(30), 0, 1),
        (4, "status@broadcast", None, ts(0.5), 0, 0),
        (7, "123456@lid.status", None, ts(0.4), 0, 3),
        (5, "336399800003@s.whatsapp.net", "Maman", ts(72), 0, 0),
        (8, "33639981001@s.whatsapp.net", "\u202a+33 6 39 98 10 01\u202c", ts(200), 0, 0),
        (10, "336399800010@s.whatsapp.net", "Paul Vocal", ts(800), 0, 0),
        (9, "987654321012345@lid", "+33\xa06\xa039\xa098\xa010\xa002", ts(300), 0, 0),
    ])
    conn.executemany("INSERT INTO ZWAGROUPMEMBER (Z_PK, ZMEMBERJID, ZCONTACTNAME) VALUES (?,?,?)", [
        (1, "336399800009@s.whatsapp.net", ""),
    ])
    conn.executemany("INSERT INTO ZWAPROFILEPUSHNAME VALUES (?,?,?)", [
        (1, "336399800009@s.whatsapp.net", "Chloé"),
    ])
    conn.executemany("INSERT INTO ZWAMESSAGE (Z_PK, ZCHATSESSION, ZISFROMME, ZMESSAGEDATE, ZTEXT, ZMESSAGETYPE, ZFROMJID, ZGROUPMEMBER, ZMEDIAITEM) VALUES (?,?,?,?,?,?,?,?,?)", [
        (1, 1, 1, ts(100), "Bonjour Alice, voici le devis", 0, None, None, None),
        (2, 1, 0, ts(50), "Merci, je regarde le devis", 0, "336399800001@s.whatsapp.net", None, None),
        (3, 1, 0, ts(48), "Vous pouvez m'envoyer la facture ?", 0, "336399800001@s.whatsapp.net", None, None),
        (4, 2, 0, ts(2), "Tu passes demain ?", 0, "336399800002@s.whatsapp.net", None, None),
        (5, 2, 1, ts(1), "Oui", 0, None, None, None),
        (6, 3, 0, ts(30), "Réunion lundi 100% confirmée", 0, None, 1, None),
        (7, 3, 0, ts(29), None, 1, None, 1, None),
        (8, 5, 0, ts(72), "Appelle-moi", 0, "336399800003@s.whatsapp.net", None, None),
        (9, 3, 0, ts(28), None, 1, None, 1, 1),
        (10, 3, 0, ts(27), None, 59, None, 1, None),
        (11, 10, 0, ts(802), None, 3, "336399800010@s.whatsapp.net", None, 2),
        (12, 10, 0, ts(801), None, 3, "336399800010@s.whatsapp.net", None, 3),
        (13, 10, 1, ts(800), "Bien reçu", 0, None, None, None),
    ])
    conn.executemany("INSERT INTO ZWAMEDIAITEM VALUES (?,?,?,?,?)", [
        (1, 9, "Plan du bureau", None, None),
        (2, 11, None, "Media/336399800010@s.whatsapp.net/a/b/aaaa-1111.opus", 42.4),
        (3, 12, None, None, 7),
    ])
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def db(source, tmp_path):
    return make_db(source, tmp_path)


def test_list_chats_hides_status_and_orders(db):
    names = [c["name"] for c in db.list_chats()]
    assert names == ["Bob", "Équipe projet", "Alice Client", "Maman", "+33 6 39 98 10 01", "+33\xa06\xa039\xa098\xa010\xa002", "Paul Vocal"]


def test_chat_by_phone_number(db):
    assert db.get_messages("06 39 98 10 01")["chat"] == "+33 6 39 98 10 01"
    assert db.get_messages("+33639981001")["chat"] == "+33 6 39 98 10 01"
    assert db.get_messages("06 39 98 10 02")["jid"] == "987654321012345@lid"


def test_list_chats_query(db):
    [c] = db.list_chats(query="alice")
    assert c["unread"] == 2
    assert c["last_message"]["text"] == "Vous pouvez m'envoyer la facture ?"


def test_get_messages_chronological_and_senders(db):
    res = db.get_messages("alice")
    assert [m["from"] for m in res["messages"]] == ["moi", "Alice Client", "Alice Client"]
    assert res["messages"][0]["text"].startswith("Bonjour")


def test_get_messages_limit_keeps_latest(db):
    res = db.get_messages("Alice Client", limit=1)
    assert [m["id"] for m in res["messages"]] == [3]
    assert res["truncated"]


def test_group_sender_and_media_label(db):
    msgs = db.get_messages("12345@g.us")["messages"]
    assert msgs[0]["from"] == "Chloé"
    assert msgs[1]["text"] == "[image]"
    assert msgs[2]["text"] == "[image: Plan du bureau]"
    assert msgs[3]["text"] == "[autre contenu, type 59]"


def test_get_messages_since(db):
    since = (NOW - timedelta(hours=60)).strftime("%Y-%m-%dT%H:%M")
    assert [m["id"] for m in db.get_messages("alice", since=since)["messages"]] == [2, 3]


def test_ambiguous_chat(db, source):
    conn = sqlite3.connect(source)
    conn.execute("INSERT INTO ZWACHATSESSION VALUES (6, 'x@s.whatsapp.net', 'Alice Perso', 0, 0, 0)")
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="Plusieurs"):
        db.get_messages("alice")


def test_search_with_context_and_literal_percent(db):
    hits = db.search_messages("devis")
    assert [h["match"]["id"] for h in hits] == [2, 1]
    assert [m["id"] for m in hits[0]["context_after"]] == [3]
    assert [h["match"]["id"] for h in db.search_messages("100%")] == [6]
    assert db.search_messages("1_0") == []


def test_pending_replies(db):
    res = db.pending_replies(min_hours=24, now=NOW)
    assert [r["chat"] for r in res] == ["Maman", "Alice Client"]
    assert [m["id"] for m in res[1]["unanswered_messages"]] == [2, 3]
    assert [r["chat"] for r in db.pending_replies(min_hours=24, max_days=2.5, now=NOW)] == ["Alice Client"]
    with_groups = db.pending_replies(min_hours=24, include_groups=True, now=NOW)
    assert "Équipe projet" in [r["chat"] for r in with_groups]


def test_exclusion_applies_everywhere(source, tmp_path):
    db = make_db(source, tmp_path, Config(exclude_chats=["maman"]))
    assert "Maman" not in [c["name"] for c in db.list_chats()]
    assert db.search_messages("Appelle") == []
    assert "Maman" not in [r["chat"] for r in db.pending_replies(now=NOW)]
    with pytest.raises(ValueError):
        db.get_messages("Maman")


def test_only_chats(source, tmp_path):
    db = make_db(source, tmp_path, Config(only_chats=["Bob"]))
    assert [c["name"] for c in db.list_chats()] == ["Bob"]


def test_chat_activity(db):
    res = db.chat_activity("alice", days=3, now=NOW)
    window = res["last_3_days"]
    assert (window["messages_from_me"], window["messages_from_them"]) == (0, 2)
    assert window["last"] == res["their_last_message"]
    assert res["my_last_message"] < res["their_last_message"]


def test_schema_change_is_reported(source, tmp_path):
    conn = sqlite3.connect(source)
    conn.execute("ALTER TABLE ZWAMESSAGE RENAME COLUMN ZTEXT TO ZBODY")
    conn.commit()
    conn.close()
    db = make_db(source, tmp_path)
    with pytest.raises(SchemaError, match="ZWAMESSAGE.ZTEXT"):
        db.list_chats()


def test_source_untouched(db, source):
    before = source.read_bytes()
    db.list_chats()
    assert source.read_bytes() == before


def test_voice_labels_before_transcription(db):
    msgs = db.get_messages("Paul Vocal")["messages"]
    assert msgs[0]["text"] == "[vocal 0:42, pas encore transcrit]"
    assert msgs[1]["text"] == "[vocal 0:07, fichier non téléchargé sur le Mac]"


def test_transcribe_voice_notes_and_cache(source, tmp_path):
    fake = FakeVoice(tmp_path / "media")
    db = make_db(source, tmp_path, fake=fake)
    res = db.transcribe_voice_notes(chat="Paul Vocal")
    assert res["transcribed"] == 1
    first, second = res["voice_notes"]
    assert first["transcript"] == "transcription de aaaa-1111"
    assert first["duration"] == "0:42"
    assert second["transcript"] is None and "non téléchargé" in second["status"]
    assert fake.fetched == ["Media/336399800010@s.whatsapp.net/a/b/aaaa-1111.opus"]

    # deuxième appel : rien n'est recopié ni retranscrit
    assert db.transcribe_voice_notes(chat="Paul Vocal")["transcribed"] == 0
    assert len(fake.transcribed) == 1

    # la transcription apparaît dans get_messages et est trouvée par search_messages
    assert db.get_messages("Paul Vocal")["messages"][0]["text"] == "[vocal 0:42] « transcription de aaaa-1111 »"
    assert [h["match"]["id"] for h in db.search_messages("TRANSCRIPTION DE")] == [11]


def test_transcribe_by_message_ids(source, tmp_path):
    fake = FakeVoice(tmp_path / "media")
    db = make_db(source, tmp_path, fake=fake)
    assert [v["id"] for v in db.transcribe_voice_notes(message_ids=[12])["voice_notes"]] == [12]
    assert fake.transcribed == []


def test_excluded_chat_voice_notes_never_fetched(source, tmp_path):
    fake = FakeVoice(tmp_path / "media")
    db = make_db(source, tmp_path, Config(exclude_chats=["Paul Vocal"]), fake=fake)
    assert db.transcribe_voice_notes()["voice_notes"] == []
    assert fake.fetched == [] and fake.transcribed == []
