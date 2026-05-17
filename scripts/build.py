"""
SQLite を読んで dist/ に静的 HTML を書き出す。

環境変数:
  SITE_PASSWORD  必須。閲覧サイトの JS 簡易ロックに使うパスワード。
                 .env (SITE_PASSWORD=...) からも読む(env var が優先)。
                 'changeme' は本番事故防止のため拒否する。

使い方:
  SITE_PASSWORD=secret python scripts/build.py
"""

import hashlib
import json
import os
import shutil
import sqlite3
import struct
import sys
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "reports.db"
TEMPLATES_DIR = ROOT / "templates"
DIST_DIR = ROOT / "dist"

# 黒服ごとのカラーパレット。落ち着いたトーンで、ナイトワーク色(赤・ピンク)は避ける。
# テンプレでタグ・ヘッダー色などに使う。Tailwind CDN は HTML 内クラス文字列を
# スキャンするので、ここで使う class 名はテンプレに必ず文字列として出てくる必要がある。
KUROFUKU_COLORS = {
    "五上":   {"bg": "bg-sky-50",     "text": "text-sky-700",     "border": "border-sky-200",     "dot": "bg-sky-500",     "border_l": "border-l-sky-500"},
    "ひろし": {"bg": "bg-violet-50",  "text": "text-violet-700",  "border": "border-violet-200",  "dot": "bg-violet-500",  "border_l": "border-l-violet-500"},
    "川田":   {"bg": "bg-teal-50",    "text": "text-teal-700",    "border": "border-teal-200",    "dot": "bg-teal-500",    "border_l": "border-l-teal-500"},
    "鴇田":   {"bg": "bg-orange-50",  "text": "text-orange-700",  "border": "border-orange-200",  "dot": "bg-orange-500",  "border_l": "border-l-orange-500"},
    "向原":   {"bg": "bg-lime-50",    "text": "text-lime-700",    "border": "border-lime-200",    "dot": "bg-lime-500",    "border_l": "border-l-lime-500"},
}
DEFAULT_KUROFUKU_COLOR = {
    "bg": "bg-slate-100", "text": "text-slate-700", "border": "border-slate-200", "dot": "bg-slate-400", "border_l": "border-l-slate-400",
}


def kurofuku_color(name: str) -> dict:
    return KUROFUKU_COLORS.get(name, DEFAULT_KUROFUKU_COLOR)


# ---------------------------------------------------------------------------
# 設定読み込み
# ---------------------------------------------------------------------------

def load_password() -> str:
    pw = os.environ.get("SITE_PASSWORD")
    if not pw:
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                if key.strip() == "SITE_PASSWORD":
                    pw = val.strip().strip('"').strip("'")
                    break
    if not pw:
        sys.exit("ERROR: SITE_PASSWORD が未設定。env var もしくは .env で指定してください。")
    if pw == "changeme":
        sys.exit("ERROR: SITE_PASSWORD='changeme' は本番事故防止のため拒否されます。")
    return pw


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------

