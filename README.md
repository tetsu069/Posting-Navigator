# Posting Navigator v1.0.3

町丁目KMZとOpenStreetMap道路からポスティング巡回ルートを生成し、現場ではスマホGPSで配布済み区間を自動記録するWeb/PWAです。GitHub Pagesを画面、RenderをPython APIとして利用できます。


## v1.0.3 で追加・修正

- 道路網を単に色塗りする方式から、**巡回順序付きステップ列**へ変更
- Chinese Postman方式で対象道路をできるだけ一筆書きに近く巡回
- 小さな非連結道路成分を最大成分から落とさず、全成分を巡回対象に保持
- 各巡回ステップに `seq`（順番）・道路種別・OSM ID・重複通行情報を保持
- 地図上に進行方向の矢印 `→` を約45m間隔で表示
- 巡回順番号を10ステップごとに表示し、START / GOALも表示
- 通常巡回=赤、必要な重複通行=橙、非連結道路への移動=灰色点線で区別
- 現場モードにも担当ルートの進行方向矢印を表示
- メトリクスに対象道路本数・非連結成分数・移動区間距離を追加
- v1.0.2のOverpass複数ミラー・User-Agent対応、町丁目境界/薄色表示を維持
- GitHub Pages公開用Render URLを `docs/config.js` に保持

## v1.0で追加

- PWA対応（ホーム画面へ追加、アプリ風全画面表示）
- スマホGPS追跡
- 担当ルートの未配布=赤 / 配布済み=緑表示
- GPS位置がルートから指定距離内に入ると区間を自動完了
- 端末LocalStorageへ進捗保存
- 6桁共有コードによる複数端末参加
- 担当者別進捗をAPIへ保存し約5秒ごとに同期
- Google Identity Servicesログイン（任意設定）
- Googleログイン利用時、自分が作成した共有プロジェクト一覧を表示
- 従来のKML/KMZ/GeoJSON/CSV出力を維持

## ローカル起動

Windowsは `start_web.bat` をダブルクリックします。ブラウザで `http://127.0.0.1:8787` が開きます。

手動の場合：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e .
posting-navigator-web
```

## 公開構成

```text
GitHub Pages (docs/)
        |
        | HTTPS API
        v
Render (posting_navigator.webapp)
        |
        +-- Overpass / OSM
        +-- SQLite progress DB
```

### 1. RenderへAPIを公開

1. GitHubへこのリポジトリをpush。
2. Render → New → Blueprint → このリポジトリを選択。
3. `render.yaml` を適用。
4. デプロイ後のURL（例 `https://posting-navigator-api.onrender.com`）を控える。
5. `/api/health` を開き、`"version":"1.0.3"` を確認。

Renderの環境変数：

- `ALLOWED_ORIGINS`: `https://あなたのGitHubユーザー名.github.io`
- `GPS_THRESHOLD_M`: 初期の自動完了判定距離。既定18m
- `SYNC_INTERVAL_MS`: チーム同期間隔。既定5000ms
- `GOOGLE_CLIENT_ID`: Googleログインを使う場合のみ設定
- `POSTING_NAV_DB`: SQLite DBパス

### 2. GitHub PagesをAPIへ接続

`docs/config.js` を編集します。

```js
window.POSTING_NAVIGATOR_API = 'https://実際のAPI名.onrender.com';
```

GitHub → Settings → Pages → Source を **GitHub Actions** にします。`.github/workflows/pages.yml` が `docs/` を公開します。

### 3. PWAをスマホへ追加

Android/Chromeではページを開き「アプリをインストール」または「ホーム画面に追加」。iPhone/Safariでは共有ボタン → 「ホーム画面に追加」です。GPS利用にはHTTPSが必要なので、公開運用ではGitHub PagesのHTTPS URLを使ってください。

## Googleログイン設定（任意）

1. Google Cloud ConsoleでOAuth 2.0 **Web application** のClient IDを作成。
2. Authorized JavaScript originsへGitHub Pagesのオリジンを追加。
   - 例: `https://YOUR-GITHUB-USER.github.io`
3. Renderの `GOOGLE_CLIENT_ID` にClient IDを設定。
4. Renderを再デプロイ。

Googleログインなしでも、共有コードと進捗同期は利用できます。ログインを使うと「自分が作ったプロジェクト一覧」を呼び戻せます。

## 現場での使い方

1. 「ルート作成」でKMZ → 町丁目 → 人数を指定し、巡回ルートを生成。
2. 自動的に共有コードが発行される。
3. 各担当端末は共有コードで参加し、「現場モード」で自分の担当を選択。
4. 「GPS開始」。ルートから設定距離内を通ると、該当区間が赤から緑へ変化。
5. 「チーム」タブで全担当の進捗を確認。

GPS誤差や並行道路が近接する場所では誤判定の可能性があります。現場では判定距離を8〜40mで調整できます。

## データ永続化について

進捗は端末のLocalStorageにも保存されるため、同じ端末ではブラウザ再読み込み後も復元できます。API側はSQLiteです。Renderの一時ディスクだけで運用するとサービス再起動・再デプロイでDBが消える可能性があります。本番で履歴を永続保存する場合は、Render Persistent Diskを `/var/data` などにマウントし、`POSTING_NAV_DB=/var/data/posting_navigator.db` に変更してください（利用プランの条件を確認してください）。将来PostgreSQL等へ置換できるようAPI層を分離しています。

## テスト

```bash
pytest -q
node --check docs/app.js
```

## 既存CLI

```bash
posting-navigator build \
  --kmz data/input/shinjuku_posting_map.kmz \
  --area 北新宿一丁目 \
  --output output/kita-shinjuku-1 \
  --start-lon 139.6925 \
  --start-lat 35.7005 \
  --workers 4
```

実OSM以外を許可しない場合は `--no-offline-fallback` を追加します。実運用時は `summary.json` の `data_mode` が `osm` であることを確認してください。
