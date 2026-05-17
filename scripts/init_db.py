"""
data/reports.db を作成し、スキーマと初期シードを投入する。

実行例:
  python scripts/init_db.py            # 新規作成(既存DBがあればエラー)
  python scripts/init_db.py --force    # 既存DBを削除して作り直す
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "reports.db"

# スキーマ。最終仕様(キャストは kurofuku_id FK + shift + status + quit_date 追加版)
SCHEMA = """
CREATE TABLE kurofukus (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL
);

CREATE TABLE casts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  kurofuku_id INTEGER NOT NULL,
  shift TEXT NOT NULL,                       -- 'night' / 'day'
  status TEXT NOT NULL DEFAULT 'active',     -- 'active' / 'quit'
  quit_date TEXT,                            -- YYYY-MM-DD(status='quit'のときのみ)
  created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY(kurofuku_id) REFERENCES kurofukus(id)
);

CREATE TABLE initiatives (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  category TEXT NOT NULL,                    -- 'cast' / 'quit_risk' / 'standalone'
  description TEXT,
  is_active INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE cast_initiative_status (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cast_id INTEGER NOT NULL,
  initiative_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'not_started', -- 'not_started' / 'in_progress' / 'done'
  comment TEXT,
  updated_at TEXT DEFAULT (datetime('now')),
  event_date TEXT,                            -- バースデー(施策3)開催予定日 YYYY-MM-DD。他施策は未使用
  FOREIGN KEY(cast_id) REFERENCES casts(id),
  FOREIGN KEY(initiative_id) REFERENCES initiatives(id),
  UNIQUE(cast_id, initiative_id)
);

CREATE TABLE reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_date TEXT NOT NULL,                 -- YYYY-MM-DD
  cast_id INTEGER NOT NULL,
  kurofuku_id INTEGER NOT NULL,
  initiative_id INTEGER NOT NULL,
  content TEXT NOT NULL,
  reaction TEXT,                             -- 'positive' / 'neutral' / 'negative' / NULL
  raw_text TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY(cast_id) REFERENCES casts(id),
  FOREIGN KEY(kurofuku_id) REFERENCES kurofukus(id),
  FOREIGN KEY(initiative_id) REFERENCES initiatives(id)
);

CREATE TABLE quit_risks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cast_id INTEGER UNIQUE NOT NULL,
  certainty TEXT NOT NULL,                   -- 'confirmed' / 'likely'
  expected_quit_date TEXT,                   -- YYYY-MM-DD or NULL
  reason TEXT,
  updated_at TEXT DEFAULT (datetime('now')),
  is_resolved INTEGER DEFAULT 0,
  FOREIGN KEY(cast_id) REFERENCES casts(id)
);

CREATE TABLE restaurants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT,
  status TEXT DEFAULT 'not_visited',
  note TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

