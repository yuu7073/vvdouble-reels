# -*- coding: utf-8 -*-
# 相機介面版型：用程式畫出乾淨的 overlay（不是從設計圖去背），字用真字型（Inter，與 iOS 介面同系）
# 畫布 1080×1920；上下遮罩半透明；左下相片格留給 CapCut 遮罩圖層放圖

import os
import sys

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
BAR = (89, 83, 69)          # 設計稿的深色條顏色
BAR_ALPHA = 0.60            # 上下遮罩透明度（iOS 相機介面實際約 55–65%）
YEL = (242, 201, 76)
WHITE = (255, 255, 255)
SS = 3                      # 超取樣倍率，讓線條與字邊緣乾淨

TOP_BAR = (0, 0, W, 181)
BOTTOM_BAR = (0, 1576, W, H)
CHEVRON = (540, 159, 31)
FOCUS = (522, 716, 722, 917)
SUN = (770, 818, 13)
PILL = (356, 1485, 721, 1562)
ZOOMS = [("0,5", 402, 24, False), ("1x", 496, 41, True), ("2", 585, 24, False), ("3", 675, 24, False)]
ZOOM_Y = 1522
MODES = [("CINEMATIC", 216), ("VIDEO", 379), ("PHOTO", 534), ("PORTRAIT", 721), ("PANO", 887)]
MODE_Y = 1669
SHUTTER = (540, 1804, 78, 67)
THUMB = (142, 1749, 252, 1859)
FLIP = (884, 1804, 54)


def _font(size, weight="SemiBold"):
    cands = []
    base = getattr(sys, "_MEIPASS", None)
    here = os.path.dirname(os.path.abspath(__file__))
    for d in ([os.path.join(base, "core", "fonts")] if base else []) + [os.path.join(here, "fonts")]:
        cands.append(os.path.join(d, f"Inter-{weight}.otf"))
    cands += [f"/usr/share/fonts/opentype/inter/Inter-{weight}.otf"]
    for c in cands:
        if os.path.exists(c):
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def draw(out_path):
    s = SS
    im = Image.new("RGBA", (W * s, H * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    a = int(255 * BAR_ALPHA)

    def R(*v):
        return tuple(int(round(x * s)) for x in v)

    # 上下半透明遮罩
    d.rectangle(R(*TOP_BAR), fill=BAR + (a,))
    d.rectangle(R(*BOTTOM_BAR), fill=BAR + (a,))

    # 三分構圖線
    for x in (360, 720):
        d.line([R(x, 181), R(x, 1576)], fill=(255, 255, 255, 90), width=max(1, s // 2))
    for y in (698, 1137):
        d.line([R(0, y), R(W, y)], fill=(255, 255, 255, 90), width=max(1, s // 2))

    # 對焦框＋四邊刻度
    x0, y0, x1, y1 = FOCUS
    d.rectangle(R(x0, y0, x1, y1), outline=YEL + (255,), width=2 * s)
    mx, my, t = (x0 + x1) / 2, (y0 + y1) / 2, 12
    for ln in ([(mx, y0), (mx, y0 + t)], [(mx, y1 - t), (mx, y1)], [(x0, my), (x0 + t, my)], [(x1 - t, my), (x1, my)]):
        d.line([R(*ln[0]), R(*ln[1])], fill=YEL + (255,), width=2 * s)

    # 太陽
    cx, cy, r = SUN
    d.ellipse(R(cx - r, cy - r, cx + r, cy + r), fill=YEL + (255,))
    import math
    for k in range(8):
        ang = k * math.pi / 4
        p0 = (cx + math.cos(ang) * (r + 7), cy + math.sin(ang) * (r + 7))
        p1 = (cx + math.cos(ang) * (r + 16), cy + math.sin(ang) * (r + 16))
        d.line([R(*p0), R(*p1)], fill=YEL + (255,), width=2 * s)

    # 焦段膠囊
    px0, py0, px1, py1 = PILL
    d.rounded_rectangle(R(px0, py0, px1, py1), radius=int((py1 - py0) / 2 * s), fill=(67, 63, 52, 220))
    for label, x, r, on in ZOOMS:
        d.ellipse(R(x - r, ZOOM_Y - r, x + r, ZOOM_Y + r), fill=(54, 50, 39, 255) if on else (54, 50, 39, 230))
        f = _font(int((30 if on else 22) * s), "SemiBold")
        d.text(R(x, ZOOM_Y), label, font=f, fill=(YEL if on else (215, 215, 215)) + (255,), anchor="mm")

    # 模式文字（獨立一層，兩側漸隱只影響文字）
    import numpy as np
    tl = Image.new("RGBA", im.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(tl)
    f = _font(int(27 * s), "SemiBold")
    for label, x in MODES:
        col = YEL if label == "PHOTO" else WHITE
        td.text(R(x, MODE_Y), label, font=f, fill=col + (255,), anchor="mm")
    xs = np.arange(W * s) / s
    fade = np.clip((xs - 100) / 110, 0, 1) * np.clip((W - 100 - xs) / 110, 0, 1)
    fade = 0.35 + 0.65 * fade
    ta = np.asarray(tl.getchannel("A")).astype(float) * fade[None, :]
    tl.putalpha(Image.fromarray(ta.astype("uint8")))
    im.alpha_composite(tl)
    d = ImageDraw.Draw(im)

    # 快門
    cx, cy, ro, ri = SHUTTER
    d.ellipse(R(cx - ro, cy - ro, cx + ro, cy + ro), outline=WHITE + (255,), width=4 * s)
    d.ellipse(R(cx - ri, cy - ri, cx + ri, cy + ri), fill=(216, 216, 216, 255))

    # 左下相片格（底色；上面會再放一層遮罩圖層讓使用者換圖）
    d.rounded_rectangle(R(*THUMB), radius=8 * s, fill=(40, 40, 52, 255))

    # 翻轉鏡頭
    cx, cy, r = FLIP
    d.ellipse(R(cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 64))
    ar = 22
    d.arc(R(cx - ar, cy - ar, cx + ar, cy + ar), start=200, end=340, fill=WHITE + (255,), width=3 * s)
    d.arc(R(cx - ar, cy - ar, cx + ar, cy + ar), start=20, end=160, fill=WHITE + (255,), width=3 * s)
    for sx, sy, ang in ((cx + ar * 0.94, cy - ar * 0.34, 1), (cx - ar * 0.94, cy + ar * 0.34, -1)):
        d.polygon([R(sx, sy + 9 * ang), R(sx - 8, sy - 5 * ang), R(sx + 8, sy - 5 * ang)], fill=WHITE + (255,))

    im = im.resize((W, H), Image.LANCZOS)
    im.save(out_path)
    return out_path


if __name__ == "__main__":
    print(draw(sys.argv[1] if len(sys.argv) > 1 else "camera_overlay.png"))
