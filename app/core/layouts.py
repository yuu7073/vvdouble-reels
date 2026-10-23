# -*- coding: utf-8 -*-
# 版型：把設計底圖的藍／黃（／紫）色塊自動去背，塞進影片後用 ffmpeg 先合成一段 MP4，再放進草稿
#
# 版型圖放在 版型/ 資料夾（JPG 即可，不需要透明 PNG）
#   黃＝模特影片　藍＝模特影片或近拍　紫＝底圖照片（LOOKBOOK）
# 每個版型的「順序」：#＝版型段　M＝滿版模特　D＝滿版近拍

import json
import os
import subprocess
import sys

import numpy as np

def _layout_dir():
    """版型圖位置：原始碼模式在本檔同層的 版型/；打包後在 _internal/core/版型"""
    cands = [os.path.dirname(os.path.abspath(__file__))]
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cands = [os.path.join(base, "core"), base] + cands
    for c in cands:
        d = os.path.join(c, "版型")
        if os.path.isdir(d):
            return d
    return os.path.join(cands[0], "版型")


HERE = os.path.dirname(os.path.abspath(__file__))
LAYOUT_DIR = _layout_dir()
CANVAS_W, CANVAS_H = 1080, 1920
LAYOUT_SEC = 4.0          # 版型段目標長度（秒）

YEL = np.array([223, 207, 173.])
BLU = np.array([98, 98, 126.])
PUR = np.array([147, 89, 166.])
WHT = np.array([236, 236, 238.])        # 撕紙白邊
COLORS = {"yellow": YEL, "blue": BLU, "purple": PUR}

