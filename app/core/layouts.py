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
    "lookbook": {"name": "LOOKBOOK", "file": "1_LOOKBOOK.jpg", "pattern": "#,M,M,D,M,D",
                 "regions": [{"color": "purple", "role": "P", "blur": 10, "dark": 0.10},
                             {"color": "yellow", "role": "M1", "still": True, "noise": 0.05}]},
    "camera":   {"name": "相機介面", "file": "2_相機.jpg", "pattern": "#,M,M,D,M,D",
                 "regions": [{"color": "yellow", "role": "M1"}]},
    "stamp":    {"name": "郵票", "file": "3_郵票.jpg", "pattern": "M,#,D,M,M,D",
                 "regions": [{"color": "blue", "role": "M1"}]},
    "torn_diag": {"name": "撕紙斜版", "file": "4_撕紙斜.jpg", "pattern": "M,D,#,D,M,M",
                  "regions": [{"color": "yellow", "role": "M1"}, {"color": "blue", "role": "D"}]},
    "torn_band": {"name": "撕紙橫版", "file": "5_撕紙橫.jpg", "pattern": "M,M,#,D,M",
                  "regions": [{"color": "blue", "index": 0, "role": "M1"},
                              {"color": "blue", "index": 1, "role": "M2"},
                              {"color": "yellow", "role": "D"}]},
    "detail":   {"name": "DETAIL", "file": "6_DETAIL.jpg", "pattern": "M,M,#,M,M,D",
                 "regions": [{"color": "blue", "role": "D"}, {"color": "yellow", "role": "D2"}]},
    "grid":     {"name": "九宮格", "file": "7_九宮格.jpg", "pattern": "#,M,D,M,M,M",
                 "regions": [{"color": "yellow", "index": 0, "role": "M1"},
                             {"color": "yellow", "index": 1, "role": "M2"},
                             {"color": "yellow", "index": 2, "role": "M1"},
                             {"color": "yellow", "index": 3, "role": "M2"},
                             {"color": "blue", "index": 0, "role": "D"},
                             {"color": "blue", "index": 1, "role": "M2"},
                             {"color": "blue", "index": 2, "role": "D"},
                             {"color": "blue", "index": 3, "role": "D"},
                             {"color": "blue", "index": 4, "role": "M1"}]},
}
ORDER = ["lookbook", "camera", "stamp", "torn_diag", "torn_band", "detail", "grid"]


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
    stamp = f"{os.path.getmtime(src)}|{os.path.getsize(src)}|v3"
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
        if not any(r["color"] == color_name for r in L["regions"]):
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
            spec = next((r for r in L["regions"] if r["color"] == color_name and r.get("index", 0) == idx), None)
            if not spec:
                continue
            mk = Image.fromarray((m * 255).astype(np.uint8)).resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
            mk = mk.point(lambda v: 255 if v >= 128 else 0)
            name = f"mask_{len(regions)}.png"
            mk.save(os.path.join(out, name))
            ma = np.asarray(mk) > 0
            ys, xs = np.where(ma)
            regions.append({"mask": name, "role": spec["role"],
                            "box": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
                            "blur": spec.get("blur", 0), "dark": spec.get("dark", 0),
                            "noise": spec.get("noise", 0), "still": bool(spec.get("still"))})
    rgba = np.dstack([fg, np.where(alpha_all > 0.5, 0, 255)]).astype(np.uint8)
    ov = Image.fromarray(rgba, "RGBA").resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
    arr = np.asarray(ov).copy()
    arr[..., 3] = np.where(arr[..., 3] >= 128, 255, 0)
    Image.fromarray(arr, "RGBA").save(os.path.join(out, "overlay.png"))
    meta = {"stamp": stamp, "overlay": "overlay.png", "regions": regions, "dir": out}
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    return meta


# ---------- 合成 ----------

def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _probe(path):
    """回傳 (寬, 高, 旋轉角度)"""
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


def _fit_filter(src_w, src_h, box, focus_x=0.5, focus_y=0.3):
    """把來源 cover 進 box：回傳 scale+crop 濾鏡字串"""
    bw, bh = box[2] - box[0], box[3] - box[1]
    r = max(bw / src_w, bh / src_h)
    sw, sh = int(round(src_w * r)) + 2, int(round(src_h * r)) + 2
    ox = int(max(0, min(sw - bw, (sw - bw) * focus_x)))
    oy = int(max(0, min(sh - bh, (sh - bh) * focus_y)))
    return f"scale={sw}:{sh}:flags=lanczos,crop={bw}:{bh}:{ox}:{oy}"


