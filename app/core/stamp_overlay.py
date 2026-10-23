# -*- coding: utf-8 -*-
# 郵票版型：用程式畫仿真的郵票框（鋸齒孔、紙張質感、微微傾斜）＋金屬迴紋針，黑底
# 郵票內部透明，影片放在下面一層；interior_box() 給引擎當影片的框位

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 1080, 1920
SS = 3                         # 超取樣
CX, CY = 545, 985              # 郵票中心
SW, SH = 860, 1400             # 郵票外框尺寸（含鋸齒）
BORDER = 42                    # 白邊寬
ANGLE = -1.5                   # 傾斜角度（度）
PERF_R, PERF_PITCH = 13, 34    # 鋸齒孔半徑、間距
PAPER = (245, 244, 240)
BG = (12, 12, 12)


def interior_box():
    """影片框位（畫布像素，軸對齊的外接矩形，略縮一點讓邊緣壓在白邊下）"""
    iw, ih = SW - 2 * BORDER, SH - 2 * BORDER
    a = math.radians(abs(ANGLE))
    bw = iw * math.cos(a) + ih * math.sin(a)
    bh = ih * math.cos(a) + iw * math.sin(a)
    return [int(CX - bw / 2) + 6, int(CY - bh / 2) + 6, int(CX + bw / 2) - 6, int(CY + bh / 2) - 6]


def _rot(points, cx, cy, deg):
    a = math.radians(deg)
    out = []
    for x, y in points:
        dx, dy = x - cx, y - cy
        out.append((cx + dx * math.cos(a) - dy * math.sin(a), cy + dx * math.sin(a) + dy * math.cos(a)))
    return out


def _stamp_layer(s):
    """回傳 (紙層 RGBA, 內部透明遮罩 L)，皆為超取樣尺寸"""
    size = (W * s, H * s)
    paper = Image.new("L", size, 0)
    d = ImageDraw.Draw(paper)
    x0, y0, x1, y1 = CX - SW / 2, CY - SH / 2, CX + SW / 2, CY + SH / 2
    # 紙本體（未旋轉，先畫在正的座標，最後整層旋轉）
    d.rectangle([x0 * s, y0 * s, x1 * s, y1 * s], fill=255)
    # 鋸齒孔：沿四邊打半圓洞
    r = PERF_R * s
    n_w = int(round(SW / PERF_PITCH))
    n_h = int(round(SH / PERF_PITCH))
    for i in range(n_w + 1):
        x = x0 + i * SW / n_w
        for y in (y0, y1):
            d.ellipse([x * s - r, y * s - r, x * s + r, y * s + r], fill=0)
    for j in range(n_h + 1):
        y = y0 + j * SH / n_h
        for x in (x0, x1):
            d.ellipse([x * s - r, y * s - r, x * s + r, y * s + r], fill=0)
    # 內部挖空
    hole = Image.new("L", size, 0)
    hd = ImageDraw.Draw(hole)
    hd.rectangle([(x0 + BORDER) * s, (y0 + BORDER) * s, (x1 - BORDER) * s, (y1 - BORDER) * s], fill=255)
    paper = Image.fromarray(np.where(np.asarray(hole) > 0, 0, np.asarray(paper)).astype("uint8"))
    # 旋轉
    paper = paper.rotate(-ANGLE, resample=Image.BICUBIC, center=(CX * s, CY * s))
    hole = hole.rotate(-ANGLE, resample=Image.BICUBIC, center=(CX * s, CY * s))
    # 紙張質感：細微顆粒＋輕微暗角，邊緣內側一點點陰影
    rs = np.random.RandomState(3)
    noise = rs.normal(0, 4, (H * s, W * s))
    pa = np.asarray(paper).astype(float)
    rgb = np.zeros((H * s, W * s, 3), dtype=float)
    for i, c in enumerate(PAPER):
        rgb[..., i] = c + noise
    # 孔洞內側壓一點暗（紙的厚度感）
    edge = paper.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(2 * s))
    inner = np.clip((255 - np.asarray(edge).astype(float)) / 255, 0, 1)
    rgb -= inner[..., None] * 18
    # 內框（挖空）邊緣：內側一圈淡陰影，讓影片像壓在紙下面
    hole_blur = np.asarray(hole.filter(ImageFilter.GaussianBlur(3 * s))).astype(float) / 255
    rgb -= (hole_blur * (pa > 0))[..., None] * 26
    layer = np.dstack([np.clip(rgb, 0, 255), pa]).astype("uint8")
    return Image.fromarray(layer, "RGBA"), hole