# 區塊：color＋index（同色由上到下、左到右第幾塊）→ 角色
#   M1＝第一位模特（大碼）  M2＝第二位模特（中碼）  D＝近拍  P＝底圖照片
LAYOUTS = {
    # LOOKBOOK：照 Vivian 2026-10-23 在 CapCut 調好的草稿逐項讀回來——
    #   底圖（模糊壓暗）加「色差默片」特效、模特影片照設計框位、不放框線層、
    #   版型段期間 網址 移到上方、商品名稱不顯示、LOOKBOOK 文字用 Windows「Modern」字型
    "lookbook": {"name": "LOOKBOOK", "file": "1_LOOKBOOK.jpg", "pattern": "#,M,M,D,M,D", "overlay": False,
                 "regions": [{"color": "purple", "role": "P", "blur": 10, "dark": 0.10,
                              "effects": [{"effect_id": "7395465862666390789", "name": "色差默片", "category_id": "8959", "category_name": "復古",
                                           "params": [("effects_adjust_vertical_chromatic", 0.57, 0.75),
                                                      ("effects_adjust_horizontal_chromatic", 0.71, 0.75),
                                                      ("effects_adjust_noise", 0.57, 1.0),
                                                      ("effects_adjust_filter", 0.36, 1.0)]}]},
                             {"color": "yellow", "role": "M1"}],
                 "texts": [{"text": "LOOKBOOK", "pos": [0.0, -0.6341], "font_path": "C:/WINDOWS/Fonts/modern.fon",
                            "size": 16.417, "scale": 1.4805, "bold": True, "color": (0.96, 0.93, 0.86), "letter_spacing": 0, "align": 1}],
                 "site_pos": [0.0, 0.6836], "hide_title": True,
                 "erase": [{"box": [60, 1440, 1420, 1660]}]},
    # 相機介面：照 Vivian 2026-10-23 在 CapCut 調好的草稿逐項讀回來——
    #   overlay 用程式畫（camera_overlay.py，上下條不透明、Inter 字型、沒有上方箭頭）
    #   模特影片不裁切、放大 1.147、往上移 y=0.2153，加「調節 > 顆粒」0.21
    #   左下相片格一層（矩形遮罩、圓角 15%），版型段期間 商品名稱 與 網址 都不顯示
    "camera":   {"name": "相機介面", "file": "2_相機.jpg", "pattern": "#,M,M,D,M,D", "overlay_draw": "camera",
                 "regions": [{"color": "yellow", "role": "M1", "box": [0, 0, 1080, 1920], "zoom": 1.1474, "offset": [0.0, 0.2153],
                              "adjusts": [{"type": "particle", "effect_id": "7501974767453474064", "value": 0.2124}]}],
                 "thumb": {"box": [142, 1749, 252, 1859], "role": "D", "round_corner": 15},
                 "hide_title": True, "hide_site": True},
    # 郵票：照 Vivian 2026-10-23 在 CapCut 調好的草稿讀回——
    #   版型段放最前面（#,M,D,M,M,D）；郵票框放大 1.0438；模特影片鋪滿畫面再放大 1.0438（郵票內只露中間）；
    #   版型段期間商品名稱不顯示、網址移到上方 (0.0286, 0.8215)
    #   框與迴紋針她嫌設計圖去背的不漂亮，改用程式畫仿真版（stamp_overlay.py）
    "stamp":    {"name": "郵票", "file": "3_郵票.jpg", "pattern": "#,M,D,M,M,D", "overlay_draw": "stamp",
                 "overlay_scale": 1.0438,
                 #   最底下可放底圖（製作頁選的底圖，鋪滿畫面），影片蓋不到的地方就露底圖不露黑邊
                 "regions": [{"role": "P", "box": [0, 0, 1080, 1920], "raw": True},
                             {"role": "M1", "box": [0, 0, 1080, 1920], "zoom": 1.0438}],
                 "hide_title": True, "site_pos": [0.0286, 0.8215]},
    # 撕紙：Vivian 2026-10-23 改成「較直線」的撕紙接縫——上下兩段都是矩形（可在 CapCut 直接換影片），
    #   接縫用她給的撕紙邊 PNG 紋理（torn_overlay.py）蓋住；上段在上層、兩段在接縫處重疊一點不露縫
    #   她在 CapCut 調好後讀回：順序改為 滿版→近拍→版型→滿版→近拍→滿版；撕紙框往下移 y=-0.0356；其餘（商品名稱、網址）照標準
    "torn_diag": {"name": "撕紙", "file": "4_撕紙斜.jpg", "pattern": "M,D,#,M,D,M", "overlay_draw": "torn",
                  "overlay_offset": [0.0, -0.0356],
                  "regions": [{"role": "D", "box": "torn_bottom"}, {"role": "M1", "box": "torn_top"}]},
    # 撕紙橫版：2026-10-23 她決定不用
    # DETAIL：照 Vivian 2026-10-23 在 CapCut 調好的草稿讀回——
    #   上下近拍位置不變（白帶本來就在正中央 862–1057）；DETAIL 字位置 x=-0.5966、放大 1.199；
    #   英文改成中性文案、位置 x=0.3412、行距 0.22；版型段期間網址不顯示、商品名稱照常
    "detail":   {"name": "DETAIL", "file": "6_DETAIL.jpg", "pattern": "M,M,#,M,M,D",
                 "regions": [{"color": "blue", "role": "D"}, {"color": "yellow", "role": "D2"}],
                 "texts": [{"text": "DETAIL", "pos": [-0.5966, 0.0], "scale": 1.1992, "size": 7.0, "bold": True,
                            "color": (0.05, 0.05, 0.05), "align": 0},
                           {"text": "Clean cuts, easy layers.\nNothing loud, nothing extra.\nJust the everyday, done well.",
                            "pos": [0.3412, 0.0], "size": 3.0, "color": (0.1, 0.1, 0.1), "align": 0, "line_spacing": 4}],   # CapCut 裡顯示 0.22
                 "hide_site": True,
                 "erase": [{"box": [60, 1230, 1130, 1350], "fill": (250, 249, 247)}]},
    # 九宮格：照 Vivian 2026-10-23 在 CapCut 調好的草稿讀回——格位不變；版型段期間 商品名稱 不顯示、網址 移到上方 y=0.7597
    "grid":     {"name": "九宮格", "file": "7_九宮格.jpg", "pattern": "#,M,D,M,M,M",
                 "hide_title": True, "site_pos": [0.0, 0.7597],
                 "regions": [{"color": "yellow", "index": 0, "role": "M1"},
                             {"color": "yellow", "index": 1, "role": "M2"},
                             {"color": "yellow", "index": 2, "role": "M1"},
                             {"color": "yellow", "index": 3, "role": "M2"},
                             {"color": "blue", "index": 0, "role": "D"},
                             {"color": "blue", "index": 1, "role": "M2"},
                             {"color": "blue", "index": 2, "role": "D"},
                             {"color": "blue", "index": 3, "role": "D"},
                             {"color": "blue", "index": 4, "role": "M1"}],
                 # 設計圖上的 (1)–(9) 編號是標示用，正式不要：格子以外全部填黑
                 "erase": [{"box": [0, 0, 1449, 432], "fill": (0, 0, 0)}, {"box": [0, 958, 1449, 1156], "fill": (0, 0, 0)},
                           {"box": [0, 1683, 1449, 1880], "fill": (0, 0, 0)}, {"box": [0, 2407, 1449, 2576], "fill": (0, 0, 0)}]},
}
ORDER = ["lookbook", "camera", "stamp", "torn_diag", "detail", "grid"]


