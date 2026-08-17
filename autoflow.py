#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shopify-order-autoflow
新規の「支払い済み・未発送」注文を検知したら:
  1. 明細表(受注表)PDFを自動生成
  2. Chatwork「North Edge 発送データ共有」(家族ルーム)へ投稿(奥さん印刷用)
  3. 草平さんへ送り状購入のメンション通知
送り状の購入・印刷は自動化しない(購入=課金のため人間が実行)。

watcher.py と同じ差分検知方式(autoflow_state.json)。何度動いても重複投稿しない。

使い方:
  python autoflow.py           通常実行
  python autoflow.py --init    現状の注文を処理済みとして記録するだけ(投稿しない)
  python autoflow.py --dry     PDF生成のみ(Chatwork投稿しない)。出力は ./out/
"""
import io
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOCAL_CONFIG_PATH = os.path.join(BASE_DIR, "config.local.json")
STATE_PATH = os.path.join(BASE_DIR, "autoflow_state.json")
FONT_PATH = os.path.join(BASE_DIR, "fonts", "NotoSansJP.ttf")
CHATWORK_API = "https://api.chatwork.com/v2"
JST = timezone(timedelta(hours=9))

PREF_JA = {
    "Hokkaido": "北海道", "Hokkaidō": "北海道", "Aomori": "青森県", "Iwate": "岩手県",
    "Miyagi": "宮城県", "Akita": "秋田県", "Yamagata": "山形県", "Fukushima": "福島県",
    "Ibaraki": "茨城県", "Tochigi": "栃木県", "Gunma": "群馬県", "Saitama": "埼玉県",
    "Chiba": "千葉県", "Tokyo": "東京都", "Tōkyō": "東京都", "Kanagawa": "神奈川県",
    "Niigata": "新潟県", "Toyama": "富山県", "Ishikawa": "石川県", "Fukui": "福井県",
    "Yamanashi": "山梨県", "Nagano": "長野県", "Gifu": "岐阜県", "Shizuoka": "静岡県",
    "Aichi": "愛知県", "Mie": "三重県", "Shiga": "滋賀県", "Kyoto": "京都府", "Kyōto": "京都府",
    "Osaka": "大阪府", "Ōsaka": "大阪府", "Hyogo": "兵庫県", "Hyōgo": "兵庫県",
    "Nara": "奈良県", "Wakayama": "和歌山県", "Tottori": "鳥取県", "Shimane": "島根県",
    "Okayama": "岡山県", "Hiroshima": "広島県", "Yamaguchi": "山口県", "Tokushima": "徳島県",
    "Kagawa": "香川県", "Ehime": "愛媛県", "Kochi": "高知県", "Kōchi": "高知県",
    "Fukuoka": "福岡県", "Saga": "佐賀県", "Nagasaki": "長崎県", "Kumamoto": "熊本県",
    "Oita": "大分県", "Ōita": "大分県", "Miyazaki": "宮崎県", "Kagoshima": "鹿児島県",
    "Okinawa": "沖縄県",
}

ORDERS_QUERY = """
{
  orders(first: 10, sortKey: CREATED_AT, reverse: true,
         query: "%QUERY%") {
    nodes {
      id name createdAt
      taxesIncluded
      totalPriceSet { shopMoney { amount } }
      subtotalPriceSet { shopMoney { amount } }
      totalShippingPriceSet { shopMoney { amount } }
      totalTaxSet { shopMoney { amount } }
      shippingAddress { name company address1 address2 city province zip phone }
      billingAddress { name company address1 address2 city province zip phone }
      lineItems(first: 20) { nodes { title variantTitle quantity
        originalUnitPriceSet { shopMoney { amount } }
        discountedTotalSet { shopMoney { amount } } } }
    }
  }
}
"""


def log(msg):
    print(f"[{datetime.now(JST).strftime('%H:%M:%S')}] {msg}", flush=True)


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


def get_env(name):
    v = os.environ.get(name)
    if v:
        return v
    local = load_json(LOCAL_CONFIG_PATH, {})
    return local.get(name)


def get_shopify_token(cfg):
    direct = get_env("SHOPIFY_TOKEN")
    if direct:
        return direct
    client_id = get_env("SHOPIFY_CLIENT_ID")
    client_secret = get_env("SHOPIFY_CLIENT_SECRET")
    if not (client_id and client_secret):
        log("SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET がありません。")
        sys.exit(1)
    url = f"https://{cfg['shopify']['shop_domain']}/admin/oauth/access_token"
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["access_token"]


def fetch_open_orders(cfg, token, search=None):
    """search=None なら「支払い済み・未発送」。注文番号指定なら name:#1106 等"""
    q = search or "financial_status:paid fulfillment_status:unshipped status:open"
    sp = cfg["shopify"]
    url = f"https://{sp['shop_domain']}/admin/api/{sp['api_version']}/graphql.json"
    req = urllib.request.Request(
        url,
        data=json.dumps({"query": ORDERS_QUERY.replace("%QUERY%", q)}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    if data.get("errors") and not data.get("data"):
        raise RuntimeError(f"GraphQLエラー: {data['errors']}")
    return data["data"]["orders"]["nodes"]


# ---------- 明細表PDF生成 (A4 300dpi) ----------

W, H = 2480, 3508
MARGIN = 210


def yen(amount_str):
    n = int(float(amount_str))
    return f"¥{n:,}"


def address_lines(a):
    """Shopify住所→明細表用の行リスト"""
    if not a:
        return []
    pref = PREF_JA.get((a.get("province") or "").strip(), a.get("province") or "")
    lines = [f'{a.get("name", "")} 様']
    if a.get("company"):
        lines.append(a["company"])
    addr = f'{a.get("address1", "") or ""}'
    if a.get("address2"):
        addr += f' {a["address2"]}'
    lines.append(addr)
    lines.append(f'〒{a.get("zip", "")} {pref} {a.get("city", "")}')
    lines.append("日本")
    if a.get("phone"):
        lines.append(a["phone"])
    return lines


def render_packing_slip(order):
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    def font(size, weight=500):
        f = ImageFont.truetype(FONT_PATH, size)
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
        return f

    f_title = font(96, 600)
    f_h = font(44, 700)
    f_b = font(40, 500)
    f_s = font(36, 500)

    black, gray = (0, 0, 0), (70, 70, 70)

    # header
    d.text((MARGIN, 260), "NORTH EDGE STANDARD", font=f_title, fill=black)
    created = datetime.fromisoformat(order["createdAt"].replace("Z", "+00:00")).astimezone(JST)
    d.text((W - MARGIN, 280), f"注文{order['name']}", font=f_h, fill=black, anchor="ra")
    d.text((W - MARGIN, 345), created.strftime("%Y年%-m月%-d日") if os.name != "nt"
           else created.strftime("%Y年%m月%d日"), font=f_b, fill=gray, anchor="ra")

    # addresses
    y0 = 560
    d.text((MARGIN, y0), "配送先", font=f_h, fill=black)
    d.text((W // 2 + 60, y0), "請求先", font=f_h, fill=black)
    ship_lines = address_lines(order.get("shippingAddress"))
    bill = order.get("billingAddress")
    same = bill == order.get("shippingAddress")
    bill_lines = ["配送先住所と同じ"] if same else address_lines(bill)
    y = y0 + 90
    for i, line in enumerate(ship_lines):
        d.text((MARGIN, y + i * 62), line, font=f_b, fill=black)
    for i, line in enumerate(bill_lines):
        d.text((W // 2 + 60, y + i * 62), line, font=f_b, fill=black)
    y = y + max(len(ship_lines), len(bill_lines)) * 62 + 90

    # table header
    d.line([(MARGIN, y), (W - MARGIN, y)], fill=black, width=6)
    y += 50
    col_qty, col_price, col_amt = W - MARGIN - 900, W - MARGIN - 500, W - MARGIN
    d.text((MARGIN + 120, y), "アイテム / ITEM", font=f_s, fill=black)
    d.text((col_qty, y), "数量 / QTY", font=f_s, fill=black, anchor="ra")
    d.text((col_price, y), "単価 / PRICE", font=f_s, fill=black, anchor="ra")
    d.text((col_amt, y), "金額 / AMOUNT", font=f_s, fill=black, anchor="ra")
    y += 100

    item_x = MARGIN + 120
    item_w = col_qty - item_x - 120  # 数量列に食い込まないための折り返し幅

    def wrap(text, font):
        """日本語は単語境界がないので1文字ずつ幅を測って折り返す"""
        lines, cur = [], ""
        for ch in text:
            if d.textlength(cur + ch, font=font) > item_w and cur:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        return lines

    for it in order["lineItems"]["nodes"]:
        lines = wrap(it["title"], f_b)
        if it.get("variantTitle"):
            lines.append(it["variantTitle"])
        d.multiline_text((item_x, y), "\n".join(lines), font=f_b, fill=black, spacing=18)
        d.text((col_qty, y), f'{it["quantity"]}個', font=f_b, fill=black, anchor="ra")
        d.text((col_price, y), yen(it["originalUnitPriceSet"]["shopMoney"]["amount"]),
               font=f_b, fill=black, anchor="ra")
        d.text((col_amt, y), yen(it["discountedTotalSet"]["shopMoney"]["amount"]),
               font=f_b, fill=black, anchor="ra")
        y += max(2, len(lines)) * 62 + 46

    y += 30
    d.line([(MARGIN, y), (W - MARGIN, y)], fill=black, width=6)
    y += 70

    # totals
    label_x, val_x = W - MARGIN - 700, W - MARGIN
    rows = [
        ("小計 / Subtotal", yen(order["subtotalPriceSet"]["shopMoney"]["amount"]), f_b),
        ("送料 / Shipping", yen(order["totalShippingPriceSet"]["shopMoney"]["amount"]), f_b),
    ]
    for label, val, font in rows:
        d.text((label_x, y), label, font=font, fill=black, anchor="ra")
        d.text((val_x, y), val, font=font, fill=black, anchor="ra")
        y += 78
    y += 10
    d.line([(label_x - 260, y), (W - MARGIN, y)], fill=black, width=4)
    y += 40
    d.text((label_x, y), "合計 / Total", font=f_h, fill=black, anchor="ra")
    d.text((val_x, y), yen(order["totalPriceSet"]["shopMoney"]["amount"]), font=f_h,
           fill=black, anchor="ra")
    y += 90
    if order.get("taxesIncluded"):
        d.text((val_x, y), f'(内税 {yen(order["totalTaxSet"]["shopMoney"]["amount"])})',
               font=f_s, fill=gray, anchor="ra")

    # footer
    fy = H - 640
    d.text((W // 2, fy), "ご利用ありがとうございました。", font=f_b, fill=black, anchor="ma")
    footer = [
        "NORTH EDGE STANDARD",
        "247-0028, 神奈川県 横浜市栄区 亀井町7-8, ディアコートB202, 日本",
        "northedge.standard@gmail.com",
        "shop.northedge-standard.com",
    ]
    for i, line in enumerate(footer):
        d.text((W // 2, fy + 140 + i * 64), line, font=f_s, fill=gray, anchor="ma")

    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=300.0)
    return buf.getvalue()


# ---------- Chatwork ----------

def chatwork_upload(room_id, token, filename, content, message):
    boundary = "----autoflow-boundary"
    body = io.BytesIO()

    def w(s):
        body.write(s if isinstance(s, bytes) else s.encode("utf-8"))

    w(f"--{boundary}\r\n")
    w(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n')
    w("Content-Type: application/pdf\r\n\r\n")
    w(content)
    w("\r\n")
    w(f"--{boundary}\r\n")
    w('Content-Disposition: form-data; name="message"\r\n\r\n')
    w(message)
    w(f"\r\n--{boundary}--\r\n")
    req = urllib.request.Request(
        f"{CHATWORK_API}/rooms/{room_id}/files",
        data=body.getvalue(),
        headers={
            "X-ChatWorkToken": token,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        })
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def chatwork_post(room_id, body_text, token):
    data = urllib.parse.urlencode({"body": body_text}).encode()
    req = urllib.request.Request(
        f"{CHATWORK_API}/rooms/{room_id}/messages",
        data=data, headers={"X-ChatWorkToken": token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        log("config.json がありません。")
        sys.exit(1)
    af = cfg.get("autoflow", {})
    family_room = af.get("family_room_id")
    mention_id = af.get("mention_account_id")
    if not family_room:
        log("config.json に autoflow.family_room_id がありません。")
        sys.exit(1)

    init_only = "--init" in sys.argv
    dry = "--dry" in sys.argv
    # --order 1106 1107 … 発送済みでも指定の注文を出し直す(差分検知を無視)
    redo = []
    if "--order" in sys.argv:
        redo = [a.lstrip("#") for a in sys.argv[sys.argv.index("--order") + 1:]
                if not a.startswith("--")]

    chatwork_token = get_env("CHATWORK_TOKEN")
    if not dry and not chatwork_token:
        log("CHATWORK_TOKEN がありません。")
        sys.exit(1)

    token = get_shopify_token(cfg)
    search = " OR ".join(f"name:#{n}" for n in redo) if redo else None
    orders = fetch_open_orders(cfg, token, search)
    state = load_json(STATE_PATH, {"processed": []})
    processed = set(state["processed"])

    new_orders = orders if (dry or redo) else [o for o in orders if o["id"] not in processed]
    if init_only:
        state["processed"] = sorted(processed | {o["id"] for o in orders})
        save_json(STATE_PATH, state)
        log(f"初期化: {len(new_orders)}件を処理済みとして記録(投稿なし)。")
        return

    if not new_orders:
        log("新規の支払い済み・未発送注文はありません。")
        return

    for o in reversed(new_orders):  # 古い順に処理
        name = o["name"]
        ship = o.get("shippingAddress") or {}
        cust = ship.get("name", "お客様")
        items = ", ".join(
            f'{i["title"].replace("NES-FLAT MAGIC SHOOTING LINE", "シューティングライン")}'
            f'({i.get("variantTitle") or ""})×{i["quantity"]}'
            for i in o["lineItems"]["nodes"])
        total = yen(o["totalPriceSet"]["shopMoney"]["amount"])
        log(f"{name} {cust}様 {items} {total} を処理中…")
        pdf = render_packing_slip(o)

        if dry:
            outdir = os.path.join(BASE_DIR, "out")
            os.makedirs(outdir, exist_ok=True)
            path = os.path.join(outdir, f"明細表_{name.lstrip('#')}.pdf")
            with open(path, "wb") as f:
                f.write(pdf)
            log(f"  --dry: {path} に保存(投稿なし)")
            continue

        msg = (f"[info][title]{name} {cust}様の明細表（自動送信）[/title]"
               f"ご注文: {items}　合計{total}\n"
               "印刷: A4普通紙・前のカセット。ファイルを開いて「プリント」でOKです\n"
               "送り状はこのあと別途届きます[/info]")
        chatwork_upload(family_room, chatwork_token, f"明細表_{name.lstrip('#')}.pdf", pdf, msg)
        if mention_id:
            chatwork_post(
                family_room,
                f"[To:{mention_id}] 新規注文 {name} {cust}様（{items}・{total}）\n"
                "明細表は自動投稿済み。送り状の購入をお願いします→ "
                "https://admin.shopify.com/store/cvz1fy-qm/apps/shippingplus/shippings\n"
                "購入できたらClaudeに「送り状回収して」でPDF化できます",
                chatwork_token)
        processed.add(o["id"])
        state["processed"] = sorted(processed)
        save_json(STATE_PATH, state)
        log(f"  投稿完了: {name}")


if __name__ == "__main__":
    main()
