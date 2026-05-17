# Claude Code 作業仕様書 (shisaku-tracker)

このファイルは Claude Code 用の単一情報源。新セッション開始時はまずこれを通読する。
人間向けの README は別途 `README.md` にある(現状は薄い、必要に応じて拡充)。

---

## 1. プロジェクトの本質

キャバクラのコンサル業務支援。店舗の黒服(4〜10人)から LINE 等で受ける施策進捗報告を、
ユーザー(コンサル)が Claude Code に貼り付け→**Claude が対話パース**→ `ingest.py` で
DB(SQLite)に書き込み→ `build.py` が静的 HTML 生成→ GitHub Pages で黒服チームに共有。

**ツールの設計思想(ブレない原則)**

- データ収集の精度より「**黒服が完遂できたか / できなくても一目で分かる可視化**」を優先
- `done` / `declined` を簡単に巻き戻さない
- 完了条件は施策ごとに明確に定義済み(本書の §6 参照)
- 動きの少ない黒服を炙り出す表示を優先(`/kurofukus` 接触率カラー)

**自然言語パースは Claude が行う。** `ingest.py` は薄い CLI で、参照・登録・更新コマンドを揃えるだけ。
正規表現や辞書で頑張らない。

---

## 2. 立ち位置と運用フロー

```
黒服 → LINE → コンサル(ユーザー) → 貼り付け → Claude(俺) → ingest.py → SQLite
                                                                          ↓
                                                                       build.py
                                                                          ↓
                                                                 GitHub Pages
                                                                          ↓
                                                              黒服がスマホで閲覧
```

- 黒服は閲覧のみ。入力はしない。
- 認証は JS パスワードゲート(SHA-256 ハッシュ埋め込み + localStorage)。
- 全員スマホ操作前提。**モバイル専用設計**(レスポンシブではない)。
- ホーム画面追加で PWA としても起動できる(standalone)。

---

## 3. ディレクトリ構成

```
shisaku-tracker/
├── CLAUDE.md                ← このファイル
├── README.md                ← 薄い人間向け案内
├── .env.example             ← Git管理、本物の .env は .gitignore
├── .gitignore
├── requirements.txt         ← jinja2 のみ
├── .venv/                   ← ローカル venv(gitignore)
├── data/
│   └── reports.db           ← SQLite。Git にバイナリ直 push する方針
├── scripts/
│   ├── init_db.py           ← スキーマ + シード投入。--force で再作成
│   ├── ingest.py            ← DB 操作 CLI(自然言語パースは入れない)
│   └── build.py             ← 静的 HTML 生成 + manifest/icon 生成
├── templates/               ← Jinja2
│   ├── base.html            ← layout, password gate, 下タブナビ, PWA メタ
│   ├── _macros.html         ← status/reaction/quit_risk バッジ
│   ├── index.html
│   ├── initiative.html
│   ├── casts.html           ← sticky 検索ボックス + 全1リスト(最終接触順) + 担当黒服タグ
│   ├── cast.html
│   ├── quit_risks.html      ← リスト/カレンダータブ切替(下タブからは外した。ホームの退店リスクカードから到達)
│   ├── calendar.html        ← /calendar/「予定」: 退店(確定日)+バースデー(event_date)統合カレンダー
│   ├── kurofukus.html       ← 黒服別カード(アクション終了率/進行中/未接触/完了)
│   ├── kurofuku.html        ← 黒服個別ページ /kurofukus/{id}/(担当キャスト一覧)
│   └── restaurants.html     ← ナビ非表示、URL 直打ちのみ
├── dist/                    ← ビルド成果物。gitignore
└── .github/workflows/
    └── deploy.yml           ← GitHub Actions → Pages 自動デプロイ(稼働中)
```

---

## 4. 起動・ビルド・確認コマンド

