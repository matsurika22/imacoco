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
│   ├── casts.html           ← sticky 検索ボックス + JS フィルタ
│   ├── cast.html
│   ├── quit_risks.html      ← リスト/カレンダータブ切替
│   ├── kurofukus.html
│   └── restaurants.html     ← ナビ非表示、URL 直打ちのみ
├── dist/                    ← ビルド成果物。gitignore
└── .github/workflows/       ← Step 4 でここに deploy.yml(未着手)
```

---

## 4. 起動・ビルド・確認コマンド

**前提**:
- 作業時は必ず `cd /Users/mk/dev/shisaku-tracker` してから。venv はプロジェクト内。
- **Python 3.12** を想定(現状 `python3` = 3.12.5)。Step 4 の GitHub Actions も 3.12 でセットアップする
- 外部依存は **jinja2 のみ**(`requirements.txt`)。残りは Python stdlib

```bash
# 初回 / 環境再構築
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# DB を新規作成(既存があれば --force 必要)
python3 scripts/init_db.py
python3 scripts/init_db.py --force

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
| `cast_initiative_status` | 現在の進捗(A群施策のみ) | UNIQUE(cast_id, initiative_id) |
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
| 3 | バースデーイベント開催のお願い | cast | 打診したが調整中 | 日程確定 | **使わない**(「やらない」も in_progress 据え置き) |
| 4 | ひろしさん顔きき計画 | standalone | (報告対象外) | (報告対象外) | — |
| 5 | 辞めそうな子の見える化 | quit_risk | (`add-quit-risk` を使う) | — | — |

**バースデーの特別ルール**: 「やらない」と言われても `declined` にせず `in_progress` 据え置き。
コンサルとしては引き続き説得の余地ありとみなす。「日程確定」のみ `done` 昇格。

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
- `add-report --date YYYY-MM-DD --cast X --initiative N --by KUROFUKU --content "..." [--reaction positive|neutral|negative] [--status not_started|declined|in_progress|done] [--comment "..."] [--raw "..."] [--force]`
- `set-status --cast X --initiative N --status ... [--comment "..."] [--force]`
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
- 上部ナビなし、**下部固定タブ**(`<nav>` を `fixed bottom-0`)。`safe-area-inset-bottom` 対応
- タップ領域 44px 以上(`py-3` 以上)
- 入力欄は `text-base`(16px)で iOS の自動ズーム抑制
- 本文は `text-sm` / `text-base` 主体。`text-xs` は補助のみ

**密度を抑える工夫**:
- /initiatives/{id}/ の黒服別グループは `<details>` で**デフォルト折り畳み**。サマリー行に「進N 完N 無N 未N」表示。
- 時系列ログ:
  - /initiatives/{id}/ → 直近 **3件** 表示 + 過去は `<details>` 折り畳み
  - /casts/{id}/ → 直近 **5件** 表示 + 過去は `<details>` 折り畳み
- /casts/ → **sticky 検索ボックス**(キャスト名インクリメンタルフィルタ JS)。グループは展開のまま
- /casts/ 末尾に「退店済み」`<details>`(default 閉じる、グレー表示)

**色の方針(落ち着いたトーン、ナイトワーク色を出さない)**:
- ベース: slate(背景 50, テキスト 700-800)
- progress 系:
  - in_progress: amber-700
  - done: emerald-700
  - declined: rose-50/rose-600(完了の駄目パターン、強くは主張しない)
  - not_started: slate-400
- quit_risk:
  - confirmed: red-700
  - likely: orange-600

**カレンダー(/quit-risks/)**:
- 今月〜+12ヶ月、過去送り不可
- vanilla JS で月ごとに描画、各日に max 2件まで表示 + 「+N」省略表示

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
- `certainty`: `confirmed`(本人が辞めると言った)/ `likely`(辞めそう)
- `expected_quit_date`: 「多分5月末」などの曖昧な日付も**確定値で登録**(構造で「多分」を持たない方針)
- 確度が変わったら `update-quit-risk` で手動更新
- `cast quit` を打つと自動で対応する quit_risks の `is_resolved=1`
- 解消の意味の使い分け:
  - `is_resolved=1` + `casts.status='quit'` → 実退店で解消
  - `is_resolved=1` + `casts.status='active'` → 辞めない宣言で解消
- `/quit-risks/` には `is_resolved=0` のみ表示。解消済みはキャスト個別ページで履歴確認

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

## 15. 現在のテストデータ状態(本番運用前のサンプル)

セッション切替時点のスナップショット:

- **えま** (五上/夜): TikTok=in_progress (反応薄い) / 紹介CP=in_progress (1人紹介してくれそう)
- **はるな** (五上/夜): TikTok=declined (絶対やらないと言われた) ← 4状態モデル動作確認用
- **りり** (ひろし/夜): quit_risks に likely 登録 / 2026-05-31 / 引き抜き話 / 報告ログにも1件
- **くう** (川田/夜): status=quit / quit_date=2026-04-30(退店済み一覧確認用)

本番運用直前に `python3 scripts/init_db.py --force` で全部リセット予定。

---

## 16. デプロイ計画(Step 4、次セッション着手)

**まだ未実装**。`.github/workflows/deploy.yml` を作成して GitHub Actions → GitHub Pages に。

**現時点の git 状態(重要)**:
- `git init` 済みだが **まだ1度もコミットしていない**(`fatal: ... does not have any commits yet`)
- `.github/workflows/` は空ディレクトリだけ既に存在(`deploy.yml` 未作成)
- すべて未追跡ファイル
- Step 4 着手の最初のステップは「初回コミット → GitHub にリポジトリ作成 → push」になる
- 初回コミットの粒度は「Step 1〜3 一括」で OK(細分化する意義なし、プロジェクト立ち上げ commit として)

**公開方針(2026-05-09 確定)**:
- **public リポジトリ + GitHub Pages(無料)** を採用
- ユーザーは「`reports.db` を含むソース全体が誰でもダウンロード可能になる」というリスクを**認識した上で承諾済み**
  - JS パスワードゲートは「閲覧の入り口の鍵」であって「データ自体の暗号化」ではない
  - URL を知っていてリポジトリも知っている第三者は理論上 `data/reports.db` を直接落として中身を見られる
- もし将来 private 化したい場合は GitHub Pro($4/月)契約 + Settings 切替で対応可能(ワークフローは変更不要)
- 公開 URL は `https://<github-username>.github.io/shisaku-tracker/` を想定

設計案(本書 §10 のプランと整合):

```yaml
# 概要
on: push to main
jobs:
  build:
    - checkout
    - setup-python 3.12
    - pip install -r requirements.txt
    - run scripts/build.py with env SITE_PASSWORD: ${{ secrets.SITE_PASSWORD }}
    - upload-pages-artifact dist/
  deploy:
    - actions/deploy-pages
```

事前準備:
1. GitHub にリポジトリ作成(public 推奨。private にすると Pages は GitHub Pro 課金が必要)
2. リポジトリ Settings → Pages → Source: GitHub Actions
3. Settings → Secrets and variables → Actions → New repository secret: `SITE_PASSWORD`
4. push → Actions 緑 → Pages URL が払い出される
5. 黒服に URL とパスワードを伝達

注意:
- `dist/` は gitignore のまま。CI 内でビルドして deploy artifact にする。
- `data/reports.db` は repo にバイナリで含まれる(運用方針通り)。CI はそれを読んでビルド。
- `SITE_PASSWORD: changeme` は build.py が拒否するので CI が落ちる(本番事故防止)。

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