def fetch_all(conn: sqlite3.Connection):
    """このビルドで使う一通りのデータをまとめて取り出す。"""
    kurofukus = [dict(r) for r in conn.execute(
        "SELECT id, name FROM kurofukus ORDER BY id"
    )]
    # 黒服ごとに color パレットを attach
    for k in kurofukus:
        k["color"] = kurofuku_color(k["name"])
    initiatives = [dict(r) for r in conn.execute(
        "SELECT id, name, category, description FROM initiatives ORDER BY id"
    )]
    casts = [dict(r) for r in conn.execute(
        "SELECT c.id, c.name, c.kurofuku_id, k.name AS kurofuku, c.shift, c.status, c.quit_date "
        "FROM casts c JOIN kurofukus k ON c.kurofuku_id = k.id ORDER BY c.id"
    )]
    # 各キャストにも担当黒服の color を持たせる(テンプレで使いやすく)
    for c in casts:
        c["kurofuku_color"] = kurofuku_color(c["kurofuku"])
    statuses = [dict(r) for r in conn.execute(
        "SELECT cast_id, initiative_id, status, comment, updated_at, event_date "
        "FROM cast_initiative_status"
    )]
    reports = [dict(r) for r in conn.execute(
        "SELECT r.id, r.report_date, r.cast_id, r.kurofuku_id, r.initiative_id, "
        "r.content, r.reaction, "
        "c.name AS cast, k.name AS kurofuku, i.name AS initiative "
        "FROM reports r "
        "JOIN casts c ON r.cast_id = c.id "
        "JOIN kurofukus k ON r.kurofuku_id = k.id "
        "JOIN initiatives i ON r.initiative_id = i.id "
        "ORDER BY r.report_date DESC, r.id DESC"
    )]
    quit_risks = [dict(r) for r in conn.execute(
        "SELECT q.cast_id, q.certainty, q.expected_quit_date, q.reason, "
        "q.updated_at, q.is_resolved, c.name AS cast, k.name AS kurofuku "
        "FROM quit_risks q "
        "JOIN casts c ON q.cast_id = c.id "
        "JOIN kurofukus k ON c.kurofuku_id = k.id "
        "WHERE q.is_resolved = 0"
    )]
    for q in quit_risks:
        q["kurofuku_color"] = kurofuku_color(q["kurofuku"])
    return {
        "kurofukus": kurofukus,
        "initiatives": initiatives,
        "casts": casts,
        "statuses": statuses,
        "reports": reports,
        "quit_risks": quit_risks,
    }


# ---------------------------------------------------------------------------
# 集計ヘルパ
# ---------------------------------------------------------------------------

def short_date(s: str) -> str:
    """'2026-05-01' → '5/1'"""
    if not s:
        return ""
    try:
        d = datetime.strptime(s[:10], "%Y-%m-%d")
        return f"{d.month}/{d.day}"
    except Exception:
        return s


def short_datetime(s: str) -> str:
    """'2026-05-06 11:18:37' → '5/6'"""
    return short_date(s)


def status_for(statuses, cast_id, initiative_id):
    for s in statuses:
        if s["cast_id"] == cast_id and s["initiative_id"] == initiative_id:
            return s
    return None


def quit_risk_for(quit_risks, cast_id):
    for q in quit_risks:
        if q["cast_id"] == cast_id:
            return q
    return None


def month_bounds(today: date):
    start = today.replace(day=1)
    if start.month == 12:
        next_month = date(start.year + 1, 1, 1)
    else:
        next_month = date(start.year, start.month + 1, 1)
    return start, next_month  # [start, next_month)


# ---------------------------------------------------------------------------
# 各ページ用の context 生成
# ---------------------------------------------------------------------------

def context_index(data, today):
    cast_initiatives = [i for i in data["initiatives"] if i["category"] == "cast"]
    active_casts = [c for c in data["casts"] if c["status"] == "active"]
    active_ids = {c["id"] for c in active_casts}

    # 施策カードのデータ
    ini_views = []
    for ini in cast_initiatives:
        # アクティブキャスト分母で集計(declined も含めた4状態を数える)
        rows = [s for s in data["statuses"] if s["initiative_id"] == ini["id"] and s["cast_id"] in active_ids]
        in_progress = sum(1 for s in rows if s["status"] == "in_progress")
        done = sum(1 for s in rows if s["status"] == "done")
        declined = sum(1 for s in rows if s["status"] == "declined")
        # 未着手 = レコードなし or status='not_started'
        not_started = len(active_casts) - in_progress - done - declined
        recent = []
        for r in data["reports"]:
            if r["initiative_id"] == ini["id"] and r["cast_id"] in active_ids:
                recent.append({
                    "date_short": short_date(r["report_date"]),
                    "cast": r["cast"],
                    "reaction": r["reaction"],
                    "content": r["content"],
                })
                if len(recent) == 3:
                    break
        ini_views.append({
            "id": ini["id"], "name": ini["name"],
            "in_progress": in_progress, "done": done,
            "declined": declined, "not_started": not_started,
            "recent": recent,
        })

    # 退店リスク サマリ
    confirmed = sum(1 for q in data["quit_risks"] if q["certainty"] == "confirmed")
    likely = sum(1 for q in data["quit_risks"] if q["certainty"] == "likely")
    cutoff = today + timedelta(days=90)
    within_3months = sum(
        1 for q in data["quit_risks"]
        if q["expected_quit_date"]
        and today <= datetime.strptime(q["expected_quit_date"], "%Y-%m-%d").date() <= cutoff
    )

    return {
        "title": "ダッシュボード",
        "initiatives": ini_views,
        "quit_summary": {
            "confirmed": confirmed,
            "likely": likely,
            "within_3months": within_3months,
        },
    }