**前提**:
- 作業時は必ず `cd /Users/mk/dev/shisaku-tracker` してから。venv はプロジェクト内。
- **Python 3.12** を想定(現状 `python3` = 3.12.5)。GitHub Actions も 3.12 でセットアップ済み
- 外部依存は **jinja2 のみ**(`requirements.txt`)。残りは Python stdlib

```bash
# 初回 / 環境再構築
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# DB 初期化。⚠️ 本番運用中なので通常打たない(§15)。
#   init_db.py     → 既存があればエラーで止まる(安全)
#   init_db.py --force → 既存DBを全削除して再シード = 本番データ全消失。絶対打つな
python3 scripts/init_db.py            # ※ 既に reports.db があるのでこれ自体エラーになる
# python3 scripts/init_db.py --force  # ← 封印。本番データを消すので使用禁止

# CLI(参照・登録・更新)
python3 scripts/ingest.py <subcommand> [options]
python3 scripts/ingest.py --help     # サブコマンド一覧
python3 scripts/ingest.py list-casts --by 五上

# ビルド(SITE_PASSWORD 必須、'changeme' は拒否)
SITE_PASSWORD=test1234 .venv/bin/python scripts/build.py

# ローカル確認
.venv/bin/python -m http.server -d dist 8000
# → http://localhost:8000/ をブラウザで(モバイルサイズ推奨)
```

`.env` でも SITE_PASSWORD を渡せる(env var が優先)。

---

## 5. DB スキーマ(最終版)

`scripts/init_db.py` の `SCHEMA` がソース・オブ・トゥルース。要点:

| テーブル | 役割 | 注意 |
|---|---|---|
| `kurofukus` | 黒服マスタ | name UNIQUE |
| `casts` | キャストマスタ | name UNIQUE / `kurofuku_id` FK / `shift` 必須(`night`/`day`)/ `status`(`active`/`quit`)/ `quit_date` |
| `initiatives` | 施策マスタ | category: `cast` / `quit_risk` / `standalone` |
| `cast_initiative_status` | 現在の進捗(A群施策のみ) | UNIQUE(cast_id, initiative_id) / `event_date`(バースデー施策3の開催予定日 YYYY-MM-DD、他施策は未使用。2026-05-18 `ALTER TABLE` で追加) |
| `reports` | 全報告ログ(時系列) | quit_risk と standalone は通常 reports に書かれないが、quit_risk については `add-quit-risk` で書く |
| `quit_risks` | 退店リスク(専用) | UNIQUE(cast_id) / `is_resolved` |
| `restaurants` | ひろし顔きき計画(ガワだけ) | 現在は未使用 |

**`cast_initiative_status.status` は4値**: `not_started` / `declined` / `in_progress` / `done`

---

## 6. 施策と完了条件

| id | name | category | 進行中 (in_progress) | 完了 (done) | declined を使うか |
|---|---|---|---|---|---|
| 1 | TikTok動画買取 | cast | いけそう/撮影調整中 | データ受領 | **使う**(やらないと言われた) |
| 2 | 紹介CPの周知 | cast | いけそう | 実際に紹介してくれた | **使う**(断られた) |
| 3 | バースデーイベント開催のお願い | cast | 打診したが調整中(開催希望だが未確定) | **開催月が決まった**(「8月にやる」レベル) | **使わない**(「やらない」も in_progress 据え置き) |
| 4 | ひろしさん顔きき計画 | standalone | (報告対象外) | (報告対象外) | — |
| 5 | 辞めそうな子の見える化 | quit_risk | (`add-quit-risk` を使う) | — | — |

**バースデーの特別ルール**:
- 「やらない」と言われても `declined` にせず `in_progress` 据え置き。コンサルとしては
  引き続き説得の余地ありとみなす(declined は使わない)。
- **完了条件は「開催月が決まったら」**(2026-05-18 ユーザー決定で「日程確定のみ」から緩和)。
  「8月にやる」レベルで `done`。希望止まり・未確定(例「来年3月にやれるように」)は
  `in_progress` 据え置き。
