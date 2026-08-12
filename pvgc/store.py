"""SQLite persistence. Plain SQL, no ORM."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pvgc.config import DB_PATH, FORMAT_ID, STATS_CUTOFF, STATS_MONTH
from pvgc.team import Team

SCHEMA = """
CREATE TABLE IF NOT EXISTS team (
    id INTEGER PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,
    paste TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('gauntlet', 'candidate')),
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS run (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    format_id TEXT NOT NULL,
    stats_month TEXT NOT NULL,
    cutoff INTEGER NOT NULL,
    gauntlet_hash TEXT NOT NULL,
    n_battles INTEGER NOT NULL,
    stats_from_cache INTEGER NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS matchup (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES run(id),
    candidate_id INTEGER NOT NULL REFERENCES team(id),
    opponent_id INTEGER NOT NULL REFERENCES team(id),
    n INTEGER NOT NULL,
    wins INTEGER NOT NULL,
    losses INTEGER NOT NULL,
    failed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS battle (
    id INTEGER PRIMARY KEY,
    matchup_id INTEGER NOT NULL REFERENCES matchup(id),
    seed INTEGER,
    winner TEXT,
    turns INTEGER,
    bring_a_json TEXT NOT NULL DEFAULT '[]',
    bring_b_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_matchup_run ON matchup(run_id, candidate_id);
CREATE INDEX IF NOT EXISTS idx_battle_matchup ON battle(matchup_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path = DB_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def add_team(self, team: Team, role: str, source: str,
                 meta: dict | None = None) -> int:
        h = team.hash()
        row = self.conn.execute("SELECT id FROM team WHERE hash = ?", (h,)).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO team (hash, paste, role, source, created_at, meta_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, team.to_paste(), role, source, _now(), json.dumps(meta or {})),
        )
        self.conn.commit()
        return cur.lastrowid

    def start_run(self, gauntlet_hash: str, n_battles: int, from_cache: bool,
                  notes: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO run (started_at, format_id, stats_month, cutoff,"
            " gauntlet_hash, n_battles, stats_from_cache, notes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), FORMAT_ID, STATS_MONTH, STATS_CUTOFF, gauntlet_hash,
             n_battles, int(from_cache), notes),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_matchup(self, run_id: int, candidate_id: int, opponent_id: int,
                    n: int, wins: int, losses: int, failed: int = 0) -> int:
        if wins + losses != n:
            raise ValueError(
                f"matchup integrity: n={n} but wins+losses={wins + losses}. "
                "n must be derived from recorded battles, never the requested count."
            )
        cur = self.conn.execute(
            "INSERT INTO matchup (run_id, candidate_id, opponent_id, n, wins,"
            " losses, failed) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, candidate_id, opponent_id, n, wins, losses, failed),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_battle(self, matchup_id: int, seed: int | None, winner: str | None,
                   turns: int | None, bring_a: list, bring_b: list) -> int:
        cur = self.conn.execute(
            "INSERT INTO battle (matchup_id, seed, winner, turns, bring_a_json,"
            " bring_b_json) VALUES (?, ?, ?, ?, ?, ?)",
            (matchup_id, seed, winner, turns,
             json.dumps(list(bring_a or [])), json.dumps(list(bring_b or []))),
        )
        self.conn.commit()
        return cur.lastrowid

    def matchups_for(self, run_id: int, candidate_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM matchup WHERE run_id = ? AND candidate_id = ?",
            (run_id, candidate_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def battles_for(self, matchup_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM battle WHERE matchup_id = ?", (matchup_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["bring_a"] = json.loads(d.pop("bring_a_json"))
            d["bring_b"] = json.loads(d.pop("bring_b_json"))
            out.append(d)
        return out

    def get_run(self, run_id: int) -> dict:
        return dict(self.conn.execute(
            "SELECT * FROM run WHERE id = ?", (run_id,)
        ).fetchone())

    def team_paste(self, team_id: int) -> str:
        return self.conn.execute(
            "SELECT paste FROM team WHERE id = ?", (team_id,)
        ).fetchone()["paste"]