def context_initiative(data, ini, today):
    active_casts = [c for c in data["casts"] if c["status"] == "active"]
    active_ids = {c["id"] for c in active_casts}

    # 黒服 × shift でグルーピング
    groups_map = {}  # (kurofuku, shift) -> list
    for c in active_casts:
        key = (c["kurofuku"], c["shift"])
        groups_map.setdefault(key, []).append(c)

    groups = []
    for (kuro, shift), casts in sorted(groups_map.items(), key=lambda x: (0 if x[0][1] == "night" else 1, x[0][0])):
        rows = []
        for c in casts:
            s = status_for(data["statuses"], c["id"], ini["id"])
            qr = quit_risk_for(data["quit_risks"], c["id"])
            rows.append({
                "cast_id": c["id"],
                "cast": c["name"],
                "status": s["status"] if s else "not_started",
                "comment": s["comment"] if s else "",
                "updated_short": short_datetime(s["updated_at"]) if s else "",
                "quit_risk": qr["certainty"] if qr else None,
            })
        # 進行中→完了→無理→未着手 の順で並べると、アクション必要なものから見える
        # (declined は完了の駄目パターン、両方ともこれ以上アクション不要)
        order = {"in_progress": 0, "done": 1, "declined": 2, "not_started": 3}
        rows.sort(key=lambda r: (order.get(r["status"], 4), r["cast"]))
        groups.append({
            "kurofuku": kuro,
            "kurofuku_color": kurofuku_color(kuro),
            "shift_label": "夜" if shift == "night" else "昼",
            "rows": rows,
            "in_progress": sum(1 for r in rows if r["status"] == "in_progress"),
            "done": sum(1 for r in rows if r["status"] == "done"),
            "declined": sum(1 for r in rows if r["status"] == "declined"),
            "not_started": sum(1 for r in rows if r["status"] == "not_started"),
        })

    summary_in_progress = sum(g["in_progress"] for g in groups)
    summary_done = sum(g["done"] for g in groups)
    summary_declined = sum(g["declined"] for g in groups)
    summary_not_started = sum(g["not_started"] for g in groups)

    reports = [r for r in data["reports"] if r["initiative_id"] == ini["id"]]

    # バースデー(施策3)のみ: 退店リスクページと同じ「進捗/リスト/カレンダー」タブ用の
    # データ。リストは日付あり(確定+未確定をまとめ月で区切る)+ 日付未定。
    is_birthday = ini["name"] == "バースデーイベント開催のお願い"
    bday_dated, bday_undated, bday_calendar = [], [], []
    if is_birthday:
        for c in active_casts:
            s = status_for(data["statuses"], c["id"], ini["id"])
            if not s or s["status"] not in ("in_progress", "done"):
                continue
            ev = s.get("event_date")
            confirmed = s["status"] == "done"
            base = {
                "cast": c["name"], "cast_id": c["id"],
                "kurofuku": c["kurofuku"], "kurofuku_color": c["kurofuku_color"],
                "comment": s["comment"] or "", "confirmed": confirmed,
            }
            if ev:
                du = _days_until(ev, today)
                bday_calendar.append({
                    "date": ev, "cast": c["name"], "cast_id": c["id"],
                    "kind": "birthday", "confirmed": confirmed,
                })
                if du >= 0:  # 過ぎた予定日はリストから除外(カレンダーには残す)
                    bday_dated.append({**base, "date": ev, "days_until": du})
            else:
                bday_undated.append(base)
        bday_dated.sort(key=lambda r: r["date"])

    return {
        "title": ini["name"],
        "initiative": ini,
        "groups": groups,
        "summary": {
            "in_progress": summary_in_progress,
            "done": summary_done,
            "declined": summary_declined,
            "not_started": summary_not_started,
        },
        "reports": reports,
        "is_birthday": is_birthday,
        "bday_months": _group_by_month(bday_dated),
        "bday_undated": bday_undated,
        "bday_calendar": bday_calendar,
    }