- 開催日は `cast_initiative_status.event_date`(YYYY-MM-DD)に構造化保存し、
  `/calendar/`(予定タブ)に載せる。**日が未記載なら月初(1日)で登録**。
  投入時は `add-report`/`set-status` の `--event-date` を使う(§8)。
  `done`=確定として濃 emerald、`done` 以外=未確定として薄 emerald で表示。

---

## 7. ステータス遷移の制約

`scripts/ingest.py` の `_upsert_status` 参照。

- **TERMINAL_STATUSES = ("done", "declined")**: ここから別状態に変更するには `--force` 必須(誤操作防止)
- **同 status の上書き**: `updated_at` は触らない / `comment` のみ最新で上書き
- **通常変化**: status / comment / updated_at すべて更新

報告投入時の派生:
- A 群施策(category='cast'): `add-report` → reports 追加 + 任意で `--status` 指定で cast_initiative_status を upsert
- 退店リスク(category='quit_risk', id=5): `add-quit-risk` → reports + quit_risks の両方を upsert
- standalone(id=4): `add-report` は拒否、施策5は `add-quit-risk` を使う

`add-report` は同日同キャスト同施策の重複検出付き(`--force` で続行可能)。

---

## 8. ingest.py サブコマンド一覧

参照系:
- `list-kurofukus`
- `list-casts [--shift night|day] [--by KUROFUKU] [--status active|quit|all]`
- `list-initiatives`
- `list-recent-reports [--cast X] [--initiative N] [--days N] [--limit N]`
- `show-cast --name X`
- `show-status --cast X --initiative N`

報告投入:
- `add-report --date YYYY-MM-DD --cast X --initiative N --by KUROFUKU --content "..." [--reaction positive|neutral|negative] [--status not_started|declined|in_progress|done] [--comment "..."] [--event-date YYYY-MM-DD] [--raw "..."] [--force]`
- `set-status --cast X --initiative N --status ... [--comment "..."] [--event-date YYYY-MM-DD] [--force]`
  - `--event-date` はバースデー(施策3)専用。`/calendar/` に載る開催予定日。日未記載なら月初。
    既存と同 status で `--event-date` だけ渡すと `updated_at` を触らず日付のみ更新できる
- `add-quit-risk --cast X --certainty confirmed|likely [--date YYYY-MM-DD] [--reason "..."] --by KUROFUKU [--report-date YYYY-MM-DD] [--content "..."] [--raw "..."]`
- `update-quit-risk --cast X [--certainty ...] [--date ...] [--reason ...] [--resolve]`

マスタメンテ:
- `cast add --name X --by KUROFUKU --shift night|day`
- `cast rename --from X --to Y`
- `cast reassign --name X --to KUROFUKU`
- `cast quit --name X --date YYYY-MM-DD`(quit_risks があれば自動 is_resolved=1)
- `kurofuku rename --from X --to Y`
- `initiative add --name X --category cast|quit_risk|standalone [--description "..."]`

---

## 9. Claude(俺)の報告パース手順

> **実務マニュアルは `INGEST_PLAYBOOK.md` を参照。**
> ユーザーに見せちゃダメな用語の言い換え表、毎回聞くことの一覧、具体ロールプレイ例は
> そちらに切り出してある。新セッション開始時は CLAUDE.md と合わせて必ず通読。

1. 報告テキストを読み、**どのキャスト × どの施策の話か** を切り分ける
2. 既存マスタ照合:
   - キャスト未登録なら **必ずユーザーに「登録するか」確認**(担当黒服とシフトを聞く)。自動仮登録はしない。
   - 黒服未登録なら同様に確認
3. 1報告に複数施策が含まれる → 施策ごとに `reports` レコード分割
4. **status / reaction の判定で迷ったらユーザーに聞く**(明文化された判定基準は持たせない方針)
5. **日付の補完**: 年省略は当日年で補完。「先週」「昨日」など相対表現は必ず聞く。年またぎ判定で微妙なら聞く
6. 確定したら `ingest.py add-report` で投入(必要に応じて `--status --comment` 同梱)
7. 退店示唆があれば `add-quit-risk` を併用
8. 重複検出ヒットや `done`/`declined` 巻き戻しでは安易に `--force` を使わず、**先にユーザーに確認**

