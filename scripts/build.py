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
    initiatives = [dict(r) for r in conn.execute(
        "SELECT id, name, category, description FROM initiatives ORDER BY id"
    )]
    casts = [dict(r) for r in conn.execute(
        "SELECT c.id, c.name, c.kurofuku_id, k.name AS kurofuku, c.shift, c.status, c.quit_date "
        "FROM casts c JOIN kurofukus k ON c.kurofuku_id = k.id ORDER BY c.id"
    )]
    statuses = [dict(r) for r in conn.execute(
        "SELECT cast_id, initiative_id, status, comment, updated_at "
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
    }


def context_casts(data):
    active_casts = [c for c in data["casts"] if c["status"] == "active"]

    groups_map = {}
    for c in active_casts:
        groups_map.setdefault((c["kurofuku"], c["shift"]), []).append(c)

    groups = []
    for (kuro, shift), casts in sorted(groups_map.items(), key=lambda x: (0 if x[0][1] == "night" else 1, x[0][0])):
        casts_with_qr = []
        for c in sorted(casts, key=lambda x: x["name"]):
            qr = quit_risk_for(data["quit_risks"], c["id"])
            casts_with_qr.append({**c, "quit_risk": qr["certainty"] if qr else None})
        groups.append({
            "kurofuku": kuro,
            "shift_label": "夜" if shift == "night" else "昼",
            "casts": casts_with_qr,
        })

    quit_casts = sorted(
        (c for c in data["casts"] if c["status"] == "quit"),
        key=lambda x: x["quit_date"] or "",
        reverse=True,
    )

    return {
        "title": "キャスト一覧",
        "groups": groups,
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
            d = datetime.strptime(q["expected_quit_date"], "%Y-%m-%d").date()
            days = (d - today).days
            dated.append({**q, "days_until": days})
        else:
            undated.append(q)

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
        "dated": dated,
        "undated": undated,
        "calendar_data": calendar_data,
    }


def context_kurofukus(data, today):
    month_start, month_end = month_bounds(today)
    # 「今月」= 暦月。 month_start <= report_date < month_end
    active_casts = [c for c in data["casts"] if c["status"] == "active"]
    # A群施策(category='cast')の数。退店リスクは含まない(報告が必ず発生する性質ではないため)
    cast_initiative_count = len([i for i in data["initiatives"] if i["category"] == "cast"])

    views = []
    for k in data["kurofukus"]:
        # 担当アクティブキャスト数
        assigned = [c for c in active_casts if c["kurofuku"] == k["name"]]
        assigned_count = len(assigned)
        # 今月の reports(その黒服 by)
        month_reports = [
            r for r in data["reports"]
            if r["kurofuku_id"] == k["id"]
            and month_start.isoformat() <= r["report_date"] < month_end.isoformat()
        ]
        report_count = len(month_reports)
        casts_touched = len({r["cast_id"] for r in month_reports})
        initiatives_touched = len({r["initiative_id"] for r in month_reports})
        # 接触率: 今月の報告件数 / (担当人数 × A群施策数)
        # ※ 退店リスクは「必ず発生するわけではない」性質の施策なので分母から除外
        denom = assigned_count * cast_initiative_count
        rate = report_count / denom if denom > 0 else 0.0
        if rate >= 0.7:
            color = "green"
        elif rate >= 0.3:
            color = "yellow"
        else:
            color = "red"
        views.append({
            "id": k["id"],
            "name": k["name"],
            "assigned_count": assigned_count,
            "report_count": report_count,
            "casts_touched": casts_touched,
            "initiatives_touched": initiatives_touched,
            "contact_rate_pct": round(rate * 100),
            "color": color,
        })

    return {
        "title": "黒服別",
        "kurofukus": views,
        "month_label": f"{month_start.year}年 {month_start.month}月",
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