def context_casts(data):
    active_casts = [c for c in data["casts"] if c["status"] == "active"]

    rows = []
    for c in active_casts:
        cast_reports = [r for r in data["reports"] if r["cast_id"] == c["id"]]
        last_date = max((r["report_date"] for r in cast_reports), default=None)
        qr = quit_risk_for(data["quit_risks"], c["id"])
        rows.append({
            **c,
            "last_date": last_date,
            "last_short": short_date(last_date) if last_date else "",
            "quit_risk": qr["certainty"] if qr else None,
        })
    # 最終アクションが新しい順、未接触(last_date=None)は最下位
    rows.sort(key=lambda r: r["last_date"] or "0000-00-00", reverse=True)

    quit_casts = sorted(
        (c for c in data["casts"] if c["status"] == "quit"),
        key=lambda x: x["quit_date"] or "",
        reverse=True,
    )

    return {
        "title": "キャスト一覧",
        "casts": rows,
        "quit_casts": quit_casts,
    }


def context_cast(data, cast):
    cast_initiatives = [i for i in data["initiatives"] if i["category"] == "cast"]
    statuses = []
    for ini in cast_initiatives:
        s = status_for(data["statuses"], cast["id"], ini["id"])
        statuses.append({
            "initiative_id": ini["id"],
            "initiative_name": ini["name"],
            "status": s["status"] if s else "not_started",
            "comment": s["comment"] if s else "",
            "updated_short": short_datetime(s["updated_at"]) if s else "",
        })
    qr = quit_risk_for(data["quit_risks"], cast["id"])
    reports = [r for r in data["reports"] if r["cast_id"] == cast["id"]]
    return {
        "title": cast["name"],
        "cast": cast,
        "statuses": statuses,
        "quit_risk": qr,
        "reports": reports,
    }


def context_quit_risks(data, today):
    # 確度→日付近い順 でソート
    def sort_key(q):
        # confirmed first, then likely
        cert_order = 0 if q["certainty"] == "confirmed" else 1
        return (cert_order, q["expected_quit_date"] or "9999-99-99")

    qr_sorted = sorted(data["quit_risks"], key=sort_key)
    dated = []
    undated = []
    for q in qr_sorted:
        if q["expected_quit_date"]:
            days = _days_until(q["expected_quit_date"], today)
            if days < 0:
                continue  # 過ぎた予定日はリストから除外(/予定/ と整合)
            dated.append({**q, "days_until": days, "date": q["expected_quit_date"]})
        else:
            undated.append(q)
    # 日付近い順 → 月区切り
    dated.sort(key=lambda r: r["date"])
    dated_months = _group_by_month(dated)

    calendar_data = [
        {
            "date": q["expected_quit_date"],
            "cast": q["cast"],
            "cast_id": q["cast_id"],
            "certainty": q["certainty"],
        }
        for q in data["quit_risks"]
        if q["expected_quit_date"]
    ]

    return {
        "title": "退店リスク",
        "dated_months": dated_months,
        "undated": undated,
        "calendar_data": calendar_data,
    }


