import hashlib
import json
import sqlite3
import time
from pathlib import Path

DEFAULT_PATH = Path(".havoc") / "cache.db"


def make_key(messages, tools=None, temperature=0.0, scope=""):
    payload = {
        "messages": messages,
        "tools": tools or [],
        "temperature": temperature,
        "scope": scope,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class Cache:
    def __init__(self, path=DEFAULT_PATH):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "create table if not exists responses ("
            "key text primary key, provider text, model text, "
            "body text not null, created real not null)"
        )
        self.db.commit()

    def get(self, key):
        row = self.db.execute(
            "select body from responses where key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def put(self, key, provider, model, body):
        self.db.execute(
            "insert or replace into responses values (?, ?, ?, ?, ?)",
            (key, provider, model, json.dumps(body), time.time()),
        )
        self.db.commit()

    def count(self):
        return self.db.execute("select count(*) from responses").fetchone()[0]

    def close(self):
        self.db.close()