-- よく引くクエリ用のインデックス
CREATE INDEX idx_reports_date ON reports(report_date);
CREATE INDEX idx_reports_cast ON reports(cast_id);
CREATE INDEX idx_reports_initiative ON reports(initiative_id);
CREATE INDEX idx_reports_kurofuku ON reports(kurofuku_id);
"""

# 初期シード
KUROFUKU_NAMES = ["五上", "ひろし", "川田", "鴇田", "向原"]

# 施策。id=1〜5 順を維持(運用便宜のため)
# description はビュー上にも表示されるため、内部のカラム名(in_progress/done 等)は出さない。
INITIATIVES = [
    ("TikTok動画買取", "cast",       "データ受領で完了"),
    ("紹介CPの周知",   "cast",       "実際に紹介してくれたら完了"),
    ("バースデーイベント開催のお願い", "cast", "打診したが調整中 → 日程確定で完了"),
    ("ひろしさん顔きき計画", "standalone", "店周辺の飲食店リストを名刺持って回る(別管理)"),
    ("辞めそうな子の見える化", "quit_risk", "退店リスクは別テーブルで管理"),
]

# キャスト。(name, 担当黒服, shift)。同名なし前提。
CASTS = [
    # 夜・五上(12)
    ("えま",   "五上", "night"),
    ("みお",   "五上", "night"),
    ("ありん", "五上", "night"),
    ("まなみ", "五上", "night"),
    ("かりな", "五上", "night"),
    ("はるな", "五上", "night"),
    ("まお",   "五上", "night"),
    ("まな",   "五上", "night"),
    ("ゆう",   "五上", "night"),
    ("かの",   "五上", "night"),
    ("あん",   "五上", "night"),
    ("ひかり", "五上", "night"),
    # 夜・ひろし(12)
    ("りり",   "ひろし", "night"),
    ("かんな", "ひろし", "night"),
    ("みなみ", "ひろし", "night"),
    ("はづき", "ひろし", "night"),
    ("ゆうり", "ひろし", "night"),
    ("のあ",   "ひろし", "night"),
    ("もえ",   "ひろし", "night"),
    ("るい",   "ひろし", "night"),
    ("るう",   "ひろし", "night"),
    ("あいみ", "ひろし", "night"),
    ("かえで", "ひろし", "night"),
    ("りおん", "ひろし", "night"),
    # 夜・川田(1)
    ("くう",   "川田", "night"),
    # 昼・ひろし(2)
    ("ゆりあ", "ひろし", "day"),
    ("あきな", "ひろし", "day"),
    # 昼・鴇田(7)
    ("のん",   "鴇田", "day"),
    ("みく",   "鴇田", "day"),
    ("あすか", "鴇田", "day"),
    ("ゆうか", "鴇田", "day"),
    ("れな",   "鴇田", "day"),
    ("ちなつ", "鴇田", "day"),
    ("ゆりか", "鴇田", "day"),
    # 昼・向原(7)
    ("りお",   "向原", "day"),
    ("らむ",   "向原", "day"),
    ("もか",   "向原", "day"),
    ("ももか", "向原", "day"),
    ("ゆゆ",   "向原", "day"),
    ("せり",   "向原", "day"),
    ("れん",   "向原", "day"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="reports.db を初期化してシードを投入する")
    parser.add_argument("--force", action="store_true", help="既存DBを削除して作り直す")
    args = parser.parse_args()

    if DB_PATH.exists():
        if not args.force:
            print(f"ERROR: {DB_PATH} は既に存在します。再作成するには --force を付けてください。", file=sys.stderr)
            return 1
        os.remove(DB_PATH)
        print(f"既存DBを削除: {DB_PATH}")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(SCHEMA)

        # 黒服
        conn.executemany(
            "INSERT INTO kurofukus(name) VALUES (?)",
            [(n,) for n in KUROFUKU_NAMES],
        )

        # 施策
        conn.executemany(
            "INSERT INTO initiatives(name, category, description) VALUES (?, ?, ?)",
            INITIATIVES,
        )

        # キャスト(黒服名→idの解決)
        kurofuku_ids = {
            row[1]: row[0]
            for row in conn.execute("SELECT id, name FROM kurofukus").fetchall()
        }
        for name, kurofuku_name, shift in CASTS:
            kid = kurofuku_ids.get(kurofuku_name)
            if kid is None:
                raise RuntimeError(f"未知の黒服: {kurofuku_name} (cast={name})")
            conn.execute(
                "INSERT INTO casts(name, kurofuku_id, shift) VALUES (?, ?, ?)",
                (name, kid, shift),
            )

        conn.commit()
    finally:
        conn.close()

    # 件数確認
    conn = sqlite3.connect(DB_PATH)
    try:
        counts = {
            "kurofukus": conn.execute("SELECT COUNT(*) FROM kurofukus").fetchone()[0],
            "initiatives": conn.execute("SELECT COUNT(*) FROM initiatives").fetchone()[0],
            "casts": conn.execute("SELECT COUNT(*) FROM casts").fetchone()[0],
            "casts(night)": conn.execute("SELECT COUNT(*) FROM casts WHERE shift='night'").fetchone()[0],
            "casts(day)": conn.execute("SELECT COUNT(*) FROM casts WHERE shift='day'").fetchone()[0],
        }
    finally:
        conn.close()

    print(f"DB を作成しました: {DB_PATH}")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
