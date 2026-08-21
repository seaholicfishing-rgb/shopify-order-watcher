#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chatworkへファイル/メッセージを投稿する。
シェル経由だと日本語が文字化けするので、必ずこのスクリプト経由で送ること。
  python post_chat.py file <パス> <メッセージ>
  python post_chat.py msg <メッセージ>
  python post_chat.py del <message_id>
"""
import io, json, os, sys, urllib.parse, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
ROOM = "442712906"  # North Edge 発送データ共有
API = "https://api.chatwork.com/v2"


def token():
    v = os.environ.get("CHATWORK_TOKEN")
    if v:
        return v
    with open(os.path.join(BASE, "config.local.json"), encoding="utf-8") as f:
        return json.load(f)["CHATWORK_TOKEN"]


def upload(path, message):
    boundary = "----post-chat-boundary"
    name = os.path.basename(path)
    body = io.BytesIO()

    def w(s):
        body.write(s if isinstance(s, bytes) else s.encode("utf-8"))

    w(f"--{boundary}\r\n")
    w(f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n')
    w("Content-Type: application/pdf\r\n\r\n")
    with open(path, "rb") as f:
        w(f.read())
    w(f"\r\n--{boundary}\r\n")
    w('Content-Disposition: form-data; name="message"\r\n\r\n')
    w(message)
    w(f"\r\n--{boundary}--\r\n")
    req = urllib.request.Request(
        f"{API}/rooms/{ROOM}/files", data=body.getvalue(),
        headers={"X-ChatWorkToken": token(),
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def post(message):
    req = urllib.request.Request(
        f"{API}/rooms/{ROOM}/messages",
        data=urllib.parse.urlencode({"body": message}).encode("utf-8"),
        headers={"X-ChatWorkToken": token()})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def delete(mid):
    req = urllib.request.Request(
        f"{API}/rooms/{ROOM}/messages/{mid}",
        headers={"X-ChatWorkToken": token()}, method="DELETE")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1]

    def arg_text(a):
        """シェル経由の引数は文字化けするので @ファイル指定を推奨"""
        if a.startswith("@"):
            with open(a[1:], encoding="utf-8") as f:
                return f.read().strip()
        return a

    if cmd == "file":
        print(upload(sys.argv[2], arg_text(sys.argv[3])))
    elif cmd == "msg":
        print(post(arg_text(sys.argv[2])))
    elif cmd == "del":
        print(delete(sys.argv[2]))
