# -*- coding: utf-8 -*-
# 撕紙版型的接縫：一條近直線的撕紙白邊（纖維感），剛好蓋住上下兩段影片的交接處
# 上下影片都是矩形，不用照弧度裁，換影片可以直接在 CapCut 替換

import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 1080, 1920
SEAM_Y = 1000          # 接縫中心（畫布像素）
WAVE = 10              # 線的起伏幅度（接近直線）
FIBER = (16, 34)       # 纖維帶厚度範圍（上緣）
OVERLAP = 60           # 上下影片在接縫處重疊的像素（確保不露縫）


def seam_box_top():
    return [0, 0, W, SEAM_Y + OVERLAP // 2]


def seam_box_bottom():
    return [0, SEAM_Y - OVERLAP // 2, W, H]


def _line(seed=7):
    rnd = random.Random(seed)
    xs = np.arange(W)
    # 幾個慢波＋細碎抖動，整體接近直線
    y = SEAM_Y + WAVE * np.sin(xs / 260 + 0.8) * 0.6 + WAVE * 0.4 * np.sin(xs / 90 + 2.1)
    jit = np.cumsum(np.array([rnd.uniform(-1, 1) for _ in xs])) * 0.25
    jit -= np.linspace(jit[0], jit[-1], W)
    return y + jit


def _ref_strip():
    """從 Vivian 給的撕紙參考圖（版型/撕紙邊.png，透明 PNG）把紙邊的纖維帶拉直成一條：回傳 RGBA 陣列 (STRIP_H, W_ref)"""
    import os, sys
    cands = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "版型", "撕紙邊.png")]
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cands.insert(0, os.path.join(base, "core", "版型", "撕紙邊.png"))
    src = next(c for c in cands if os.path.exists(c))
    a = np.asarray(Image.open(src).convert("RGBA")).astype(float)
    A, L = a[..., 3], a[..., :3].mean(-1)
    H, Wr = A.shape
    solid = (A > 200) & (L < 120)                    # 深色紙
    edge = np.full(Wr, H - 1)
    for x in range(Wr):
        c = solid[:, x]
        for i in range(H - 8):
            if c[i:i + 8].all():
                edge[x] = i
                break
    strip = np.zeros((STRIP_ABOVE + STRIP_BELOW, Wr, 4), dtype=float)
    for x in range(Wr):
        y0 = edge[x] - STRIP_ABOVE
        for k in range(STRIP_ABOVE + STRIP_BELOW):
            y = y0 + k
            if 0 <= y < H:
                strip[k, x] = a[y, x]
    # 深色紙的部分變透明（那裡是下方影片），只留淺色纖維
    dark = (strip[..., 3] > 0) & (strip[..., :3].mean(-1) < 125)
    strip[dark, 3] = 0
    return strip


STRIP_ABOVE, STRIP_BELOW = 72, 6


def draw(out_path, seed=7):
    strip = _ref_strip()
    sh, sw = strip.shape[:2]
    # 寬度拉到畫布寬（1280 → 1080），纖維粗細照原圖
    band = Image.fromarray(strip.astype("uint8"), "RGBA").resize((W, sh), Image.LANCZOS)
    band = np.asarray(band).astype(float)
    y = _line(seed)
    im = np.zeros((H, W, 4), dtype=float)
    for x in range(W):
        top = int(round(y[x])) - STRIP_ABOVE
        for k in range(sh):
            yy = top + k
            if 0 <= yy < H:
                im[yy, x] = band[k, x]
    out = Image.fromarray(im.astype("uint8"), "RGBA")
    # 線下方一道淡陰影，讓上層紙有厚度
    shd = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shd)
    for x in range(W):
        sd.line([(x, int(y[x]) + 1), (x, int(y[x]) + 7)], fill=(0, 0, 0, 70))
    shd = shd.filter(ImageFilter.GaussianBlur(2.5))
    Image.alpha_composite(shd, out).save(out_path)
    return out_path


if __name__ == "__main__":
    import sys
    print(draw(sys.argv[1] if len(sys.argv) > 1 else "torn_overlay.png"))