**reaction の典型判定例**(あくまで参考、迷ったら聞く):
- 「いけそう」「興味あり」「紹介してくれそう」 → `positive`
- 「無理」「嫌だ」「できない」 → `negative`
- 「考えとく」「保留」「微妙」 → `neutral` または NULL
- ニュアンスが読めない → NULL のまま

---

## 10. ビューの設計ルール

**カラム名・enum 値はビュー上で一切表示しない**(ユーザー強い要望):
- `in_progress` / `done` / `declined` / `not_started` などの英文字列は HTML 出力に出さない
- 日本語ラベルへの変換は `_macros.html` の `status_badge` マクロ + `STATUS_LABEL` 辞書(ingest.py)で一元管理
- `initiatives.description` は内部状態を匂わせない平易な日本語にする(現在の値はそのルールに従う)

**スマホ専用設計**:
- `max-w-md mx-auto`(28rem)で全ページ幅統一。レスポンシブ拡張(`sm:`/`md:`/`lg:`)は使わない
- 上部ナビなし、**下部固定タブ4分割**(`<nav>` を `fixed bottom-0`、`grid-cols-4`)。
  `safe-area-inset-bottom` 対応。タブは **ホーム / キャスト / 予定 / 黒服**
  (2026-05-18: 「退店」タブを「予定」=`/calendar/` に差し替え。退店は base.html の
  `is_calendar` で判定)。`/quit-risks/` はナビから外したが**ホームの退店リスクカード**
  (index.html)から到達できるので孤立しない
- タップ領域 44px 以上(`py-3` 以上)
- 入力欄は `text-base`(16px)で iOS の自動ズーム抑制
- 本文は `text-sm` / `text-base` 主体。`text-xs` は補助のみ

**密度を抑える工夫**:
- /initiatives/{id}/ の黒服別グループは `<details>` で**デフォルト折り畳み**。サマリー行に
  「進 N / 完 N / 無 N / 未 N」表示(ゼロは slate-300 で薄く、非ゼロのみ色付き)。
  左ボーダーは担当黒服カラー
- 時系列ログ:
  - /initiatives/{id}/ → 直近 **3件** 表示 + 過去は `<details>` 折り畳み
  - /casts/{id}/ → 直近 **5件** 表示 + 過去は `<details>` 折り畳み
- /casts/ → **sticky 検索ボックス**(キャスト名インクリメンタルフィルタ JS)。
  **黒服グループ分けは廃止**、全アクティブを1リスト。**最終接触が新しい順、未接触は最下位**。
  各行に担当黒服カラータグ + 最終接触日(未接触は rose で強調)
- /casts/ 末尾に「退店済み」`<details>`(default 閉じる、グレー表示)

**/kurofukus/(黒服別)**:
- 各黒服カード: 担当N人 / 報告N件 / **アクション終了率%** / 進行中N人 / 未接触N人 / 完了N人
- カードは**報告件数が多い順**。左ボーダーは担当黒服カラー
- **アクション終了率** = 担当キャスト × A群施策(3件)のうち done/declined に至ったペア数
  ÷ (担当人数 × A群施策数)。全期間。100% のみ % を緑、それ以外 slate-800
- 人数指標は**キャスト単位**で「担当 = 進行中 + 未接触 + 完了」が必ず成立:
  - 未接触 = A群すべて not_started
  - 完了 = **バースデー除く**(TikTok + 紹介CP)が全部 done/declined
    (バースデーは年1回しか動かず永遠に not_started のため判定から除外)
  - 進行中 = 残り(担当 − 未接触 − 完了)
