#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""送り状PDF(S3の署名付きURL)を取得し、どこでも印刷できる画像PDFに変換する。
  python label_fetch.py <名前(例 1117 / 1118_竿)> <URL>
保存先: ~/Downloads/<注文番号>_発送セット/送り状_<名前>.pdf
ヤマトB2のPDFはAdobe以外だと白紙になるため、300dpiで画像化して作り直す。
URLは curl で取る(PowerShellのInvoke-WebRequestは // を潰して署名エラーになる)。"""
import os, subprocess, sys
import pypdfium2 as pdfium

name, url = sys.argv[1], sys.argv[2]
order = name.split("_")[0]
d = os.path.join(os.path.expanduser("~"), "Downloads", f"{order}_発送セット")
os.makedirs(d, exist_ok=True)
raw = os.path.join(d, f"送り状_{name}_元データ.pdf")
out = os.path.join(d, f"送り状_{name}.pdf")
subprocess.run(["curl", "-sS", "-o", raw, url], check=True)
if open(raw, "rb").read(5) != b"%PDF-":
    sys.exit("PDFではない(URL期限切れの可能性): " + open(raw, "rb").read(200).decode("utf-8", "replace"))
imgs = [p.render(scale=300 / 72).to_pil().convert("RGB") for p in pdfium.PdfDocument(raw)]
imgs[0].save(out, "PDF", resolution=300.0, save_all=True, append_images=imgs[1:])
chk = os.path.join(d, f"_check_{name}.png")
imgs[0].resize((imgs[0].width // 3, imgs[0].height // 3)).crop((0, 0, imgs[0].width // 3, 600)).save(chk)
open(os.path.join(d, "_last.txt"), "w", encoding="utf-8").write(out + "\n" + chk)
print("pages", len(imgs), os.path.getsize(out))
