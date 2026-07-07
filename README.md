# shopify-order-watcher

Shopify（North Edge Standard）に**新しい注文が入ったらChatworkへ通知**するアプリ。
[film-camera-watcher](https://github.com/seaholicfishing-rgb/film-camera-watcher) / chatwork-to-wp と同じ構成
（Python標準ライブラリのみ・GitHub Actions・cron-job.orgで15分ごと起動・差分検知）。

## 動作

1. cron-job.org が15分ごとに GitHub Actions を起動（workflow_dispatch）
2. `watcher.py` が Shopify Admin API（GraphQL）で直近の注文を取得
3. `state.json` に無い新規注文だけ Chatwork へ通知（Botアカウント→本人へ [To:] メンション）
4. 通知済み注文IDを `state.json` に記録してコミット

- 稼働時間: JST 8〜22時。深夜の注文は翌朝8時台にまとめて通知（差分検知なので取りこぼし無し）
- 通知内容: 注文番号 / 商品と数量 / 合計金額 / 支払い状況 / 購入者名・地域 / 日時 / 管理画面の注文URL
- **顧客情報はChatworkに送るだけでリポジトリには一切保存しない**（state.jsonは注文IDのみ）

## セットアップ

### 1. Shopify Dev Dashboard でアプリを作って Client ID / Secret を取得

このストアは旧「カスタムアプリ」が廃止され新方式（Dev Dashboard）に移行済み。
トークン(`shpat_`)は画面に表示されず、**Client ID / Client Secret を実行時にトークンへ交換する**
（client credentials grant。アプリとストアが同じ組織のときだけ使える方式。トークンは24時間有効・毎回取り直す）。

1. [Dev Dashboard](https://dev.shopify.com) → **アプリ** → **アプリを作成**（名前 `order-watcher`）
2. アプリの設定で **Admin APIスコープ `read_orders`** を付与し、保護顧客データ(Protected customer data)のアクセスを設定
3. バージョンを**リリース**し、ストア `cvz1fy-qm` に**インストール**
4. アプリの **設定(Settings)** から **Client ID** と **Client secret** をコピー

### 2. GitHub リポジトリと Secrets

```
gh repo create seaholicfishing-rgb/shopify-order-watcher --public --source . --push
gh secret set SHOPIFY_CLIENT_ID       # Dev DashboardアプリのClient ID
gh secret set SHOPIFY_CLIENT_SECRET   # 同 Client secret
gh secret set CHATWORK_TOKEN          # Botアカウント(NES)のChatworkトークン
```

※ 旧方式の `shpat_` トークンが手に入る場合は、代わりに Secret `SHOPIFY_TOKEN` に入れれば
そのまま使われる（watcher.pyが両対応）。

Publicなのは Actions 無料枠を消費しないため（chatwork-to-wpと同じ判断）。
秘密情報はコードに含めず GitHub Secrets のみ。

### 3. 初期化（既存注文のサイレント記録）

ローカルで:

```
set SHOPIFY_CLIENT_ID=...
set SHOPIFY_CLIENT_SECRET=...
python watcher.py --init
git add state.json && git commit -m "init state" && git push
```

### 4. cron-job.org で15分ごと起動

既存ジョブ「camera-wacter」をClone → URLを差し替え:

```
POST https://api.github.com/repos/seaholicfishing-rgb/shopify-order-watcher/actions/workflows/watch.yml/dispatches
body: {"ref":"main"}
```

ヘッダー4種（Authorization Bearer <PAT> / Accept / X-GitHub-Api-Version / Content-Type）は既存ジョブと同じ。

⚠️ **fine-grained PAT「camera-watcher」の対象リポジトリに `shopify-order-watcher` を追加すること**
（GitHub → Settings → Developer settings → Fine-grained tokens）。追加しないと外部起動が401になる。

## コマンド

```
python watcher.py          # 通常実行（新規注文があれば通知）
python watcher.py --init   # サイレント初期化（通知せず記録のみ）
python watcher.py --test   # Chatworkへ接続テスト送信
```

## 運用メモ

- 通知先の部屋やメンション相手を変えたいとき → `config.json` の `chatwork` を編集
- 稼働時間を変えたいとき → `config.json` の `active_hours`（watch.yml の cron も合わせると綺麗）
- APIバージョン（`2026-01`）はShopifyが約1年でサポート終了する。通知が止まってActionsログに
  APIエラーが出ていたら `config.json` の `api_version` を新しい安定版に上げる
- Actionsログで `警告(一部フィールド取得不可)` が出る場合、購入者名などが取れていないだけで
  通知自体は動く（スコープ/保護データ設定を確認）
