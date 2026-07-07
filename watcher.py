#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shopify-order-watcher
Shopify(North Edge Standard)の新規注文を検知してChatworkへ通知する。

film-camera-watcher と同じ差分検知方式:
  通知済みの注文IDを state.json に保存し、新規IDだけ通知する。
  何度動いても重複通知は出ない。稼働時間外の注文も翌朝の実行で拾う(取りこぼし無し)。

使い方:
  python watcher.py          通常実行(新規注文があれば通知)
  python watcher.py --init   サイレント初期化(現状の注文を記録するだけで通知しない)
  python watcher.py --test   Chatworkへ接続テストメッセージを送る
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
CHATWORK_API = "https://api.chatwork.com/v2"
JST = timezone(timedelta(hours=9))

# 直近10件を新しい順に取得すれば、15分間隔の巡回で取りこぼす心配はない
ORDERS_QUERY = """
{
  orders(first: 10, sortKey: CREATED_AT, reverse: true) {
    edges {
      node {
        id
        name
        createdAt
        displayFinancialStatus
        totalPriceSet { shopMoney { amount currencyCode } }
        customer { displayName }
        shippingAddress { province city }
        lineItems(first: 20) { edges { node { title quantity } } }
      }
    }
  }
}
"""

FINANCIAL_STATUS_JA = {
    "PAID": "支払い済み",
    "PENDING": "支払い待ち",
    "AUTHORIZED": "オーソリ済み(未確定)",
    "PARTIALLY_PAID": "一部支払い済み",
    "REFUNDED": "返金済み",
    "PARTIALLY_REFUNDED": "一部返金済み",
    "VOIDED": "無効",
    "EXPIRED": "期限切れ",
}


def log(msg):
    print(f"[{jst_now().strftime('%H:%M:%S')}] {msg}", flush=True)


def jst_now():
    return datetime.now(JST)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fetch_orders(cfg, token):
    sp = cfg["shopify"]
    url = f"https://{sp['shop_domain']}/admin/api/{sp['api_version']}/graphql.json"
    req = urllib.request.Request(
        url,
        data=json.dumps({"query": ORDERS_QUERY}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": token,
        })
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    if data.get("errors"):
        if not data.get("data"):
            raise RuntimeError(f"Shopify APIエラー: {data['errors']}")
        # 一部フィールドだけ権限不足などの場合は警告して続行(nullで返る)
        log(f"警告(一部フィールド取得不可): {data['errors']}")
    edges = (((data.get("data") or {}).get("orders") or {}).get("edges")) or []
    return [e["node"] for e in edges]


def money(price_set):
    shop_money = (price_set or {}).get("shopMoney") or {}
    amount = shop_money.get("amount")
    if amount is None:
        return "不明"
    currency = shop_money.get("currencyCode", "")
    if currency == "JPY":
        return f"¥{int(float(amount)):,}"
    return f"{amount} {currency}"


def order_admin_url(cfg, order):
    # GraphQLのID "gid://shopify/Order/123456" から数値IDを取り出す
    num = order["id"].rsplit("/", 1)[-1]
    return f"https://admin.shopify.com/store/{cfg['shopify']['admin_store_handle']}/orders/{num}"


def build_message(order, cfg):
    lines = []
    mention_ids = cfg["chatwork"].get("mention_account_ids") or []
    if mention_ids:
        # 自分宛てメンションを付けると Chatwork が通知音/バッジを鳴らす
        lines.append("".join(f"[To:{m}]" for m in mention_ids))

    shop_label = cfg["shopify"].get("shop_label", "Shopify")
    lines.append(f"[info][title]🎉 ご注文が入りました！ {order['name']} / {shop_label}[/title]")

    for edge in ((order.get("lineItems") or {}).get("edges")) or []:
        item = edge["node"]
        lines.append(f"・{item['title']} ×{item['quantity']}")

    lines.append(f"合計: {money(order.get('totalPriceSet'))}")

    status = order.get("displayFinancialStatus")
    if status:
        lines.append(f"支払い: {FINANCIAL_STATUS_JA.get(status, status)}")

    customer = order.get("customer") or {}
    if customer.get("displayName"):
        addr = order.get("shippingAddress") or {}
        place = "・".join(p for p in [addr.get("province"), addr.get("city")] if p)
        place_part = f"（{place}）" if place else ""
        lines.append(f"購入者: {customer['displayName']} 様{place_part}")

    created = order.get("createdAt")
    if created:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(JST)
        lines.append(f"日時: {dt.strftime('%Y-%m-%d %H:%M')}")

    lines.append("▼ 管理画面で注文を確認")
    lines.append(order_admin_url(cfg, order))
    lines.append("[/info]")
    return "\n".join(lines)


def chatwork_post(room_id, body, token):
    data = urllib.parse.urlencode({"body": body}).encode()
    req = urllib.request.Request(
        f"{CHATWORK_API}/rooms/{room_id}/messages",
        data=data, headers={"X-ChatWorkToken": token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    init_mode = "--init" in sys.argv
    test_mode = "--test" in sys.argv

    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        log("config.json が読めません。終了します。")
        sys.exit(1)

    chatwork_token = os.environ.get("CHATWORK_TOKEN")
    room_id = str(cfg["chatwork"]["room_id"])

    if test_mode:
        if not chatwork_token:
            log("環境変数 CHATWORK_TOKEN がありません。")
            sys.exit(1)
        chatwork_post(room_id, "🛒 shopify-order-watcher 接続テスト（このメッセージは削除してOK）", chatwork_token)
        log("接続テスト送信OK")
        return

    shopify_token = os.environ.get("SHOPIFY_TOKEN")
    if not shopify_token:
        log("環境変数 SHOPIFY_TOKEN がありません。")
        sys.exit(1)

    # 稼働時間ガード（JST）。差分検知なので時間外の注文は翌朝まとめて通知される
    if not init_mode and os.environ.get("FORCE_RUN") != "1":
        ah = cfg.get("active_hours", {})
        hour = jst_now().hour
        if not (ah.get("start", 0) <= hour <= ah.get("end", 23)):
            log(f"稼働時間外（JST {hour}時）のため何もせず終了。")
            return

    state = load_json(STATE_PATH, {"seen_order_ids": []})
    seen_ids = state.get("seen_order_ids", [])
    seen = set(seen_ids)

    orders = fetch_orders(cfg, shopify_token)
    new_orders = [o for o in orders if o["id"] not in seen]
    new_orders.reverse()  # 古い順に通知(時系列)
    log(f"取得 {len(orders)}件 / 新規 {len(new_orders)}件")

    if init_mode:
        log(f"初期化モード: 既存 {len(new_orders)}件をサイレント記録（通知なし）")
    else:
        if new_orders and not chatwork_token:
            log("環境変数 CHATWORK_TOKEN がありません。")
            sys.exit(1)
        for order in new_orders:
            chatwork_post(room_id, build_message(order, cfg), chatwork_token)
            log(f"通知: {order['name']}")

    if new_orders:
        seen_ids += [o["id"] for o in new_orders]
        state["seen_order_ids"] = seen_ids[-500:]  # 古いIDは500件で切り捨て
        state["updated_at"] = jst_now().isoformat()
        save_json(STATE_PATH, state)
        log("state.json 更新")


if __name__ == "__main__":
    main()