def _days_until(date_str, today):
    """'YYYY-MM-DD' と today(date)から残り日数(整数)。過去は負。"""
    d = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    return (d - today).days


def _group_by_month(rows):
    """date(YYYY-MM-DD)を持つ行リスト(日付昇順前提)を月ごとに区切る。
    戻り値: [{"label": "YYYY年M月", "rows": [...]}, ...] を時系列順で。"""
    out = []
    for r in rows:
        ym = r["date"][:7]
        if not out or out[-1]["ym"] != ym:
            y, m = ym.split("-")
            out.append({"ym": ym, "label": f"{y}年{int(m)}月", "rows": []})
        out[-1]["rows"].append(r)
    return out


def _birthday_initiative_id(data):
    ini = next(
        (i for i in data["initiatives"] if i["name"] == "バースデーイベント開催のお願い"),
        None,
    )
    return ini["id"] if ini else None


def context_calendar(data, today):
    """/予定/ 用。退店(確定日)とバースデー(開催予定日)を、退店リスクページと同じ
    「リスト / カレンダー」2タブで見せる。カレンダー JS 用 calendar_events と、
    リスト用 list_dated(日付順)/ list_undated(日付未定)を渡す。
    退店リスク機能側(context_quit_risks)はそのまま、ここは独立に集約するだけ。"""
    active_casts = {
        c["id"]: c for c in data["casts"] if c["status"] == "active"
    }
    birthday_id = _birthday_initiative_id(data)

    events = []        # カレンダー JS 用
    list_dated = []    # リストタブ: 日付あり(日付順)
    list_undated = []  # リストタブ: 日付未定

    # 退店
    for q in data["quit_risks"]:
        if q["expected_quit_date"]:
            du = _days_until(q["expected_quit_date"], today)
            events.append({
                "date": q["expected_quit_date"], "cast": q["cast"],
                "cast_id": q["cast_id"], "kind": "quit", "certainty": q["certainty"],
            })
            # 過ぎた予定日はリストから除外(カレンダーには残す)
            if du >= 0:
                list_dated.append({
                    "date": q["expected_quit_date"], "days_until": du,
                    "cast": q["cast"], "cast_id": q["cast_id"],
                    "kurofuku": q["kurofuku"], "kurofuku_color": q["kurofuku_color"],
                    "kind": "quit", "certainty": q["certainty"], "detail": q["reason"] or "",
                })
        else:
            list_undated.append({
                "cast": q["cast"], "cast_id": q["cast_id"],
                "kurofuku": q["kurofuku"], "kurofuku_color": q["kurofuku_color"],
                "kind": "quit", "certainty": q["certainty"], "detail": q["reason"] or "",
            })

    # バースデー(アクティブキャストのみ)
    if birthday_id is not None:
        for s in data["statuses"]:
            if s["initiative_id"] != birthday_id or s["cast_id"] not in active_casts:
                continue
            if s["status"] not in ("in_progress", "done"):
                continue
            c = active_casts[s["cast_id"]]
            confirmed = s["status"] == "done"
            if s.get("event_date"):
                du = _days_until(s["event_date"], today)
                events.append({
                    "date": s["event_date"], "cast": c["name"],
                    "cast_id": c["id"], "kind": "birthday", "confirmed": confirmed,
                })
                if du >= 0:
                    list_dated.append({
                        "date": s["event_date"], "days_until": du,
                        "cast": c["name"], "cast_id": c["id"],
                        "kurofuku": c["kurofuku"], "kurofuku_color": c["kurofuku_color"],
                        "kind": "birthday", "confirmed": confirmed,
                        "detail": s["comment"] or "",
                    })
            else:
                list_undated.append({
                    "cast": c["name"], "cast_id": c["id"],
                    "kurofuku": c["kurofuku"], "kurofuku_color": c["kurofuku_color"],
                    "kind": "birthday", "confirmed": confirmed,
                    "detail": s["comment"] or "",
                })

    list_dated.sort(key=lambda r: r["date"])

    return {
        "title": "予定",
        "calendar_events": events,
        "list_months": _group_by_month(list_dated),
        "list_undated": list_undated,
    }


