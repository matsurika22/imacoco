"""
reports.db を操作する CLI。自然言語パースは行わない。
Claude Code が報告テキストを対話パースした結果を受けて、ここで DB を読み書きする。

サブコマンド一覧(主なもの):
  参照:
    list-kurofukus
    list-casts [--shift night|day] [--by KUROFUKU] [--status active|quit|all]
    list-initiatives
    list-recent-reports [--cast X] [--initiative N] [--days N]
    show-cast --name X
    show-status --cast X --initiative N

  報告投入:
    add-report --date YYYY-MM-DD --cast X --initiative N --by KUROFUKU
               --content "..." [--reaction positive|neutral|negative]
               [--status not_started|in_progress|done] [--comment "..."]
               [--raw "..."] [--force]
    set-status --cast X --initiative N --status ... [--comment "..."] [--force]
    add-quit-risk --cast X --certainty confirmed|likely [--date YYYY-MM-DD]
                  [--reason "..."] --by KUROFUKU [--report-date YYYY-MM-DD]
                  [--content "..."] [--raw "..."]

  マスタメンテ:
    cast add --name X --by KUROFUKU --shift night|day
    cast rename --from X --to Y
    cast reassign --name X --to KUROFUKU
    cast quit --name X --date YYYY-MM-DD
    kurofuku rename --from X --to Y
    initiative add --name X --category cast|quit_risk|standalone [--description "..."]
    update-quit-risk --cast X [--certainty ...] [--date ...] [--reason ...] [--resolve]

設計メモ:
  - done を簡単に巻き戻さない: 既存 status='done' を non-done に変えるには --force が必要
  - 同じ status の上書きでは updated_at を触らない(comment は最新で上書きする)
  - 重複検出: add-report は同日同キャスト同施策の既存 report があれば --force を要求
  - A 群(category='cast')のみ cast_initiative_status を更新。施策5は quit_risks 専用、施策4はそもそも報告対象外
"""

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "reports.db"


# ---------------------------------------------------------------------------
# DB ヘルパ
# ---------------------------------------------------------------------------