def list_layouts():
    return [{"id": k, "name": LAYOUTS[k]["name"], "pattern": LAYOUTS[k]["pattern"],
             "needs_photo": any(r["role"] == "P" for r in LAYOUTS[k]["regions"])} for k in ORDER]


# ---------- 去色塊 ----------

def _key(p, k):
    """像素落在 白↔色塊 這條線上多少就算多少色塊；回傳 (連續 alpha, 去色後前景)"""
    v = k - WHT
    q = p - WHT
    a = (q @ v) / (v @ v)
    perp = np.linalg.norm(q - a[..., None] * v, axis=-1)
    a = np.clip(a, 0, 1) * np.clip((38 - perp) / 18, 0, 1)
    a2 = np.clip(a, 0, 0.5)[..., None]
    fg = np.clip((p - a2 * k) / (1 - a2), 0, 255)
    return a, fg


def _label(mask):
    """連通區塊編號（在 1/4 解析度做，再放大），回傳 (labels, n)"""
    small = mask[::4, ::4]
    h, w = small.shape
    lab = np.zeros((h, w), dtype=np.int32)
    n = 0
    for y0 in range(h):
        for x0 in range(w):
            if not small[y0, x0] or lab[y0, x0]:
                continue
            n += 1
            stack = [(y0, x0)]
            lab[y0, x0] = n
            while stack:
                y, x = stack.pop()
                for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                    if 0 <= yy < h and 0 <= xx < w and small[yy, xx] and not lab[yy, xx]:
                        lab[yy, xx] = n
                        stack.append((yy, xx))
    big = np.repeat(np.repeat(lab, 4, axis=0), 4, axis=1)[:mask.shape[0], :mask.shape[1]]
    return np.where(mask, big, 0), n