def _paperclip(s):
    """金屬迴紋針（RGBA 超取樣尺寸），含在紙上的陰影"""
    size = (W * s, H * s)
    clip = Image.new("RGBA", size, (0, 0, 0, 0))
    cx, top, bottom = 300, 215, 560
    w_out, w_in = 72, 44
    r_out, r_in = w_out / 2, w_in / 2
    # 迴紋針路徑：外圈 U + 內圈 U，用圓弧與直線組成
    def u_path(cx, top, bottom, half, flip):
        # 回傳折線點（由上到下再回來），flip 決定開口方向
        pts = []
        steps = 24
        # 頂端半圓
        for i in range(steps + 1):
            a = math.pi + math.pi * i / steps
            pts.append((cx + half * math.cos(a), top + half + half * math.sin(a)))
        return pts
    def stroke(draw, pts, width, fill):
        draw.line([(x * s, y * s) for x, y in pts], fill=fill, width=int(width * s), joint="curve")
    # 組成一條連續路徑：外圈從左下往上繞頂端到右邊往下，再在底部繞回內圈往上
    path = []
    path.append((cx - r_out, bottom - 30))
    path += [(cx - r_out, top + r_out)]
    for i in range(25):
        a = math.pi + math.pi * i / 24
        path.append((cx + r_out * math.cos(a), top + r_out + r_out * math.sin(a)))
    path.append((cx + r_out, bottom - r_out))
    for i in range(25):
        a = 0 + math.pi * i / 24
        path.append((cx + r_out * math.cos(a), bottom - r_out + r_out * math.sin(a)))
    path.append((cx - r_out, top + 110))
    # 內圈
    inner = []
    inner.append((cx - r_in, top + 110))
    inner.append((cx - r_in, top + r_in + 26))
    for i in range(25):
        a = math.pi + math.pi * i / 24
        inner.append((cx + r_in * math.cos(a), top + r_in + 26 + r_in * math.sin(a)))
    inner.append((cx + r_in, bottom - 70))
    for i in range(13):
        a = 0 + math.pi * i / 24
        inner.append((cx + r_in * math.cos(a), bottom - 70 + r_in * math.sin(a)))
    pts_all = _rot(path, cx, (top + bottom) / 2, -2.0)
    inner_all = _rot(inner, cx, (top + bottom) / 2, -2.0)
    # 陰影
    sh = Image.new("RGBA", size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    for P in (pts_all, inner_all):
        stroke(sd, [(x + 5, y + 8) for x, y in P], 11, (0, 0, 0, 110))
    sh = sh.filter(ImageFilter.GaussianBlur(3 * s))
    d = ImageDraw.Draw(clip)
    for P in (pts_all, inner_all):
        stroke(d, P, 11, (70, 72, 76, 255))          # 外緣深色
        stroke(d, P, 8.5, (168, 170, 174, 255))      # 金屬本體
        stroke(d, [(x - 1.2, y - 1.2) for x, y in P], 3.2, (238, 240, 242, 255))   # 高光
        stroke(d, [(x + 1.6, y + 1.6) for x, y in P], 1.6, (95, 97, 100, 255))     # 下緣暗線
    return Image.alpha_composite(sh, clip)


def draw(out_path):
    s = SS
    size = (W * s, H * s)
    bg = Image.new("RGBA", size, BG + (255,))
    # 黑底加一點紙張顆粒，不要死黑
    rs = np.random.RandomState(9)
    arr = np.asarray(bg).astype(float)
    arr[..., :3] += rs.normal(0, 3, (H * s, W * s, 1))
    bg = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), "RGBA")
    paper, hole = _stamp_layer(s)
    # 郵票在黑底上的柔和陰影
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    sa = np.asarray(paper)[..., 3]
    shadow.putalpha(Image.fromarray((sa * 0.55).astype("uint8")))
    shadow = shadow.transform(size, Image.AFFINE, (1, 0, -10 * s, 0, 1, -14 * s))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10 * s))
    out = Image.alpha_composite(bg, shadow)
    out = Image.alpha_composite(out, paper)
    out = Image.alpha_composite(out, _paperclip(s))
    # 內部挖空：影片從這裡透出來（迴紋針壓在洞上的部分保留）
    a = np.asarray(out).copy()
    hole_a = np.asarray(hole) > 128
    clip_a = np.asarray(_paperclip(s))[..., 3] > 0
    a[..., 3] = np.where(hole_a & ~clip_a, 0, a[..., 3])
    out = Image.fromarray(a, "RGBA").resize((W, H), Image.LANCZOS)
    out.save(out_path)
    return out_path


if __name__ == "__main__":
    import sys
    print(draw(sys.argv[1] if len(sys.argv) > 1 else "stamp_overlay.png"))