- 黒服タップで **/kurofukus/{id}/**(担当キャスト一覧、最終接触が新しい順、
  各キャストに3施策ステータス + 退店リスクバッジ)

**黒服カラー(全ページ統一)**:
- build.py の `KUROFUKU_COLORS` が単一情報源。`fetch_all` で kurofuku / casts /
  quit_risks に `kurofuku_color` を attach、`_macros.html` の `kurofuku_tag` で表示
- 五上=sky / ひろし=violet / 川田=teal / 鴇田=orange / 向原=lime(落ち着いたトーン)
- 新黒服追加時は `KUROFUKU_COLORS` にも色を足す(未定義は slate フォールバック)

**色の方針(落ち着いたトーン、ナイトワーク色を出さない)**:
- ベース: slate(背景 50, テキスト 700-800)
- progress 系(`_macros.html` status_badge):
  - in_progress: amber-100/amber-800「進行中」
  - done: emerald-100/emerald-800「完了」
  - declined: rose-50/rose-700「無理そう」(完了の駄目パターン、強くは主張しない)
  - not_started: slate-100/slate-500「未着手」
- quit_risk バッジ(塗りつぶしで黒服タグと形状を区別、目立たせすぎない):
  - confirmed: `bg-red-500/90 text-white` + ⚠「退店確定」
  - likely: `bg-orange-400/90 text-white` + ⚠「退店リスク」(運用上未使用)
- 数字は ゼロを slate-300 で薄く、非ゼロのみ色付き。`tabular-nums` で桁揃え

**カレンダー(/quit-risks/ 内タブ。従来どおり維持)**:
- 今月〜+12ヶ月、過去送り不可
- vanilla JS で月ごとに描画、各日に max 2件まで表示 + 「+N」省略表示

**/calendar/(下タブ「予定」、2026-05-18 新設。2026-05-18 リスト/カレンダー2タブ化)**:
- `templates/calendar.html`。build.py `context_calendar` が単一情報源
- **退店リスクページと同じ「リスト / カレンダー」2タブ**(デフォルト=リスト)
- 退店(`quit_risks`)+ バースデー(`cast_initiative_status.event_date`、アクティブのみ)を集約
  - リスト: `list_months`(**月ごとに見出しで区切る** `_group_by_month`)/ `list_undated`。
    各行は日付を リンクの**外・上部に大きく**出し、その下のリンクにバッジ+名前+黒服タグ+メモ。
    **予定日が過ぎた行はリストから除外**(カレンダーには残す)
  - カレンダー: `calendar_events` を月グリッド描画(各セルからキャスト個別へリンク)
  - 同じ「月区切り + 過去はリストから除外」を /quit-risks/ リスト(`dated_months`)と
    /initiatives/3/ リスト(`bday_months`)にも適用(全リスト統一、2026-05-18)
- 色: 退店確定=`bg-red-500`、退店リスク=`bg-orange-500`(運用上未使用)、
  バースデー確定(status=done)=`bg-emerald-500`、バースデー未確定(done以外)=`bg-emerald-300`
- バッジ文言は `_macros.html`: 退店=`quit_risk_badge`、バースデー=`birthday_badge`
  (「バースデー確定」/「バースデー未確定」。"開催確定" 等の曖昧表現にしない)
- 退店の日付未定もリストの「日付未定」に出す(/quit-risks/ 側とは別集計)
- /quit-risks/ 内カレンダーとはコード独立(quit_risks.html / context_quit_risks は不変)
- Tailwind CDN 対策で dot/バッジ色クラスは凡例・マクロにリテラルで必ず出す(§10 色方針と同様)

**/initiatives/3/(バースデー施策ページのみ、2026-05-18 タブ化)**:
- `initiative.html` は全 A 群共通。バースデー(`is_birthday`)のときだけ
  **「進捗 / リスト / カレンダー」3タブ**(デフォルト=進捗=従来の統計/黒服別/ログ)。
  施策1・2 はタブ無しの従来表示(`is_birthday=False` で素通り)
