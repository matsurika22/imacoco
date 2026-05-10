# shisaku-tracker

キャバクラ施策進捗管理ツール。黒服から受けた報告テキストを CLI 経由でDBに記録し、GitHub Pages の静的サイトで進捗を可視化する。

## ツールの設計思想

データ収集の精度より「**黒服がきちんと完遂できたか、できなくても一目でわかる可視化**」を優先する。
- `done` を簡単に巻き戻さない(再話があっても据え置き)
- 完了条件は施策ごとに明確に定義
- 動きの少ない黒服を炙り出す表示を優先

## 構成

- `data/reports.db` — SQLite。Git でバイナリ直 push(一人運用前提)
- `scripts/init_db.py` — スキーマ作成 + 初期シード投入
- `scripts/ingest.py` — DB 操作 CLI(参照・登録・更新)。**自然言語パースは行わない**(Claude Code が対話で行う)
- `scripts/build.py` — SQLite を読んで `dist/` に静的 HTML を生成

## 運用フロー

1. 黒服から LINE で報告が来たら、コンサルが Claude Code に貼り付ける
2. Claude Code が報告内容をパースし、`ingest.py` のサブコマンドで DB に書き込む
3. push すると GitHub Actions がビルド→Pages デプロイ
4. 黒服はパスワード付きの公開 URL でダッシュボードを閲覧

## セットアップ

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/init_db.py        # 初回のみ。既存DBを再作成するなら --force
```

## 黒服を呼び出すとき

```bash
python scripts/ingest.py <subcommand> --by <黒服name> ...
```

`--by` の値は `kurofukus.name` の文字列(例: `--by hiroshi`)。
