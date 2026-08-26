#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chatworkを指示の受け口として読む（Shopify発送用）。
草平さんが「送り状買って」等と書いたら拾って実行できるようにする。
家族ルームと注文通知ルームの両方を見るので、どちらに書いてもよい。

  python chat_commands.py                     未処理の指示をJSONで出力
  python chat_commands.py --done <msg_id> ..  実行済みとして記録
  python chat_commands.py --say @<本文ファイル> [--room <id>]  返信する
"""
import json, os, re, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "chatcmd_state.json")
JST = timezone(timedelta(hours=9))
API = "https://api.chatwork.com/v2"

CFG = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
FAMILY = str(CFG["autoflow"]["family_room_id"])        # 442712906 奥さんと共有
ORDERS = str(CFG["chatwork"]["room_id"])               # 441869132 注文通知
ROOMS = [FAMILY, ORDERS]
OWNER = str(CFG["autoflow"]["mention_account_id"])     # 草平さんの発言だけ指示にする

# 指示として認識する言い回し（上から順に判定）
PATTERNS = [
    (r"(送り状|ラベル).*(買|購入)", "BUY"),
    (r"発送(やって|して|お願い|よろ)", "BUY"),
    (r"(回収|取って|出して)", "COLLECT"),
    (r"(通知メール|発送メール).*(送|出)", "NOTIFY"),
    (r"(状況|進捗|どうなって)", "STATUS"),
    (r"(やめ|中止|キャンセル)", "CANCEL"),
]

KIND_DESC = {
    "BUY":     "新規注文の送り状を購入し、PDFを家族ルームへ投稿する",
    "COLLECT": "購入済みの送り状PDFを回収して家族ルームへ投稿する",
    "NOTIFY":  "発送完了メールをお客様へ送信する",
    "STATUS":  "未発送の一覧と進捗を返信する（購入はしない）",
    "CANCEL":  "進行中の作業を止めて状況を返信する",
}


def token():
    return json.load(open(os.path.join(BASE, "config.local.json"),
                          encoding="utf-8"))["CHATWORK_TOKEN"]


def load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"handled": []}


def save(d):
    json.dump(d, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def api(path, method="GET", data=None):
    req = urllib.request.Request(
        f"{API}{path}",
        data=urllib.parse.urlencode(data).encode() if data else None,
        headers={"X-ChatWorkToken": token()}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        if e.code == 204:      # 新着なし
            return []
        raise


def main():
    st = load()
    done = set(st.get("handled", []))

    if "--done" in sys.argv:
        for m in sys.argv[sys.argv.index("--done") + 1:]:
            if not m.startswith("--"):
                done.add(m)
        st["handled"] = sorted(done)[-300:]
        save(st)
        print(json.dumps({"recorded": len(done)}, ensure_ascii=False))
        return

    if "--say" in sys.argv:
        a = sys.argv[sys.argv.index("--say") + 1]
        text = open(a[1:], encoding="utf-8").read().strip() if a.startswith("@") else a
        room = FAMILY
        if "--room" in sys.argv:
            room = sys.argv[sys.argv.index("--room") + 1]
        print(json.dumps(api(f"/rooms/{room}/messages", "POST", {"body": text}),
                         ensure_ascii=False))
        return

    cmds = []
    for room in ROOMS:
        for m in api(f"/rooms/{room}/messages?force=1") or []:
            mid = str(m["message_id"])
            if mid in done or str(m["account"]["account_id"]) != OWNER:
                continue
            body = re.sub(r"\[[^\]]*\]", "", m["body"]).strip()   # [info]等を除去
            if not body:
                continue
            for pat, kind in PATTERNS:
                if re.search(pat, body):
                    cmds.append({
                        "message_id": mid, "room": room,
                        "時刻": datetime.fromtimestamp(m["send_time"], JST).strftime("%m/%d %H:%M"),
                        "本文": body[:200], "種別": kind, "内容": KIND_DESC[kind],
                    })
                    break

    cmds.sort(key=lambda c: c["時刻"])
    print(json.dumps({
        "確認時刻": datetime.now(JST).strftime("%m/%d %H:%M"),
        "未処理の指示": cmds,
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