def context_kurofukus(data, today):
    active_casts = [c for c in data["casts"] if c["status"] == "active"]
    # A群施策(category='cast')。退店リスクは「必ず発生する性質ではない」ため分母から除外。
    cast_initiative_ids = {i["id"] for i in data["initiatives"] if i["category"] == "cast"}
    cast_initiative_count = len(cast_initiative_ids)

    views = []
    for k in data["kurofukus"]:
        assigned = [c for c in active_casts if c["kurofuku"] == k["name"]]
        assigned_ids = {c["id"] for c in assigned}
        assigned_count = len(assigned)

        # 全期間カウンタ(その黒服 by)
        my_reports = [r for r in data["reports"] if r["kurofuku_id"] == k["id"]]
        report_count = len(my_reports)
        casts_touched = len({r["cast_id"] for r in my_reports})
        initiatives_touched = len({r["initiative_id"] for r in my_reports})

        # アクション終了率: 担当キャスト × A群施策 のうち、これ以上できることがない
        # 状態(done / declined)に至ったペアの数 / (担当人数 × A群施策数)。
        # 「進行中」「未着手」は終了とみなさない。
        closed_pairs = {
            (s["cast_id"], s["initiative_id"])
            for s in data["statuses"]
            if s["cast_id"] in assigned_ids
            and s["initiative_id"] in cast_initiative_ids
            and s["status"] in ("done", "declined")
        }
        denom = assigned_count * cast_initiative_count
        rate = len(closed_pairs) / denom if denom > 0 else 0.0
        # 100% コンプリート(全ペア done/declined)のみ緑。それ以外は同列扱いで黒。
        color = "green" if rate >= 1.0 else "black"

        # 「未接触」「完了」「進行中」を**キャスト単位**で算出し、合計が担当数になるよう揃える。
        # ・未接触 = A群すべて not_started(動かしていない)
        # ・完了   = 追える施策(バースデー除く)が全部 done/declined。バースデーは年1回しか
        #            動かないため判定から除外(2026-05-11 ユーザー合意)
        # ・進行中 = それ以外(= 担当 - 未接触 - 完了)
        non_birthday_ini_ids = {
            i["id"] for i in data["initiatives"]
            if i["category"] == "cast" and i["name"] != "バースデーイベント開催のお願い"
        }

        def _status_of(cast_id, ini_id):
            s = next(
                (x for x in data["statuses"]
                 if x["cast_id"] == cast_id and x["initiative_id"] == ini_id),
                None,
            )
            return s["status"] if s else "not_started"

        def _is_a_untouched(cast_id):
            return all(_status_of(cast_id, ini_id) == "not_started"
                       for ini_id in cast_initiative_ids)

        def _is_completed(cast_id):
            return all(_status_of(cast_id, ini_id) in ("done", "declined")
                       for ini_id in non_birthday_ini_ids)

        untouched_count = sum(1 for c in assigned if _is_a_untouched(c["id"]))
        completed_count = sum(1 for c in assigned if _is_completed(c["id"]))
        in_progress_count = assigned_count - untouched_count - completed_count

        views.append({
            "id": k["id"],
            "name": k["name"],
            "kurofuku_color": k["color"],
            "assigned_count": assigned_count,
            "report_count": report_count,
            "casts_touched": casts_touched,
            "initiatives_touched": initiatives_touched,
            "contact_rate_pct": round(rate * 100),
            "color": color,
            "in_progress_count": in_progress_count,
            "untouched_count": untouched_count,
            "completed_count": completed_count,
        })

    # 報告件数が多い順(動いている黒服を上に)
    views.sort(key=lambda v: v["report_count"], reverse=True)

    return {
        "title": "黒服別",
        "kurofukus": views,
        "cast_initiative_count": cast_initiative_count,
    }