- リストは **確定(done)/ 未確定(in_progress+日付)/ 日付未定(in_progress+日付なし)** の3分割。
  `context_initiative` が `bday_confirmed/bday_tentative/bday_undated/bday_calendar` を渡す
- 行レイアウト・バッジ・色は /calendar/ と統一(日付はリンク外・上部)
- **バースデーのメモ(reports.content / cast_initiative_status.comment)に日付を書かない**。
  日付は `event_date` 一本化、リスト/カレンダー/バッジが表示を担う。メモは状態だけ
  (現行は全件「開催予定」。2026-05-18 ユーザー指示で重複解消)

---

## 11. PWA(ホーム画面追加)対応

- `dist/manifest.webmanifest`(build.py が生成)
- `dist/icons/icon-{180,192,512}.png`(stdlib `struct`+`zlib` で直接 PNG 生成、依存追加なし)
- アイコン: slate-700 背景に白3本横線
- iOS Safari → 共有 → ホーム画面に追加 で standalone 起動
- localStorage のパスワード認証も維持される

---

## 12. 退店リスク管理

- `quit_risks` テーブル(キャスト1人1行、UNIQUE)
- `certainty`: `confirmed` 固定運用。**本人が辞めると言った話だけを登録する**。
  周り発の観測(`likely`)は DB に入れない方針(2026-05-11 確定)。スキーマには
  `likely` を残してあるが運用上未使用。Claude は「やめるの温度感は?」を聞かない。
- `expected_quit_date`: 確定日 or **NULL(未定)** のどちらか。
  「多分5月末」のように曖昧な場合でも未定として登録できる(NOT NULL なし)。
  Claude は「退店時期、◯月◯日でいいですか? それとも未定で?」のように
  **未定の選択肢を含めて**確認する。
- `update-quit-risk` で確度や日付は後から更新可
- `cast quit` を打つと自動で対応する quit_risks の `is_resolved=1`
- 解消の意味の使い分け:
  - `is_resolved=1` + `casts.status='quit'` → 実退店で解消
  - `is_resolved=1` + `casts.status='active'` → 辞めない宣言で解消
- `/quit-risks/` には `is_resolved=0` のみ表示。「日付確定」と「日付未定」で
  リストを分けて出す(templates/quit_risks.html)。カレンダーには確定日のみ載る。
  解消済みはキャスト個別ページで履歴確認

---

## 13. 黒服シード(現在登録済み)

`五上` / `ひろし` / `川田` / `鴇田` / `向原`

`--by` には name 文字列を渡す(例: `--by ひろし`)。

---

## 14. キャストシード(現在登録済み、計41名)

| 担当 | shift | キャスト |
|---|---|---|
| 五上 | night | えま、みお、ありん、まなみ、かりな、はるな、まお、まな、ゆう、かの、あん、ひかり (12) |
| ひろし | night | りり、かんな、みなみ、はづき、ゆうり、のあ、もえ、るい、るう、あいみ、かえで、りおん (12) |
| 川田 | night | くう (1) |
| ひろし | day | ゆりあ、あきな (2) |
| 鴇田 | day | のん、みく、あすか、ゆうか、れな、ちなつ、ゆりか (7) |
| 向原 | day | りお、らむ、もか、ももか、ゆゆ、せり、れん (7) |

同名キャストは存在しない前提。

---

## 15. 現在のデータ状態(本番運用中)

> ⚠️ **絶対厳守: `init_db.py --force` を打つな**。本番データが入っている。
> `--force` は DB を全削除して再シードする。打つと**本番の報告・退店リスクが全消失**する。
> スキーマ変更が必要なときは `--force` ではなく `ALTER TABLE` 等で対応(過去に
> expected_quit_date の NULL 化もコード変更のみで対応した実績あり)。

2026-05-12 時点(本番運用開始済み):

- かつてのテストサンプル(えま/はるな/りり/くう)は `init_db.py --force` で
  リセット済み。**もう存在しない**