def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        die(f"DBが見つかりません: {DB_PATH}\n  先に `python scripts/init_db.py` を実行してください。")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def find_kurofuku(conn, name: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM kurofukus WHERE name = ?", (name,)).fetchone()
    if row is None:
        all_names = [r["name"] for r in conn.execute("SELECT name FROM kurofukus ORDER BY id")]
        die(f"未登録の黒服: {name}\n  登録済み: {', '.join(all_names)}")
    return row


def find_cast(conn, name: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM casts WHERE name = ?", (name,)).fetchone()
    if row is None:
        # 部分一致候補を出すと親切
        like = conn.execute(
            "SELECT name FROM casts WHERE name LIKE ? ORDER BY name", (f"%{name}%",)
        ).fetchall()
        hint = f"\n  部分一致候補: {', '.join(r['name'] for r in like)}" if like else ""
        die(f"未登録のキャスト: {name}{hint}\n  全候補は `list-casts` で確認してください。")
    return row


def find_initiative(conn, ref) -> sqlite3.Row:
    """ref は id(int / 数字文字列)または名前。"""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        row = conn.execute("SELECT * FROM initiatives WHERE id = ?", (int(ref),)).fetchone()
    else:
        row = conn.execute("SELECT * FROM initiatives WHERE name = ?", (ref,)).fetchone()
    if row is None:
        all_ = conn.execute("SELECT id, name FROM initiatives ORDER BY id").fetchall()
        listing = ", ".join(f"{r['id']}:{r['name']}" for r in all_)
        die(f"未登録の施策: {ref}\n  登録済み: {listing}")
    return row


# ---------------------------------------------------------------------------
# 表示ヘルパ
# ---------------------------------------------------------------------------

def print_table(rows, headers):
    if not rows:
        print("(該当なし)")
        return
    cols = list(zip(*([headers] + [list(r) for r in rows])))
    widths = [max(len(str(v)) for v in col) for col in cols]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for r in rows:
        print(fmt.format(*[("" if v is None else str(v)) for v in r]))


# ---------------------------------------------------------------------------
# 参照系
# ---------------------------------------------------------------------------

def cmd_list_kurofukus(args):
    conn = connect()
    rows = conn.execute("SELECT id, name FROM kurofukus ORDER BY id").fetchall()
    print_table([(r["id"], r["name"]) for r in rows], ["id", "name"])


def cmd_list_casts(args):
    conn = connect()
    sql = ("SELECT c.id, c.name, k.name AS kurofuku, c.shift, c.status, c.quit_date "
           "FROM casts c JOIN kurofukus k ON c.kurofuku_id = k.id WHERE 1=1")
    params = []
    if args.shift:
        sql += " AND c.shift = ?"
        params.append(args.shift)
    if args.by:
        # 検証兼ねて存在チェック
        find_kurofuku(conn, args.by)
        sql += " AND k.name = ?"
        params.append(args.by)
    if args.status != "all":
        sql += " AND c.status = ?"
        params.append(args.status)
    sql += " ORDER BY c.shift, k.name, c.id"
    rows = conn.execute(sql, params).fetchall()
    print_table(
        [(r["id"], r["name"], r["kurofuku"], r["shift"], r["status"], r["quit_date"]) for r in rows],
        ["id", "name", "kurofuku", "shift", "status", "quit_date"],
    )


def cmd_list_initiatives(args):
    conn = connect()
    rows = conn.execute(
        "SELECT id, name, category, is_active, description FROM initiatives ORDER BY id"
    ).fetchall()
    print_table(
        [(r["id"], r["name"], r["category"], r["is_active"], r["description"] or "") for r in rows],
        ["id", "name", "category", "active", "description"],
    )


def cmd_list_recent_reports(args):
    conn = connect()
    sql = ("SELECT r.id, r.report_date, c.name AS cast, i.name AS initiative, "
           "k.name AS kurofuku, r.reaction, r.content "
           "FROM reports r "
           "JOIN casts c ON r.cast_id = c.id "
           "JOIN initiatives i ON r.initiative_id = i.id "
           "JOIN kurofukus k ON r.kurofuku_id = k.id "
           "WHERE 1=1")
    params = []
    if args.cast:
        cast = find_cast(conn, args.cast)
        sql += " AND r.cast_id = ?"
        params.append(cast["id"])
    if args.initiative:
        ini = find_initiative(conn, args.initiative)
        sql += " AND r.initiative_id = ?"
        params.append(ini["id"])
    if args.days:
        sql += f" AND r.report_date >= date('now', '-{int(args.days)} days')"
    sql += " ORDER BY r.report_date DESC, r.id DESC"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = conn.execute(sql, params).fetchall()
    print_table(
        [(r["id"], r["report_date"], r["cast"], r["initiative"], r["kurofuku"], r["reaction"] or "", _shorten(r["content"], 40)) for r in rows],
        ["id", "date", "cast", "initiative", "by", "reaction", "content"],
    )


def _shorten(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def cmd_show_cast(args):
    conn = connect()
    cast = find_cast(conn, args.name)
    kurofuku = conn.execute("SELECT name FROM kurofukus WHERE id = ?", (cast["kurofuku_id"],)).fetchone()
    print(f"== {cast['name']} ==")
    print(f"  担当黒服: {kurofuku['name']}")
    print(f"  シフト: {cast['shift']}")
    print(f"  状態: {cast['status']}" + (f" (退店日 {cast['quit_date']})" if cast["status"] == "quit" else ""))

    print("\n[各施策のステータス]")
    rows = conn.execute(
        "SELECT i.id, i.name, COALESCE(s.status, 'not_started') AS status, s.comment, s.updated_at "
        "FROM initiatives i "
        "LEFT JOIN cast_initiative_status s ON s.initiative_id = i.id AND s.cast_id = ? "
        "WHERE i.category = 'cast' "
        "ORDER BY i.id",
        (cast["id"],),
    ).fetchall()
    print_table(
        [(r["id"], r["name"], r["status"], r["comment"] or "", r["updated_at"] or "") for r in rows],
        ["id", "施策", "status", "comment", "updated_at"],
    )

    qr = conn.execute("SELECT * FROM quit_risks WHERE cast_id = ?", (cast["id"],)).fetchone()
    if qr:
        print("\n[退店リスク]")
        print(f"  確度: {qr['certainty']}")
        print(f"  予定日: {qr['expected_quit_date'] or '(未定)'}")
        print(f"  理由: {qr['reason'] or ''}")
        print(f"  解消済み: {'はい' if qr['is_resolved'] else 'いいえ'}")
        print(f"  最終更新: {qr['updated_at']}")

    print("\n[直近の報告 5件]")
    recent = conn.execute(
        "SELECT r.report_date, i.name AS initiative, k.name AS kurofuku, r.reaction, r.content "
        "FROM reports r "
        "JOIN initiatives i ON r.initiative_id = i.id "
        "JOIN kurofukus k ON r.kurofuku_id = k.id "
        "WHERE r.cast_id = ? "
        "ORDER BY r.report_date DESC, r.id DESC LIMIT 5",
        (cast["id"],),
    ).fetchall()
    print_table(
        [(r["report_date"], r["initiative"], r["kurofuku"], r["reaction"] or "", _shorten(r["content"], 50)) for r in recent],
        ["date", "initiative", "by", "reaction", "content"],
    )


def cmd_show_status(args):
    conn = connect()
    cast = find_cast(conn, args.cast)
    ini = find_initiative(conn, args.initiative)
    row = conn.execute(
        "SELECT status, comment, updated_at FROM cast_initiative_status WHERE cast_id = ? AND initiative_id = ?",
        (cast["id"], ini["id"]),
    ).fetchone()
    if row is None:
        print(f"{cast['name']} × {ini['name']}: not_started (記録なし)")
        return
    print(f"{cast['name']} × {ini['name']}")
    print(f"  status: {row['status']}")
    print(f"  comment: {row['comment'] or ''}")
    print(f"  updated_at: {row['updated_at']}")


# ---------------------------------------------------------------------------
# ステータス更新の共通ロジック
# ---------------------------------------------------------------------------

VALID_STATUSES = ("not_started", "declined", "in_progress", "done")
# 表示ラベル(ヘルプメッセージ等で使う)
STATUS_LABEL = {
    "not_started": "未着手",
    "declined":    "無理そう",
    "in_progress": "進行中",
    "done":        "完了",
}


# 終端状態(これ以上アクション不要):done(完了)と declined(無理そう)。
# どちらも巻き戻すには --force を要求して誤操作を防ぐ。
TERMINAL_STATUSES = ("done", "declined")


def _upsert_status(conn, cast_id: int, initiative_id: int, new_status: str, comment, force: bool):
    """
    cast_initiative_status を upsert する。
      - 既存 status が TERMINAL_STATUSES のいずれかで、別の状態に戻す場合は force 必須
        (done / declined は「終わった案件」なので簡単に巻き戻さない)
      - 既存 status == new_status なら updated_at は触らず、comment のみ最新で上書き
      - それ以外は status / comment / updated_at を更新
    戻り値: 何が起きたかを示す説明文字列
    """
    row = conn.execute(
        "SELECT status, comment FROM cast_initiative_status WHERE cast_id = ? AND initiative_id = ?",
        (cast_id, initiative_id),
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO cast_initiative_status(cast_id, initiative_id, status, comment) VALUES (?, ?, ?, ?)",
            (cast_id, initiative_id, new_status, comment),
        )
        return f"new → {STATUS_LABEL.get(new_status, new_status)}"

    old_status = row["status"]
    if old_status in TERMINAL_STATUSES and new_status != old_status and not force:
        old_label = STATUS_LABEL.get(old_status, old_status)
        new_label = STATUS_LABEL.get(new_status, new_status)
        die(f"既に「{old_label}」です。「{new_label}」に変更するには --force を付けてください。\n"
            f"  (現在の comment: {row['comment'] or ''})")

    if old_status == new_status:
        if comment is not None:
            conn.execute(
                "UPDATE cast_initiative_status SET comment = ? WHERE cast_id = ? AND initiative_id = ?",
                (comment, cast_id, initiative_id),
            )
        return f"unchanged ({STATUS_LABEL.get(old_status, old_status)})"

    conn.execute(
        "UPDATE cast_initiative_status "
        "SET status = ?, comment = COALESCE(?, comment), updated_at = datetime('now') "
        "WHERE cast_id = ? AND initiative_id = ?",
        (new_status, comment, cast_id, initiative_id),
    )
    return f"{STATUS_LABEL.get(old_status, old_status)} → {STATUS_LABEL.get(new_status, new_status)}"


# ---------------------------------------------------------------------------
# 報告投入系
# ---------------------------------------------------------------------------

def cmd_add_report(args):
    conn = connect()
    cast = find_cast(conn, args.cast)
    ini = find_initiative(conn, args.initiative)
    kuro = find_kurofuku(conn, args.by)

    if ini["category"] == "quit_risk":
        die("施策5(退店リスク)は add-report ではなく add-quit-risk を使ってください。")
    if ini["category"] == "standalone":
        die(f"施策 {ini['id']}({ini['name']})は報告対象外(standalone)です。")
    if args.reaction and args.reaction not in ("positive", "neutral", "negative"):
        die(f"reaction の値が不正: {args.reaction}")
    if args.status and args.status not in VALID_STATUSES:
        die(f"status の値が不正: {args.status}")

    # 重複検出
    dup = conn.execute(
        "SELECT id FROM reports WHERE report_date = ? AND cast_id = ? AND initiative_id = ?",
        (args.date, cast["id"], ini["id"]),
    ).fetchone()
    if dup and not args.force:
        die(f"同日同キャスト同施策の報告が既にあります(id={dup['id']})。"
            f"続行するなら --force を付けてください。")

    conn.execute(
        "INSERT INTO reports(report_date, cast_id, kurofuku_id, initiative_id, content, reaction, raw_text) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (args.date, cast["id"], kuro["id"], ini["id"], args.content, args.reaction, args.raw),
    )
    report_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    status_msg = "(status未指定 → cast_initiative_status は更新せず)"
    if args.status:
        status_msg = _upsert_status(conn, cast["id"], ini["id"], args.status, args.comment, args.force)

    conn.commit()
    print(f"報告を追加: id={report_id} ({args.date} {cast['name']} × {ini['name']} by {kuro['name']})")
    print(f"  status: {status_msg}")


def cmd_set_status(args):
    conn = connect()
    cast = find_cast(conn, args.cast)
    ini = find_initiative(conn, args.initiative)
    if ini["category"] != "cast":
        die("set-status は A 群施策(category='cast')のみ対象です。")
    if args.status not in VALID_STATUSES:
        die(f"status の値が不正: {args.status}")
    msg = _upsert_status(conn, cast["id"], ini["id"], args.status, args.comment, args.force)
    conn.commit()
    print(f"{cast['name']} × {ini['name']}: {msg}")


def cmd_add_quit_risk(args):
    conn = connect()
    cast = find_cast(conn, args.cast)
    kuro = find_kurofuku(conn, args.by)
    if args.certainty not in ("confirmed", "likely"):
        die(f"certainty の値が不正: {args.certainty}")

    quit_risk_ini = conn.execute(
        "SELECT * FROM initiatives WHERE category = 'quit_risk'"
    ).fetchone()
    if quit_risk_ini is None:
        die("施策マスタに quit_risk カテゴリの施策がありません。")

    # 報告ログ(任意)
    if args.content:
        conn.execute(
            "INSERT INTO reports(report_date, cast_id, kurofuku_id, initiative_id, content, raw_text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (args.report_date or args.date or args.report_date, cast["id"], kuro["id"],
             quit_risk_ini["id"], args.content, args.raw),
        )

    # quit_risks の upsert
    existing = conn.execute("SELECT * FROM quit_risks WHERE cast_id = ?", (cast["id"],)).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO quit_risks(cast_id, certainty, expected_quit_date, reason) VALUES (?, ?, ?, ?)",
            (cast["id"], args.certainty, args.date, args.reason),
        )
        action = "新規登録"
    else:
        conn.execute(
            "UPDATE quit_risks SET certainty = ?, "
            "expected_quit_date = COALESCE(?, expected_quit_date), "
            "reason = COALESCE(?, reason), "
            "updated_at = datetime('now'), "
            "is_resolved = 0 "
            "WHERE cast_id = ?",
            (args.certainty, args.date, args.reason, cast["id"]),
        )
        action = "更新"
    conn.commit()
    print(f"退店リスクを{action}: {cast['name']} certainty={args.certainty} "
          f"date={args.date or '(未定)'} reason={args.reason or ''}")


def cmd_update_quit_risk(args):
    conn = connect()
    cast = find_cast(conn, args.cast)
    existing = conn.execute("SELECT * FROM quit_risks WHERE cast_id = ?", (cast["id"],)).fetchone()
    if existing is None:
        die(f"{cast['name']} の退店リスクレコードが存在しません。先に add-quit-risk で登録してください。")
    fields = []
    params = []
    if args.certainty:
        if args.certainty not in ("confirmed", "likely"):
            die(f"certainty の値が不正: {args.certainty}")
        fields.append("certainty = ?")
        params.append(args.certainty)
    if args.date is not None:
        fields.append("expected_quit_date = ?")
        params.append(args.date)
    if args.reason is not None:
        fields.append("reason = ?")
        params.append(args.reason)
    if args.resolve:
        fields.append("is_resolved = 1")
    if not fields:
        die("更新する項目を指定してください(--certainty / --date / --reason / --resolve のいずれか)。")
    fields.append("updated_at = datetime('now')")
    params.append(cast["id"])
    conn.execute(f"UPDATE quit_risks SET {', '.join(fields)} WHERE cast_id = ?", params)
    conn.commit()
    print(f"{cast['name']} の退店リスクを更新しました。")


# ---------------------------------------------------------------------------
# マスタメンテ系
# ---------------------------------------------------------------------------

def cmd_cast_add(args):
    conn = connect()
    if args.shift not in ("night", "day"):
        die(f"shift の値が不正: {args.shift}")
    kuro = find_kurofuku(conn, args.by)
    existing = conn.execute("SELECT id FROM casts WHERE name = ?", (args.name,)).fetchone()
    if existing:
        die(f"キャスト名 {args.name} は既に存在します(id={existing['id']})。")
    conn.execute(
        "INSERT INTO casts(name, kurofuku_id, shift) VALUES (?, ?, ?)",
        (args.name, kuro["id"], args.shift),
    )
    conn.commit()
    print(f"キャストを追加: {args.name} (担当={kuro['name']}, shift={args.shift})")


def cmd_cast_rename(args):
    conn = connect()
    cast = find_cast(conn, getattr(args, "from"))
    if conn.execute("SELECT 1 FROM casts WHERE name = ?", (args.to,)).fetchone():
        die(f"変更先 {args.to} は既に使われています。")
    conn.execute("UPDATE casts SET name = ? WHERE id = ?", (args.to, cast["id"]))
    conn.commit()
    print(f"キャスト名を変更: {cast['name']} → {args.to}")


def cmd_cast_reassign(args):
    conn = connect()
    cast = find_cast(conn, args.name)
    kuro = find_kurofuku(conn, args.to)
    conn.execute("UPDATE casts SET kurofuku_id = ? WHERE id = ?", (kuro["id"], cast["id"]))
    conn.commit()
    print(f"{cast['name']} の担当黒服を変更: → {kuro['name']}")


def cmd_cast_quit(args):
    conn = connect()
    cast = find_cast(conn, args.name)
    if cast["status"] == "quit":
        die(f"{cast['name']} は既に退店扱いです(quit_date={cast['quit_date']})。")
    conn.execute(
        "UPDATE casts SET status = 'quit', quit_date = ? WHERE id = ?",
        (args.date, cast["id"]),
    )
    # quit_risks があれば自動で解消扱いにする
    conn.execute(
        "UPDATE quit_risks SET is_resolved = 1, updated_at = datetime('now') WHERE cast_id = ?",
        (cast["id"],),
    )
    conn.commit()
    print(f"{cast['name']} を退店扱いに({args.date})。quit_risks があれば解消済みに更新。")


def cmd_kurofuku_rename(args):
    conn = connect()
    kuro = find_kurofuku(conn, getattr(args, "from"))
    if conn.execute("SELECT 1 FROM kurofukus WHERE name = ?", (args.to,)).fetchone():
        die(f"変更先 {args.to} は既に使われています。")
    conn.execute("UPDATE kurofukus SET name = ? WHERE id = ?", (args.to, kuro["id"]))
    conn.commit()
    print(f"黒服名を変更: {kuro['name']} → {args.to}")


def cmd_initiative_add(args):
    conn = connect()
    if args.category not in ("cast", "quit_risk", "standalone"):
        die(f"category の値が不正: {args.category}")
    if conn.execute("SELECT 1 FROM initiatives WHERE name = ?", (args.name,)).fetchone():
        die(f"施策名 {args.name} は既に存在します。")
    conn.execute(
        "INSERT INTO initiatives(name, category, description) VALUES (?, ?, ?)",
        (args.name, args.category, args.description),
    )
    conn.commit()
    print(f"施策を追加: {args.name} (category={args.category})")


# ---------------------------------------------------------------------------
# argparse 構築
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ingest.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = p.add_subparsers(dest="cmd", required=True)

    # 参照系
    subs.add_parser("list-kurofukus")

    p_lc = subs.add_parser("list-casts")
    p_lc.add_argument("--shift", choices=["night", "day"])
    p_lc.add_argument("--by", help="担当黒服名で絞る")
    p_lc.add_argument("--status", choices=["active", "quit", "all"], default="active")

    subs.add_parser("list-initiatives")

    p_lr = subs.add_parser("list-recent-reports")
    p_lr.add_argument("--cast")
    p_lr.add_argument("--initiative", help="id または name")
    p_lr.add_argument("--days", type=int)
    p_lr.add_argument("--limit", type=int, default=20)

    p_sc = subs.add_parser("show-cast")
    p_sc.add_argument("--name", required=True)

    p_ss = subs.add_parser("show-status")
    p_ss.add_argument("--cast", required=True)
    p_ss.add_argument("--initiative", required=True)

    # 報告投入系
    p_ar = subs.add_parser("add-report")
    p_ar.add_argument("--date", required=True, help="YYYY-MM-DD")
    p_ar.add_argument("--cast", required=True)
    p_ar.add_argument("--initiative", required=True, help="id または name")
    p_ar.add_argument("--by", required=True, help="黒服名")
    p_ar.add_argument("--content", required=True)
    p_ar.add_argument("--reaction", choices=["positive", "neutral", "negative"])
    p_ar.add_argument("--status", choices=list(VALID_STATUSES),
                      help="指定すると cast_initiative_status を upsert する")
    p_ar.add_argument("--comment", help="status と一緒に保存する一言コメント")
    p_ar.add_argument("--raw", help="元の報告テキスト全体")
    p_ar.add_argument("--force", action="store_true",
                      help="重複報告 / done を巻き戻すケースで必要")

    p_set = subs.add_parser("set-status")
    p_set.add_argument("--cast", required=True)
    p_set.add_argument("--initiative", required=True)
    p_set.add_argument("--status", required=True, choices=list(VALID_STATUSES))
    p_set.add_argument("--comment")
    p_set.add_argument("--force", action="store_true")

    p_aq = subs.add_parser("add-quit-risk")
    p_aq.add_argument("--cast", required=True)
    p_aq.add_argument("--certainty", required=True, choices=["confirmed", "likely"])
    p_aq.add_argument("--date", help="退店予定日 YYYY-MM-DD(未確定なら省略)")
    p_aq.add_argument("--reason")
    p_aq.add_argument("--by", required=True, help="黒服名")
    p_aq.add_argument("--report-date", dest="report_date", help="報告ログ用の日付 YYYY-MM-DD")
    p_aq.add_argument("--content", help="報告ログに残す本文(省略時は report を作らない)")
    p_aq.add_argument("--raw")

    p_uq = subs.add_parser("update-quit-risk")
    p_uq.add_argument("--cast", required=True)
    p_uq.add_argument("--certainty", choices=["confirmed", "likely"])
    p_uq.add_argument("--date", help="退店予定日(空文字を渡すと未定にしたい場合は別途要対応)")
    p_uq.add_argument("--reason")
    p_uq.add_argument("--resolve", action="store_true", help="is_resolved=1 にする")

    # cast subcommands
    p_cast = subs.add_parser("cast", help="キャストマスタ操作")
    cs = p_cast.add_subparsers(dest="cast_cmd", required=True)
    p_cast_add = cs.add_parser("add")
    p_cast_add.add_argument("--name", required=True)
    p_cast_add.add_argument("--by", required=True, help="担当黒服名")
    p_cast_add.add_argument("--shift", required=True, choices=["night", "day"])

    p_cast_rn = cs.add_parser("rename")
    p_cast_rn.add_argument("--from", dest="from", required=True)
    p_cast_rn.add_argument("--to", required=True)

    p_cast_re = cs.add_parser("reassign")
    p_cast_re.add_argument("--name", required=True)
    p_cast_re.add_argument("--to", required=True, help="新しい担当黒服名")

    p_cast_q = cs.add_parser("quit")
    p_cast_q.add_argument("--name", required=True)
    p_cast_q.add_argument("--date", required=True)

    # kurofuku subcommands
    p_k = subs.add_parser("kurofuku")
    ks = p_k.add_subparsers(dest="kurofuku_cmd", required=True)
    p_kr = ks.add_parser("rename")
    p_kr.add_argument("--from", dest="from", required=True)
    p_kr.add_argument("--to", required=True)

    # initiative subcommands
    p_i = subs.add_parser("initiative")
    is_ = p_i.add_subparsers(dest="initiative_cmd", required=True)
    p_ia = is_.add_parser("add")
    p_ia.add_argument("--name", required=True)
    p_ia.add_argument("--category", required=True, choices=["cast", "quit_risk", "standalone"])
    p_ia.add_argument("--description")

    return p


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

DISPATCH = {
    "list-kurofukus": cmd_list_kurofukus,
    "list-casts": cmd_list_casts,
    "list-initiatives": cmd_list_initiatives,
    "list-recent-reports": cmd_list_recent_reports,
    "show-cast": cmd_show_cast,
    "show-status": cmd_show_status,
    "add-report": cmd_add_report,
    "set-status": cmd_set_status,
    "add-quit-risk": cmd_add_quit_risk,
    "update-quit-risk": cmd_update_quit_risk,
}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.cmd in DISPATCH:
        DISPATCH[args.cmd](args)
        return 0
    if args.cmd == "cast":
        {"add": cmd_cast_add, "rename": cmd_cast_rename,
         "reassign": cmd_cast_reassign, "quit": cmd_cast_quit}[args.cast_cmd](args)
        return 0
    if args.cmd == "kurofuku":
        {"rename": cmd_kurofuku_rename}[args.kurofuku_cmd](args)
        return 0
    if args.cmd == "initiative":
        {"add": cmd_initiative_add}[args.initiative_cmd](args)
        return 0
    parser.error(f"unknown cmd: {args.cmd}")


if __name__ == "__main__":
    sys.exit(main())