def context_kurofuku(data, kurofuku, today):
    """単一黒服ページ: 担当キャスト一覧 + 各 A 群施策ステータス + 最終接触日順ソート。"""
    cast_initiatives = [i for i in data["initiatives"] if i["category"] == "cast"]
    assigned_active = [c for c in data["casts"]
                       if c["kurofuku"] == kurofuku["name"] and c["status"] == "active"]
    assigned_quit = sorted(
        (c for c in data["casts"]
         if c["kurofuku"] == kurofuku["name"] and c["status"] == "quit"),
        key=lambda x: x["quit_date"] or "", reverse=True,
    )

    rows = []
    for c in assigned_active:
        cast_reports = [r for r in data["reports"] if r["cast_id"] == c["id"]]
        last_date = max((r["report_date"] for r in cast_reports), default=None)
        statuses = []
        for ini in cast_initiatives:
            s = status_for(data["statuses"], c["id"], ini["id"])
            statuses.append({
                "initiative_id": ini["id"],
                "initiative_name": ini["name"],
                "status": s["status"] if s else "not_started",
            })
        qr = quit_risk_for(data["quit_risks"], c["id"])
        rows.append({
            "cast_id": c["id"],
            "cast": c["name"],
            "shift": c["shift"],
            "shift_label": "夜" if c["shift"] == "night" else "昼",
            "last_date": last_date,
            "last_short": short_date(last_date) if last_date else "",
            "statuses": statuses,
            "quit_risk": qr["certainty"] if qr else None,
        })

    # 最終接触が新しい順、未接触(last_date=None)は最下位
    rows.sort(key=lambda r: r["last_date"] or "0000-00-00", reverse=True)

    return {
        "title": f"{kurofuku['name']} 担当",
        "kurofuku_name": kurofuku["name"],
        "kurofuku_color": kurofuku["color"],
        "active_rows": rows,
        "quit_casts": assigned_quit,
    }


# ---------------------------------------------------------------------------
# PWA: アイコン PNG とマニフェスト生成
# ---------------------------------------------------------------------------
# 落ち着いたスレート背景に白い 3本横線(リスト/進捗をイメージ)。
# 依存を増やさないため stdlib (struct + zlib) だけで PNG を直接書き出す。

ICON_BG = (51, 65, 85)        # slate-700
ICON_FG = (255, 255, 255)     # white


def _png_chunk(typ: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))


def write_app_icon(path: Path, size: int):
    """slate-700 背景 + 白3本横線のシンプルなアプリアイコンを PNG で書き出す。"""
    bar_x0 = int(size * 0.25)
    bar_x1 = int(size * 0.75)
    bar_h = max(2, int(size * 0.07))
    bar_ys = [int(size * f) - bar_h // 2 for f in (0.32, 0.50, 0.68)]

    bg_row_pixels = bytes(ICON_BG) * size
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter: none
        in_bar = any(by <= y < by + bar_h for by in bar_ys)
        if not in_bar:
            raw += bg_row_pixels
            continue
        # 1行ぶんを構築(bar 範囲だけ白に塗る)
        row = bytearray()
        for x in range(size):
            if bar_x0 <= x < bar_x1:
                row += bytes(ICON_FG)
            else:
                row += bytes(ICON_BG)
        raw += row

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8bit RGB, no alpha
    idat = zlib.compress(bytes(raw), 9)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")
    )