- 2026-05-01〜05-10 の黒服報告を **30件投入済み**(紹介CP / TikTok 中心)
- 新キャスト **ゆい**(鴇田/昼)を 05-05 報告で追加(シードは41名、現在42名)
- 退店リスク **2件**:
  - のん(鴇田/昼): confirmed / 2026-05-15 / 家庭の事情
  - あすか(鴇田/昼): confirmed / 2026-08-15 / 痩せたので錦に行きたい+卒業イベント打つ予定
- 最新の正確な状態は DB を直接見る(`ingest.py show-cast` 等)。本書のこの節は
  スナップショットなので、細部は信用しすぎず必ず実データを確認すること

---

## 16. デプロイ(稼働中)

**実装・稼働済み**。`.github/workflows/deploy.yml` で push → GitHub Actions → Pages 自動デプロイ。

- GitHub リポジトリ: **`matsurika22/imacoco`**(public)
- 本番URL: **`https://matsurika22.github.io/imacoco/`**
- `SITE_PASSWORD` は repo Secrets に登録済み(CI がビルド時に注入。クライアントには
  SHA-256 ハッシュのみ渡る)
- `main` に push すると Actions が走り、数十秒で本番反映

**運用フロー(報告投入後)**:
1. `ingest.py` で DB 更新(→ INGEST_PLAYBOOK §2 Step 1〜7)
2. (任意)ローカル確認 `SITE_PASSWORD=test1234 .venv/bin/python scripts/build.py`
3. **ユーザーに commit/push 確認**(CLAUDE.md §17、勝手に push しない)
4. OK が出たら `git add data/reports.db` → commit(WHY を書く)→ `git push`
5. `gh run list` で Actions success を確認
- ドキュメント/テンプレ変更も同様に commit/push で自動反映

**公開方針(2026-05-09 確定、稼働後も維持)**:
- public リポジトリ + GitHub Pages(無料)
- `data/reports.db` を含むソース全体が誰でも取得可能。ユーザーはリスクを**承諾済み**
  - JS パスワードゲートは「閲覧の入り口の鍵」でデータ暗号化ではない
  - リポジトリを知る第三者は理論上 `data/reports.db` を直接落として中身を見られる
- private 化したい場合は GitHub Pro 契約 + Settings 切替(ワークフロー変更不要)

注意:
- `dist/` は gitignore。CI 内でビルドして deploy artifact にする
- `data/reports.db` は repo にバイナリで含まれる(運用方針通り)。CI はそれを読んでビルド
- `SITE_PASSWORD: changeme` は build.py が拒否するので CI が落ちる(本番事故防止)
- パスワード変更は repo Secrets の `SITE_PASSWORD` 更新 → 次 push で再ビルド。
  全閲覧者の localStorage は旧ハッシュなので再ログインが必要になる

---

## 17. 進め方の作法(ユーザー指示)

- **不明点・選択肢が出たら必ずユーザーに聞いてから進める**(勝手に決めない、特に新スキーマや表示文言)
- **各ステップ完了ごとに動作確認 → ユーザー確認**してから次へ
- 大きな仮定を置く前に必ず質問
- コミットはユーザーの明示指示があった時のみ
- 設計判断はコメントに「なぜ」を残す

---

## 18. 絶対にやってはいけないこと

- カラム名 / enum 値(`in_progress` 等の英文字列)を view 上に出す
- バースデー(施策3)で `declined` を使う
- パスワードに `changeme` を残したまま本番ビルド(build.py で拒否済み)
- `done` / `declined` を勝手に `--force` で巻き戻す(必ずユーザー確認)
- 自動コミット
- 既存の CLAUDE.md / rules.md を許可なく上書き(追記は可)
- 依存ライブラリをトレードオフの説明なしに追加(現状 jinja2 のみ)

---

## 19. 直近のユーザー決定の根拠メモ(忘れないように)

- **退店リスク サマリは「直近3ヶ月」**(以前は1ヶ月だったが2026/05/09 に変更)。
  → `build.py` の `within_3months` キー、cutoff = today + 90 days