def render(meta, sources, dur, out_path, fps=30, log=None):
    """
    sources = {role: {"path", "start": 秒, "focus_x": 0~1, "photo": bool}}
    把每個區塊的來源塞進去背後的版型，輸出 dur 秒的 MP4
    """
    exe = _ffmpeg()
    args = [exe, "-y", "-v", "error"]
    filt = []
    inputs = 0
    layers = []
    for i, reg in enumerate(meta["regions"]):
        src = sources.get(reg["role"]) or sources.get(reg["role"][:1])
        if not src:
            continue
        box = reg["box"]
        path = src["path"]
        is_img = bool(src.get("photo")) or path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        if is_img or reg.get("still"):
            if is_img:
                args += ["-loop", "1", "-t", f"{dur:.3f}", "-i", path]
            else:
                args += ["-ss", f"{src.get('start', 0):.3f}", "-t", f"{dur:.3f}", "-i", path]
            vi = inputs
            inputs += 1
            if is_img:
                from PIL import Image
                with Image.open(path) as im:
                    from PIL import ImageOps
                    im = ImageOps.exif_transpose(im)
                    sw, sh = im.size
            else:
                sw, sh = _probe(path)
            chain = _fit_filter(sw, sh, box, src.get("focus_x", 0.5), src.get("focus_y", 0.3))
            if reg.get("still") and not is_img:
                chain += f",trim=end_frame=1,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={dur:.3f}"
        else:
            args += ["-ss", f"{src.get('start', 0):.3f}", "-t", f"{dur:.3f}", "-i", path]
            vi = inputs
            inputs += 1
            sw, sh = _probe(path)
            chain = _fit_filter(sw, sh, box, src.get("focus_x", 0.5), src.get("focus_y", 0.3))
        if reg.get("blur"):
            chain += f",gblur=sigma={reg['blur']}"
        if reg.get("dark"):
            m = 1 - float(reg["dark"])
            chain += f",lutrgb=r=val*{m:.3f}:g=val*{m:.3f}:b=val*{m:.3f}"
        if reg.get("noise"):
            chain += f",noise=alls={int(reg['noise'] * 255)}:allf=t"
        chain += f",fps={fps},format=rgba,pad={CANVAS_W}:{CANVAS_H}:{box[0]}:{box[1]}:color=black@0.0"
        args += ["-loop", "1", "-t", f"{dur:.3f}", "-i", os.path.join(meta["dir"], reg["mask"])]
        mi = inputs
        inputs += 1
        filt.append(f"[{vi}:v]{chain}[r{i}]")
        filt.append(f"[{mi}:v]format=gray,fps={fps}[m{i}]")
        filt.append(f"[r{i}][m{i}]alphamerge[a{i}]")
        layers.append(f"a{i}")
    args += ["-loop", "1", "-t", f"{dur:.3f}", "-i", os.path.join(meta["dir"], meta["overlay"])]
    oi = inputs
    filt.append(f"color=c=black:s={CANVAS_W}x{CANVAS_H}:r={fps}:d={dur:.3f}[base0]")
    cur = "base0"
    for n, lname in enumerate(layers):
        filt.append(f"[{cur}][{lname}]overlay=0:0:shortest=1[b{n}]")
        cur = f"b{n}"
    filt.append(f"[{oi}:v]format=rgba,fps={fps}[ov]")
    filt.append(f"[{cur}][ov]overlay=0:0:shortest=1,format=yuv420p[out]")
    args += ["-filter_complex", ";".join(filt), "-map", "[out]", "-t", f"{dur:.3f}", "-r", str(fps),
             "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-an", "-movflags", "+faststart", out_path]
    r = subprocess.run(args, capture_output=True, timeout=600,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("ffmpeg 合成失敗：" + r.stderr.decode("utf-8", "ignore")[-600:])
    return out_path


if __name__ == "__main__":
    # 測試：python layouts.py <版型id> <快取資料夾> <輸出.mp4> role=path[@start] ...
    lid, cache, outp = sys.argv[1:4]
    srcs = {}
    for a in sys.argv[4:]:
        role, pth = a.split("=", 1)
        st = 0.0
        if "@" in pth:
            pth, st = pth.rsplit("@", 1)
            st = float(st)
        srcs[role] = {"path": pth, "start": st}
    m = prepare(lid, cache)
    print(json.dumps({k: v for k, v in m.items() if k != "stamp"}, ensure_ascii=False, indent=1))
    render(m, srcs, LAYOUT_SEC, outp)
    print("ok", outp)
