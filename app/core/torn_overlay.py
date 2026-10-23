# -*- coding: utf-8 -*-
# 撕紙版型的接縫：一條近直線的撕紙白邊（纖維感），剛好蓋住上下兩段影片的交接處
# 上下影片都是矩形，不用照弧度裁，換影片可以直接在 CapCut 替換

import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 1080, 1920
SEAM_Y = 1000          # 接縫中心（畫布像素）
WAVE = 14              # 線的起伏幅度
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


def _smooth_noise(shape, scale, seed):
    """平滑的 2D 亂數（0~1）：先做小圖再放大"""
    rs = np.random.RandomState(seed)
    h, w = shape
    small = rs.rand(max(2, h // scale + 2), max(2, w // scale + 2))
    im = Image.fromarray((small * 255).astype("uint8")).resize((w, h), Image.BICUBIC)
    return np.asarray(im).astype(float) / 255.0


def draw(out_path, seed=7):
    rnd = random.Random(seed)
    y = _line(seed)
    T = FIBER[1] + 10                        # 毛邊最大厚度
    band_h = T + 12
    y0 = int(SEAM_Y - WAVE * 2 - T - 8)
    y1 = int(SEAM_Y + WAVE * 2 + 12)
    hh = y1 - y0
    yy = np.arange(y0, y1)[:, None]
    yl = y[None, :]
    dist = (yl - yy) / T                    # 線上方：0 在線上，1 在最厚處
    n1 = _smooth_noise((hh, W), 18, seed)   # 大塊起伏
    n2 = _smooth_noise((hh, W), 5, seed + 1)  # 細碎纖維
    thick = 0.45 + 0.55 * n1                # 每個位置的厚度係數
    ragged = n2 * 0.35
    alpha = np.where((dist >= -0.06) & (dist < thick - ragged), 1.0, 0.0)
    # 外緣再長一些細毛
    hair = (dist >= thick - ragged) & (dist < thick + 0.25) & (n2 > 0.72) & (((np.arange(W)[None, :] * 7919) % 13) < 4)
    alpha = np.maximum(alpha, hair * 0.85)
    alpha = np.clip(alpha, 0, 1)
    rgba = np.zeros((hh, W, 4), dtype=float)
    tone = 240 + 12 * n2                    # 紙白帶一點明暗
    rgba[..., 0] = tone; rgba[..., 1] = tone - 2; rgba[..., 2] = tone - 6
    rgba[..., 3] = alpha * 255
    band = Image.fromarray(rgba.astype("uint8"), "RGBA")
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    im.paste(band, (0, y0))
    # 線下方一道淡陰影，讓上層紙有厚度
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    for x in range(W):
        sd.line([(x, int(y[x]) + 1), (x, int(y[x]) + 8)], fill=(0, 0, 0, 80))
    sh = sh.filter(ImageFilter.GaussianBlur(2.5))
    out = Image.alpha_composite(sh, im)
    out.save(out_path)
    return out_path


if __name__ == "__main__":
    import sys
    print(draw(sys.argv[1] if len(sys.argv) > 1 else "torn_overlay.png"))
