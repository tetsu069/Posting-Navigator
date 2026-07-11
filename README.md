# Posting Navigator

KMZの町丁目ポリゴンとOpenStreetMap道路データから、ポスティング用巡回ルートを生成し、Googleマイマップへ読み込めるKML/KMZを出力するプロジェクトです。

## v0.3.0

- KMZから町丁目を名前で抽出
- Overpass APIによるOSM道路自動取得、ミラー切替、キャッシュ
- 町丁目境界で道路をクリップし、交差点で道路グラフ化
- 幹線道路の重複通行へ罰則を付けた巡回ルート生成
- 袋小路を含む全対象道路の巡回
- 開始地点指定と最寄り道路への補正
- 巡回順序を維持した複数担当者への距離均等分割
- 全担当統合KML/KMZ、担当者別KML/KMZ、GeoJSON、CSV、summary.json出力

> `offline-fixture` は処理系の確認専用で実在道路ではありません。実運用時は `summary.json` の `data_mode` が `osm` であることを確認してください。

## セットアップ

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e .
```

## 北新宿一丁目を4担当へ分割

```bash
posting-navigator build \
  --kmz data/input/shinjuku_posting_map.kmz \
  --area 北新宿一丁目 \
  --output output/kita-shinjuku-1 \
  --start-lon 139.6925 \
  --start-lat 35.7005 \
  --workers 4
```

実OSM以外を許可しない場合は `--no-offline-fallback` を追加します。

## 出力

- `posting_navigator.kml / .kmz`: 全体ルートと担当別レイヤー
- `workers/worker_01.kml / .kmz` など: 各担当者専用
- `posting_navigator.geojson`: GIS・デバッグ用
- `assignments.csv`: 担当距離、開始・終了座標
- `boundary.geojson`: 町丁目境界
- `summary.json`: 距離、重複率、袋小路数、担当別集計、データモード

## 分割方式

中国人郵便配達問題の近似解として生成した一続きの巡回順序を、累積距離が担当者数分の等分点に達した場所で分割します。道路の途中でも補間点を作るため、担当距離の差をほぼゼロにできます。担当区間は連続し、前担当の終了地点と次担当の開始地点が一致します。

## テスト

```bash
pytest -q
```

## Web画面版（v0.4.0）

Windowsでは `start_web.bat` をダブルクリックしてください。初回だけ仮想環境と必要ライブラリを自動準備し、ブラウザで `http://127.0.0.1:8787` を開きます。

画面の操作順：
1. KMZを選択
2. 対象町丁目を選択
3. 担当人数を指定
4. 任意で地図をクリックして開始地点を指定
5. 「巡回ルートを生成」
6. 統合KMZまたは成果物一式ZIPをダウンロード

注意：GitHub PagesだけではPythonのルート計算エンジンを実行できないため、v0.4.0はPC上で動くローカルWebアプリです。将来の公開版はAPIサーバーを別途配置します。

## 公開版（v0.5.0）: GitHub Pages + APIサーバー

構成は次の2層です。

- `docs/`: GitHub Pagesで配信する静的フロントエンド
- `src/posting_navigator/webapp.py`: Render等で動かすPython API

### 1. APIをRenderへ公開

1. このリポジトリをGitHubへpush
2. Renderで「Blueprint」を作成し、リポジトリの`render.yaml`を選択
3. デプロイ後のURL（例: `https://posting-navigator-api.onrender.com`）を控える
4. Render環境変数`ALLOWED_ORIGINS`をGitHub PagesのURLに変更

### 2. GitHub Pagesを接続

`docs/config.js`を編集します。

```js
window.POSTING_NAVIGATOR_API = 'https://実際のAPI名.onrender.com';
```

GitHubの Settings → Pages → Source で「GitHub Actions」を選択します。mainへpushすると`.github/workflows/pages.yml`が自動公開します。

### 3. 接続確認

APIの`/api/health`が`status: ok`を返し、Pages画面下部の「接続先API」に公開URLが表示されれば接続済みです。

### 注意

Renderの無料枠では休止後の初回起動に時間がかかる場合があります。また、生成ファイルはAPIサーバーの一時ディスクに保存されるため、生成直後にダウンロードしてください。本格運用では永続ストレージまたはS3互換ストレージへの移行を推奨します。
