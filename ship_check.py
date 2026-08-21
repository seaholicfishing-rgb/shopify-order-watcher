#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""発送番モード: 新規注文を判定して「自動購入していいか」を返す。
実際のブラウザ操作(購入)はClaudeが行う。ここは判定と下ごしらえに専念する。

  python ship_check.py          判定結果をJSONで出力
  python ship_check.py --mark <注文番号>   処理済みとして記録
"""
import json, os, re, sys, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "shipwatch_state.json")
JST = timezone(timedelta(hours=9))
SHOP = "cvz1fy-qm.myshopify.com"
VER = "2026-01"

# 自動購入してよい上限(税込)。これを超えたら人間に確認
AUTO_LIMIT = 1000
# 竿(長物)判定に使うキーワード
ROD_HINTS = ("8100", "ロッド", "ROD")

PREF = {"Hokkaidō": "北海道", "Tōkyō": "東京都", "Kyōto": "京都府", "Ōsaka": "大阪府",
        "Hyōgo": "兵庫県", "Kōchi": "高知県", "Ōita": "大分県"}

ORDERS_Q = """
{
  orders(first: 10, sortKey: CREATED_AT, reverse: true,
         query: "financial_status:paid fulfillment_status:unshipped status:open") {
    nodes {
      id name createdAt
      totalPriceSet { shopMoney { amount } }
      shippingAddress { name zip province city address1 address2 phone }
      lineItems(first: 20) { nodes { title variantTitle quantity } }
    }
  }
}
"""


def load(p, d):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return d


def save(p, d):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def token():
    cfg = load(os.path.join(BASE, "config.local.json"), {})
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": cfg["SHOPIFY_CLIENT_ID"],
        "client_secret": cfg["SHOPIFY_CLIENT_SECRET"]}).encode()
    req = urllib.request.Request(f"https://{SHOP}/admin/oauth/access_token", data=data)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())["access_token"]


def zipcloud(zipcode):
    """郵便番号から正式な市区町村・町域を引く"""
    z = re.sub(r"\D", "", zipcode or "")
    if len(z) != 7:
        return None
    try:
        url = f"https://zipcloud.ibsnet.co.jp/api/search?zipcode={z}"
        with urllib.request.urlopen(url, timeout=15) as r:
            res = json.loads(r.read())
        if res.get("results"):
            a = res["results"][0]
            return {"pref": a["address1"], "city": a["address2"], "town": a["address3"]}
    except Exception:
        pass
    return None


def _norm(t):
    """全角数字→半角、記号ゆれを吸収して比較用に正規化"""
    for a, b in zip("０１２３４５６７８９－ー―", "0123456789---"):
        t = t.replace(a, b)
    return t


def _strip_town(addr1, town):
    """住所欄から町域部分を取り除く(半角/全角の数字ゆれも吸収)"""
    t = town
    for a, b in zip("０１２３４５６７８９", "0123456789"):
        t = t.replace(a, b)
    a1 = addr1
    for cand in (town, t):
        if cand and a1.startswith(cand):
            return a1[len(cand):]
    return a1


def judge(o):
    """この注文を自動購入してよいか判定"""
    items = o["lineItems"]["nodes"]
    total_qty = sum(i["quantity"] for i in items)
    has_rod = any(any(h in (i["title"] or "") for h in ROD_HINTS) for i in items)
    a = o.get("shippingAddress") or {}

    reasons, action = [], "AUTO"

    if has_rod:
        action = "ASK"
        reasons.append("竿を含む(2個口・サイズ判断が必要)")
    if total_qty > 2:
        action = "ASK"
        reasons.append(f"点数が多い({total_qty}点)")

    # 住所チェック。実績上ヤマトが弾いたのは下記のみ:
    #  (a) 町域名にアラビア数字が入り、それが「住所」欄側にある (例:神楽5条)
    #      → ヤマトの番地パーサが丁目と誤認する。漢数字(ひじり野北一条)は通る
    #  (b) 連結順序が壊れている (例: city=東神楽町 / addr1=上川郡)
    #  (c) 住所の二重入力
    # ※最終判定はブラウザ側のエラー表示で行う。ここは事前の注意喚起。
    zc = zipcloud(a.get("zip"))
    city, addr1, addr2 = a.get("city") or "", a.get("address1") or "", a.get("address2") or ""
    joined = f"{city}{addr1}{addr2}"
    risk = None

    if addr1 and addr2 and addr1 in addr2:
        action = "ASK"
        reasons.append("住所が二重入力されている")

    if not zc:
        action = "ASK"
        reasons.append("郵便番号を照合できなかった")
    else:
        town = zc["town"] if zc["town"] and "以下に掲載" not in zc["town"] else ""
        want_city = zc["city"] + town
        if _norm(zc["city"]) not in _norm(joined):
            action = "ASK"
            reasons.append(f"住所の並びが郵便番号と合わない(期待:{zc['city']})")
            risk = {"市区町村": want_city, "住所": _strip_town(addr1, town)}
        elif town and re.search(r"[0-9０-９]", town) and _norm(town) not in _norm(city):
            action = "ASK"
            reasons.append(f"町域「{town}」に数字が入る(ヤマトが弾く既知パターン)")
            risk = {"市区町村": want_city, "住所": _strip_town(addr1, town)}

    dt = datetime.fromisoformat(o["createdAt"].replace("Z", "+00:00")).astimezone(JST)
    return {
        "注文": o["name"], "id": o["id"],
        "日時": dt.strftime("%m/%d %H:%M"),
        "顧客": a.get("name", ""),
        "宛先": f'〒{a.get("zip","")} {PREF.get((a.get("province") or "").strip(), a.get("province") or "")}{a.get("city","")}{a.get("address1","")} {a.get("address2") or ""}'.strip(),
        "電話": a.get("phone", ""),
        "商品": [f'{i["title"]}({i.get("variantTitle") or ""})×{i["quantity"]}' for i in items],
        "点数": total_qty,
        "注文額": int(float(o["totalPriceSet"]["shopMoney"]["amount"])),
        "判定": action,
        "理由": reasons,
        "住所修正案": risk,
        "想定送料": "コンパクト ¥650〜825" if action == "AUTO" else "要判断",
        "上限": AUTO_LIMIT,
    }


def main():
    st = load(STATE, {"done": []})
    if "--mark" in sys.argv:
        for n in sys.argv[sys.argv.index("--mark") + 1:]:
            if not n.startswith("--") and n not in st["done"]:
                st["done"].append(n)
        save(STATE, st)
        print(json.dumps({"marked": st["done"][-5:]}, ensure_ascii=False))
        return

    tok = token()
    req = urllib.request.Request(
        f"https://{SHOP}/admin/api/{VER}/graphql.json",
        data=json.dumps({"query": ORDERS_Q}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Shopify-Access-Token": tok})
    res = json.loads(urllib.request.urlopen(req, timeout=30).read())
    orders = res["data"]["orders"]["nodes"]

    todo = [judge(o) for o in orders if o["name"] not in st["done"]]
    out = {
        "確認時刻": datetime.now(JST).strftime("%m/%d %H:%M"),
        "新規": len(todo),
        "自動購入OK": [t for t in todo if t["判定"] == "AUTO"],
        "要確認": [t for t in todo if t["判定"] == "ASK"],
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