def write_manifest(path: Path):
    manifest = {
        "name": "施策進捗",
        "short_name": "施策進捗",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#f8fafc",   # slate-50(起動画面)
        "theme_color": "#f8fafc",
        "icons": [
            {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 描画
# ---------------------------------------------------------------------------

def render(env, template_name: str, out_path: Path, ctx: dict, root_prefix: str,
           password_hash: str, build_time: str, current_page: str, hide_nav: bool = False):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmpl = env.get_template(template_name)
    html = tmpl.render(
        **ctx,
        root=root_prefix,
        password_hash=password_hash,
        build_time=build_time,
        current_page=current_page,
        hide_nav=hide_nav,
    )
    out_path.write_text(html, encoding="utf-8")


def main() -> int:
    if not DB_PATH.exists():
        sys.exit(f"ERROR: DBが見つかりません: {DB_PATH}")

    pw = load_password()
    pw_hash = sha256_hex(pw)

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        data = fetch_all(conn)
    finally:
        conn.close()

    today = date.today()
    build_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=False,
        lstrip_blocks=False,
    )

    # /
    render(env, "index.html", DIST_DIR / "index.html",
           context_index(data, today), "./", pw_hash, build_time, current_page="index")

    # /casts/
    render(env, "casts.html", DIST_DIR / "casts" / "index.html",
           context_casts(data), "../", pw_hash, build_time, current_page="casts")

    # /casts/{id}/
    for cast in data["casts"]:
        render(env, "cast.html",
               DIST_DIR / "casts" / str(cast["id"]) / "index.html",
               context_cast(data, cast),
               "../../", pw_hash, build_time, current_page="cast")

    # /initiatives/{id}/  (A群のみ)
    for ini in data["initiatives"]:
        if ini["category"] != "cast":
            continue
        render(env, "initiative.html",
               DIST_DIR / "initiatives" / str(ini["id"]) / "index.html",
               context_initiative(data, ini, today),
               "../../", pw_hash, build_time, current_page="initiative")

    # /quit-risks/
    render(env, "quit_risks.html", DIST_DIR / "quit-risks" / "index.html",
           context_quit_risks(data, today),
           "../", pw_hash, build_time, current_page="quit_risks")

    # /calendar/  (予定: 退店 + バースデーを1つのカレンダーに集約)
    render(env, "calendar.html", DIST_DIR / "calendar" / "index.html",
           context_calendar(data, today),
           "../", pw_hash, build_time, current_page="calendar")

    # /kurofukus/
    render(env, "kurofukus.html", DIST_DIR / "kurofukus" / "index.html",
           context_kurofukus(data, today),
           "../", pw_hash, build_time, current_page="kurofukus")

    # /kurofukus/{id}/
    for k in data["kurofukus"]:
        render(env, "kurofuku.html",
               DIST_DIR / "kurofukus" / str(k["id"]) / "index.html",
               context_kurofuku(data, k, today),
               "../../", pw_hash, build_time, current_page="kurofukus")

    # /restaurants/  (ナビには出さない、URL直打ち専用)
    render(env, "restaurants.html", DIST_DIR / "restaurants" / "index.html",
           {"title": "ひろしさん顔きき計画"},
           "../", pw_hash, build_time, current_page="restaurants", hide_nav=True)

    # 検索エンジン除けの 404 もついでに(任意)
    (DIST_DIR / "404.html").write_text(
        "<!DOCTYPE html><meta charset=\"UTF-8\"><meta name=\"robots\" content=\"noindex,nofollow\">"
        "<title>404</title><p>Not found.</p>",
        encoding="utf-8",
    )

    # PWA: manifest + アプリアイコン3種(180=apple-touch-icon, 192/512=manifest用)
    write_manifest(DIST_DIR / "manifest.webmanifest")
    write_app_icon(DIST_DIR / "icons" / "icon-180.png", 180)
    write_app_icon(DIST_DIR / "icons" / "icon-192.png", 192)
    write_app_icon(DIST_DIR / "icons" / "icon-512.png", 512)

    print(f"ビルド完了: {DIST_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