def prepare(layout_id, cache_dir, image_path=None):
    """產生去背的覆蓋圖、每個區塊的遮罩與位置；快取在 cache_dir/<layout_id>/"""
    from PIL import Image
    L = LAYOUTS[layout_id]
    src = image_path or os.path.join(LAYOUT_DIR, L["file"])
    out = os.path.join(cache_dir, layout_id)
    os.makedirs(out, exist_ok=True)
    stamp = f"{os.path.getmtime(src)}|{os.path.getsize(src)}|v22"
    meta_path = os.path.join(out, "regions.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("stamp") == stamp:
                return meta
        except Exception:
            pass

    im = Image.open(src).convert("RGB")
    p = np.asarray(im).astype(float)
    H, W = p.shape[:2]
    fg = p.copy()
    alpha_all = np.zeros((H, W))
    regions = []
    for color_name, k in COLORS.items():
        if not any(r.get("color") == color_name for r in L["regions"]):
            continue
        a, fgc = _key(p, k)
        hard = a > 0.5
        fg = np.where((a > 0.02)[..., None], fgc, fg)
        alpha_all = np.maximum(alpha_all, hard)
        lab, n = _label(hard)
        comps = []
        for i in range(1, n + 1):
            m = lab == i
            if m.sum() < W * H * 0.002:
                continue
            ys, xs = np.where(m)
            comps.append((int(ys.min()), int(xs.min()), m))
        comps.sort(key=lambda c: (c[0] // int(H * 0.1), c[1]))
        for idx, (_, _, m) in enumerate(comps):
            spec = next((r for r in L["regions"] if r.get("color") == color_name and r.get("index", 0) == idx), None)
            if not spec:
                continue
            mk = Image.fromarray((m * 255).astype(np.uint8)).resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
            mk = mk.point(lambda v: 255 if v >= 128 else 0)
            name = f"mask_{len(regions)}.png"
            mk.save(os.path.join(out, name))
            ma = np.asarray(mk) > 0
            ys, xs = np.where(ma)
            bbox_area = (xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)
            regions.append({"mask": name, "role": spec["role"], "_ord": L["regions"].index(spec),
                            "rect": bool(spec.get("force_rect") or ma.sum() >= bbox_area * 0.985),      # 不是矩形就要照形狀裁
                            "box": spec.get("box") or [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
                            "raw": bool(spec.get("raw")), "zoom": spec.get("zoom", 1.0), "offset": spec.get("offset", [0, 0]),
                            "effects": spec.get("effects", []), "adjusts": spec.get("adjusts", []),
                            "blur": spec.get("blur", 0), "dark": spec.get("dark", 0),
                            "noise": spec.get("noise", 0), "still": bool(spec.get("still"))})
    for spec in L["regions"]:
        if spec.get("color"):
            continue
        box = spec["box"]
        if box == "torn_top":
            import torn_overlay
            box = torn_overlay.seam_box_top()
        elif box == "torn_bottom":
            import torn_overlay
            box = torn_overlay.seam_box_bottom()
        mk = Image.new("L", (CANVAS_W, CANVAS_H), 0)
        from PIL import ImageDraw
        ImageDraw.Draw(mk).rectangle([box[0], box[1], box[2] - 1, box[3] - 1], fill=255)
        name = f"mask_{len(regions)}.png"
        mk.save(os.path.join(out, name))
        regions.append({"mask": name, "role": spec["role"], "_ord": L["regions"].index(spec), "rect": True,
                        "box": list(box), "raw": bool(spec.get("raw")), "zoom": spec.get("zoom", 1.0), "offset": spec.get("offset", [0, 0]),
                        "effects": spec.get("effects", []), "adjusts": spec.get("adjusts", []),
                        "blur": 0, "dark": 0, "noise": 0, "still": False})
    regions.sort(key=lambda r: r.get("_ord", 0))
    alpha = np.where(alpha_all > 0.5, 0, 255).astype(float)
    for er in L.get("erase", []):
        x0, y0, x1, y1 = er["box"]
        if er.get("fill"):
            fg[y0:y1, x0:x1] = np.array(er["fill"], dtype=float)
            alpha[y0:y1, x0:x1] = 255
        else:
            alpha[y0:y1, x0:x1] = 0
    rgba = np.dstack([fg, alpha]).astype(np.uint8)
    ov = Image.fromarray(rgba, "RGBA").resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
    arr = np.asarray(ov).copy()
    arr[..., 3] = np.where(arr[..., 3] >= 128, 255, 0)
    if L.get("overlay_draw") == "camera":
        import camera_overlay
        camera_overlay.draw(os.path.join(out, "overlay.png"))
    elif L.get("overlay_draw") == "torn":
        import torn_overlay
        torn_overlay.draw(os.path.join(out, "overlay.png"))
    elif L.get("overlay_draw") == "stamp":
        import stamp_overlay
        stamp_overlay.draw(os.path.join(out, "overlay.png"))
    else:
        Image.fromarray(arr, "RGBA").save(os.path.join(out, "overlay.png"))
    sx, sy = CANVAS_W / W, CANVAS_H / H
    texts = []
    for t in L.get("texts", []):
        if t.get("pos"):
            texts.append(dict(t))
        else:
            x0, y0, x1, y1 = t["box"]
            texts.append(dict(t, box=[int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)]))
    meta = {"stamp": stamp, "overlay": "overlay.png" if L.get("overlay", True) else "", "regions": regions, "texts": texts, "dir": out,
            "site_pos": L.get("site_pos"), "hide_title": bool(L.get("hide_title")), "hide_site": bool(L.get("hide_site")), "thumb": L.get("thumb"),
            "overlay_offset": L.get("overlay_offset"), "overlay_scale": L.get("overlay_scale")}
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    return meta


# ---------- 放進 CapCut（原生圖層，不先壓成影片） ----------

def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def probe(path):
    """回傳影片顯示用的 (寬, 高)，已把旋轉算進去"""
    import re
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-i", path], capture_output=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    err = r.stderr.decode("utf-8", "ignore")
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    w, h = (int(m.group(1)), int(m.group(2))) if m else (1080, 1920)
    rot = re.search(r"rotate\s*:\s*(-?\d+)", err)
    rot = int(rot.group(1)) if rot else 0
    disp = re.search(r"displaymatrix:.*?rotation of (-?[\d.]+)", err)
    if disp:
        rot = int(round(float(disp.group(1))))
    if rot % 180 != 0:
        w, h = h, w
    return w, h


def place(box, sw, sh, focus_x=0.5, focus_y=0.3):
    """
    把 sw×sh 的素材「蓋滿」畫布上的 box（像素）：
    回傳 (裁切比例 dict, scale, transform_x, transform_y)，直接對應 CapCut 的裁切／縮放／位置
    transform 單位同 CapCut：±1 = 半個畫布寬／高，y 向上為正
    """
    bw, bh = box[2] - box[0], box[3] - box[1]
    a = bw / bh
    if sw / sh > a:
        cw, ch = sh * a, sh
    else:
        cw, ch = sw, sw / a
    x0 = (sw - cw) * max(0.0, min(1.0, focus_x))
    y0 = (sh - ch) * max(0.0, min(1.0, focus_y))
    crop = {"upper_left_x": x0 / sw, "upper_left_y": y0 / sh, "upper_right_x": (x0 + cw) / sw, "upper_right_y": y0 / sh,
            "lower_left_x": x0 / sw, "lower_left_y": (y0 + ch) / sh, "lower_right_x": (x0 + cw) / sw, "lower_right_y": (y0 + ch) / sh}
    f = min(CANVAS_W / cw, CANVAS_H / ch)          # 裁切後的素材在 CapCut 裡 scale=1 時是「塞進畫布」
    scale = bw / (cw * f)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    tx = (cx - CANVAS_W / 2) / (CANVAS_W / 2)
    ty = -(cy - CANVAS_H / 2) / (CANVAS_H / 2)
    return crop, scale, tx, ty


def text_place(box):
    """文字層位置：回傳 (transform_x, transform_y)，以框的中心為準"""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return (cx - CANVAS_W / 2) / (CANVAS_W / 2), -(cy - CANVAS_H / 2) / (CANVAS_H / 2)


def background_photo(photo, box, out_path, blur=10, dark=0.10):
    """底圖照片：蓋滿 box、模糊、壓暗，存成 PNG 給 CapCut 當獨立圖層（之後要換照片直接在 CapCut 換）"""
    from PIL import Image, ImageFilter, ImageOps
    bw, bh = box[2] - box[0], box[3] - box[1]
    with Image.open(photo) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        r = max(bw / im.width, bh / im.height)
        im = im.resize((int(im.width * r) + 1, int(im.height * r) + 1), Image.LANCZOS)
        x0 = (im.width - bw) // 2
        y0 = (im.height - bh) // 2
        im = im.crop((x0, y0, x0 + bw, y0 + bh))
        if blur:
            im = im.filter(ImageFilter.GaussianBlur(blur * bw / 1080))
        if dark:
            im = Image.eval(im, lambda v: int(v * (1 - dark)))
        im.save(out_path)
    return out_path


def render_alpha_clip(src, start, dur, box, mask_png, out_path, focus_x=0.5, focus_y=0.3, fps=30, adjusts=None):
    """
    區塊不是矩形（撕紙弧度等）時：把影片 cover 進 box、墊成整個畫布，再用遮罩 PNG 當 alpha，
    輸出帶透明的 .mov（ProRes 4444）。放進 CapCut 後仍是獨立一層，可以移動、縮放。
    """
    sw, sh = probe(src)
    bw, bh = box[2] - box[0], box[3] - box[1]
    r = max(bw / sw, bh / sh)
    s_w, s_h = int(round(sw * r)) + 2, int(round(sh * r)) + 2
    ox = int(max(0, min(s_w - bw, (s_w - bw) * focus_x)))
    oy = int(max(0, min(s_h - bh, (s_h - bh) * focus_y)))
    chain = (f"[0:v]scale={s_w}:{s_h}:flags=lanczos,crop={bw}:{bh}:{ox}:{oy},fps={fps},format=rgba,"
             f"pad={CANVAS_W}:{CANVAS_H}:{box[0]}:{box[1]}:color=black@0.0[v];"
             f"[1:v]format=gray,fps={fps}[m];[v][m]alphamerge,format=yuva444p10le[out]")
    args = [_ffmpeg(), "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src,
            "-loop", "1", "-t", f"{dur:.3f}", "-i", mask_png,
            "-filter_complex", chain, "-map", "[out]", "-t", f"{dur:.3f}", "-r", str(fps),
            "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-an", out_path]
    rr = subprocess.run(args, capture_output=True, timeout=600,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if rr.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("ffmpeg 裁切失敗：" + rr.stderr.decode("utf-8", "ignore")[-500:])
    return out_path


def cover_scale(sw, sh):
    """素材不裁切、直接放大到鋪滿整個畫布所需的 scale（scale=1 是塞進畫布）"""
    f = min(CANVAS_W / sw, CANVAS_H / sh)
    return max(CANVAS_W / (sw * f), CANVAS_H / (sh * f))