- **/casts/ は検索ボックス必須**(2026/05/09)。グループは折り畳まない(検索があるため)
- **/casts/{id}/ 報告ログは直近5件 + 過去 details**(2026/05/09)
- **declined は「完了の駄目パターン」**(2026/05/09)。done と対称的に保護。バースデーでは使わない
- **退店リスクは常時 confirmed・時期未定OK**(2026/05/11)。本人発言ベースのみ登録、
  観測(likely)は入れない、温度感を聞かない。詳細 §12 / INGEST_PLAYBOOK §3
- **/casts/ は黒服グループ廃止 → 最終接触順1リスト + 担当黒服カラータグ**(2026/05/12)。
  「動きの少ない子を炙り出す」目的に合わせ、未接触を最下位ではなく上げる案も検討したが
  最終接触新しい順(未接触=最下位)に確定
- **接触率 → アクション終了率に再定義**(2026/05/12)。変遷: 触れたキャスト÷担当 →
  報告件数÷(担当×施策) → **done/declined ペア ÷ (担当×A群施策3)**。
  「1回触れた」ではなく「これ以上できることがない所まで進めた」を成果とみなす
- **/kurofukus/ 完了判定はバースデー除外**(2026/05/12)。バースデーは年1回しか動かず
  永遠に not_started なので、TikTok+紹介CP の2施策で完了を判定
- **declined ラベル「無理だった」→「無理そう」**(2026/05/12)。確定の言い回しが強すぎ、
  推察ニュアンスに。`_macros.html` / `ingest.py STATUS_LABEL` / INGEST_PLAYBOOK 反映済み
- **黒服ごとに固有色**(2026/05/12)。build.py `KUROFUKU_COLORS` が単一情報源(§10)
- **OG/meta タグ追加**(2026/05/12)。LINE 等のリンクプレビューが本文を拾う問題対策。
  `og:image` は絶対URL必須のため `base.html` にハードコード(リポジトリ移行時は要更新)
- **バースデー完了条件を「日程確定のみ」→「開催月が決まったら」に緩和**(2026/05/18)。
  「8月にやる」レベルで done。希望止まり・未確定は in_progress 据え置き。§6 参照
- **バースデー開催日を構造化保存**(2026/05/18)。前回「メモ運用のまま」だったが
  カレンダー化のため方針変更。`cast_initiative_status.event_date` を `ALTER TABLE` で
  追加(本番DB稼働中につき `--force` 不使用、§15)。投入は `--event-date`(§8)
- **下タブ「退店」→「予定」差し替え + /calendar/ 新設**(2026/05/18)。退店+バースデーを
  1カレンダーに集約。/quit-risks/ はナビから外したがホームの退店リスクカードから到達。
  §10 / §3 参照
- **/予定・/initiatives/3/ を退店リスク型タブに**(2026/05/18)。/予定=リスト/カレンダー、
  /initiatives/3/=進捗/リスト/カレンダー(進捗デフォルト、施策1・2は不変)。リストは
  日付をリンク外・上部に大きく出す形に。§10 参照
- **バースデーのメモは日付を書かない運用に**(2026/05/18)。日付カラム(event_date)が
  無かった頃の名残でメモに年月が重複していたのを解消。メモ(content/comment)は状態のみ
  (全件「開催予定」)、日付は event_date 一本化。バッジ文言は「バースデー確定/未確定」
- **全リスト月区切り + 過去はリストから除外**(2026/05/18)。/予定・/initiatives/3/・
  /quit-risks/ のリストを月見出しで区切り、予定日が過ぎた行はリストから消す
  (カレンダーには残す)。`_group_by_month` 共通。§10 参照
- **確定退店日が過ぎた人は都度確認で cast quit**(2026/05/18)。自動退店化はしない。
  Claude は未解消 quit_risks の確定日経過を見つけたらユーザーに確認 → OK で cast quit
  (例: のん 2026-05-15 を本日処理)。INGEST_PLAYBOOK §8-1 参照
