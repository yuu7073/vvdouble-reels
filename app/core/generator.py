# -*- coding: utf-8 -*-
# VV DOUBLE 新品模特實穿 Reels 草稿產生器 v4
#
# 用法 A（單一商品）：把 執行.bat、本程式、模特資料.csv 放進影片所在的資料夾，雙擊執行
# 用法 B（批次）：同層建「商品」資料夾，裡面每個商品一個子資料夾
#
# 檔名規則（顏色、尺碼可省略）：
#   亮亮 (1).MP4                 → 只有模特
#   亮亮_奶茶色_3L4L (2).MP4     → 模特＋顏色＋尺碼
#   近拍 (1).mov                 → 近拍，排在最後並自動放大
# 同一位模特的多段影片會排在一起，順序：尺碼大→小，沒有尺碼時依體重重→輕

import csv
import os
import re
import sys

import copy
import json
import subprocess
import uuid

import numpy as np
import pycapcut as cc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import vision
except Exception:
    vision = None
from pycapcut import TextStyle, ClipSettings, TextBorder, TextBackground, KeyframeProperty, SEC

# ===== 設定 =====
DRAFT_FOLDER = os.path.join(os.environ.get("LOCALAPPDATA", ""), "CapCut", "User Data", "Projects", "com.lveditor.draft")
WIDTH, HEIGHT = 1080, 1920
CLIP_SEC = 3.0            # 每段實穿最多取幾秒
CLIP_SKIP = 0.5           # 跳過每段開頭幾秒（避開按錄影的晃動）
CLOSEUP_SEC = 2.5         # 每段近拍最多取幾秒
ZOOM_SCALE = 1.35         # 近拍結尾放大倍率
FIT_DEFAULT = "舒適"
CARD_Y = -0.62
TITLE_Y = 0.80
CLOSEUP_Y = 0.0           # 完全置中
MAX_TOTAL_SEC = 28.0      # 影片總長上限
CLOSEUP_SHARE = 0.3       # 近拍佔總段數比例
SITE_Y = -0.90
SITE_TEXT = "WWW.VVDOUBLE.COM"
BEAT_SYNC = True          # 有音樂時自動卡點
MID_CLOSEUP_RANGE = (1.5, 2.0)   # 中間那段近拍長度（秒）
AUTO_STABILIZE = True     # 手震明顯的片段自動開啟 CapCut 穩定（最少裁切）
SHAKE_THRESHOLD = 0.4     # 抖動量超過畫面寬度的 0.4% 就開
STABLE_LEVEL = 1          # 0=關 1=最少裁切 2=推薦 3=最穩定
PERSON_CENTER = True      # 人物置中：挑人在中間的片段；偏一邊時稍微放大並平移置中
PERSON_MAX_SCALE = 1.25   # 置中平移時最多放大到幾倍
AUTO_BEST_SEGMENT = True  # 自動挑每支影片最穩、最清楚的片段（檔名加 @12 可指定從第 12 秒）
MUSIC_FADE_IN = 1.0      # 音樂漸入秒數
MUSIC_AUTO_HIGHLIGHT = True   # 自動從歌曲最豐富的段落開始（檔名加 @45 可指定從第 45 秒）
TEMPLATE_DRAFT = "實穿模板"   # 潤飾從這份草稿複製：每位模特套一段臉部預設組
TITLE_POS_Y = 0.67           # 套預設組後商品名稱位置
SITE_POS_Y = 0.745           # 套預設組後網址位置
# ================

HERE = os.path.dirname(os.path.abspath(__file__))
STYLE_FILE = os.path.join(HERE, "樣式.json")
LAYOUT_TEXT_TEMPLATE = os.path.join(HERE, "版型文字模板.json")   # CapCut 存過的文字素材，給版型文字當底
BEAUTY_FILE = os.path.join(HERE, "潤飾.json")
PRODUCT_ROOT = os.path.join(HERE, "商品")
MODEL_FILE = os.path.join(HERE, "模特資料.csv")
VIDEO_EXT = (".mp4", ".mov", ".m4v")
AUDIO_EXT = (".mp3", ".m4a", ".wav", ".aac")
SIZE_ORDER = ["F", "FREE", "S", "SM", "M", "ML", "L", "XL", "XL2L", "2L", "2XL", "3L", "3XL",
              "3L4L", "4L", "4XL", "5L", "5XL", "5L6L", "6L", "6XL"]


def fail(msg):
    print("\n[錯誤] " + msg)
    input("\n按 Enter 結束...")
    sys.exit(1)


def read_csv(path):
    for enc in ("utf-8-sig", "cp950", "big5"):
        try:
            with open(path, encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    fail(f"{os.path.basename(path)} 無法讀取，請另存為「CSV UTF-8」")


def load_models():
    if not os.path.exists(MODEL_FILE):
        fail("找不到 模特資料.csv")
    models = {}
    for r in read_csv(MODEL_FILE):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        name = r.get("名字", "")
        if name:
            models[name.upper()] = {
                "名字": name,
                "身高": re.sub(r"[^0-9.]", "", r.get("身高", "")),
                "體重": re.sub(r"[^0-9.]", "", r.get("體重", "")),
            }
    return models


def parse_clip(filename):
    """回傳 (順序編號, 段落編號, [模特, 顏色, 尺碼, 版型])"""
    stem = os.path.splitext(filename)[0].strip()
    stem = re.sub(r"\s*@\d+(?:\.\d+)?\s*$", "", stem)   # 去掉 @秒數
    order = None
    m = re.match(r"^(\d+)[_\-\s]+", stem)
    if m:
        order = int(m.group(1))
        stem = stem[m.end():]
    take = 0
    m = re.search(r"\s*[\(（](\d+)[\)）]\s*$", stem)
    if m:
        take = int(m.group(1))
        stem = stem[:m.start()]
    parts = [p.strip() for p in re.split(r"[_\-]", stem) if p.strip()]
    return order, take, parts


WORD_RANK = {"大": 100, "中": 50, "小": 10}


def size_of(parts):
    """從檔名找尺碼：第3段，或任何「著XX」那段"""
    for p in parts[1:]:
        if p.startswith("著"):
            return p
    return parts[2] if len(parts) > 2 else ""


def size_rank(size):
    s = size.upper().replace(" ", "")
    if not s:
        return -1
    for w, r in WORD_RANK.items():
        if w + "碼" in s:
            return r
    if s in SIZE_ORDER:
        return SIZE_ORDER.index(s)
    for i in range(len(SIZE_ORDER) - 1, -1, -1):
        if SIZE_ORDER[i] in s:
            return i
    return -1


def sort_clips(clips, models):
    def key(item):
        f, _ = item
        order, take, parts = parse_clip(f)
        name = parts[0].upper() if parts else ""
        if order is not None:
            return (0, order, name, take)
        size = size_of(parts)
        model = models.get(name)
        weight = float(model["體重"]) if model and model["體重"] else 0
        return (1, -size_rank(size), -weight, name, take)
    return sorted(clips, key=key)


def card_text(model, size, fit):
    lines = [f"MODEL {model['名字'].upper()}"]
    hw = " / ".join(x for x in (
        f"{model['身高']}CM" if model["身高"] else "",
        f"{model['體重']}KG" if model["體重"] else "") if x)
    if hw:
        lines.append(hw)
    if size.startswith("著"):
        lines.append(size)
    elif size:
        lines.append(f"著{size} {fit}".strip())
    return "\n".join(lines)


LAST_ENERGY = None


def manual_start(filename):
    m = re.search(r"@(\d+(?:\.\d+)?)\s*$", os.path.splitext(filename)[0])
    return float(m.group(1)) if m else None


def analyze_clip(path):
    """解關鍵影格一次，算清晰度／晃動／人物位置。回傳 dict 或 None"""
    if vision is None:
        return None
    frames, times = vision.decode_keyframes(path, 320)
    if frames is None or len(frames) < 2:
        return None
    # 有些手機影片每一格都是關鍵影格，抽到每秒 4 張就夠
    span = max(float(times[-1] - times[0]), 0.25)
    if len(times) > span * 4:
        idx = np.unique(np.linspace(0, len(times) - 1, int(span * 4) + 1).astype(int))
        frames, times = frames[idx], times[idx]
    gray = frames.astype(np.float32).mean(axis=3)
    lap = (gray[:, 1:-1, 1:-1] * 4 - gray[:, :-2, 1:-1] - gray[:, 2:, 1:-1]
           - gray[:, 1:-1, :-2] - gray[:, 1:-1, 2:])
    sharp = lap.var(axis=(1, 2))
    motion = np.concatenate([[0], np.abs(np.diff(gray, axis=0)).mean(axis=(1, 2))])
    motion[0] = motion[1] if len(motion) > 1 else 0
    people = vision.detect_people(frames) if PERSON_CENTER and vision.available() else None
    return {"frames": frames, "times": times, "sharp": sharp, "motion": motion, "people": people}


def best_window(path, length_sec, total_sec, edge=0.5, info=None, want_person=True):
    """挑「清晰度高、晃動小、人物在中間」的時間窗；失敗回傳 None"""
    if total_sec <= length_sec + 2 * edge + 0.5:
        return None
    if info is None:
        info = analyze_clip(path)
    if info is None or len(info["times"]) < 3:
        return None
    times, sharp, motion = info["times"], info["sharp"], info["motion"]

    def norm(a):
        return (a - a.min()) / (a.max() - a.min() + 1e-6)
    score = norm(sharp) - norm(motion)
    if want_person and info.get("people"):
        fr = np.array([vision.framing_score(p) for p in info["people"]])
        found = np.array([p is not None for p in info["people"]])
        if found.mean() >= 0.3:          # 這支影片真的有人才用構圖分數
            score = score + 1.5 * fr

    grid = np.arange(0, total_sec, 0.1)
    curve = np.interp(grid, times, score)
    win = max(1, int(round(length_sec / 0.1)))
    csum = np.concatenate([[0], np.cumsum(curve)])
    best_t, best_s = None, -9.0
    for i in range(int(edge / 0.1), len(grid) - win - int(edge / 0.1)):
        sc = (csum[i + win] - csum[i]) / win
        if sc > best_s:
            best_t, best_s = float(grid[i]), sc
    return best_t


def center_settings(info, t0, t1):
    """人物偏離中心時，回傳 (scale, transform_x) 讓人置中；不需要時回傳 None"""
    if not PERSON_CENTER or info is None or not info.get("people"):
        return None
    off = vision.person_offset(info["people"], info["times"], t0, t1)
    if off is None or abs(off) < 0.05:
        return None
    shift = -2 * off                              # 單位：半個畫布寬
    scale = min(PERSON_MAX_SCALE, 1.0 + abs(shift))
    shift = max(-(scale - 1), min(scale - 1, shift))  # 不露黑邊
    return scale, shift


def zoom_target(info, t0, t1):
    """近拍：回傳要放大到的點 (tx, ty)，0~1；沒有就 None"""
    if info is None or vision is None:
        return None
    return vision.detail_point(info["frames"], info["times"], t0, t1)


def choose_start(f, full, mat, dur, default_skip, info=None, want_person=True):
    """決定從影片第幾秒開始：檔名 @秒數 → 自動挑最穩最清楚 → 預設"""
    total = mat.duration / SEC
    want = manual_start(f)
    if want is not None:
        return int(max(0.0, min(want, total - dur / SEC)) * SEC), "指定"
    if AUTO_BEST_SEGMENT:
        t = best_window(full, dur / SEC, total, info=info, want_person=want_person)
        if t is not None:
            return int(t * SEC), "自動"
    return min(default_skip, max(0, mat.duration - dur)), "開頭"


def music_start_index(beats, bgm_path, length_sec):
    """選音樂從哪一拍開始：檔名有 @秒數 就照指定；否則自動挑最豐富的段落"""
    m = re.search(r"@(\d+(?:\.\d+)?)", os.path.basename(bgm_path))
    if m:
        want = float(m.group(1))
        return min(range(len(beats)), key=lambda i: abs(beats[i] - want)), "指定"
    if not MUSIC_AUTO_HIGHLIGHT or LAST_ENERGY is None:
        return 0, "開頭"
    rms, fps = LAST_ENERGY
    song_len = len(rms) / fps
    best, best_score = 0, -1.0
    for i in range(0, len(beats), 4):          # 每 4 拍（一小節）找一次
        st = beats[i]
        if st + length_sec > song_len - 3:      # 留到結尾前 3 秒
            break
        a, b = int(st * fps), int((st + length_sec) * fps)
        score = float(rms[a:b].mean())
        if score > best_score:
            best, best_score = i, score
    return best, "自動挑段"


def detect_beats(path):
    """回傳 (拍點秒數 list, BPM)；失敗回傳 (None, None)"""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None, None
    sr, hop, nfft = 22050, 512, 2048
    try:
        raw = subprocess.run([exe, "-v", "quiet", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                             capture_output=True, timeout=120,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return None, None
    y = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if len(y) < sr * 5:
        return None, None
    n = 1 + (len(y) - nfft) // hop
    win = np.hanning(nfft).astype(np.float32)
    idx = np.arange(nfft)[None, :] + hop * np.arange(n)[:, None]
    mag = np.log1p(10 * np.abs(np.fft.rfft(y[idx] * win, axis=1)))
    flux = np.maximum(0, np.diff(mag, axis=0)).sum(axis=1)
    flux = np.concatenate([[0], flux])
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")
    flux = np.maximum(flux, 0)
    fps = sr / hop
    ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    lags = np.arange(len(ac))
    bpm = np.where(lags > 0, 60 * fps / np.maximum(lags, 1), 0)
    weight = np.exp(-0.5 * (np.log2(np.maximum(bpm, 1) / 120) / 0.9) ** 2)
    valid = (bpm >= 70) & (bpm <= 180)
    score = np.where(valid, ac * weight, 0)
    k = int(np.argmax(score))
    if k <= 1 or k >= len(score) - 1:
        return None, None
    a, b, c = score[k - 1], score[k], score[k + 1]
    denom = a - 2 * b + c
    period = k + (0.5 * (a - c) / denom if denom != 0 else 0.0)
    ip = int(round(period))
    grid = np.arange(0, len(flux) - period, period)
    def phase_score(o):
        ix = np.round(o + grid).astype(int)
        ix = ix[ix < len(flux)]
        return flux[ix].sum()
    phase = max(np.arange(0, period, 0.5), key=phase_score)
    rad = max(1, ip // 4)
    beats, strength = [], []
    pos = float(phase)
    while pos < len(flux):
        c0 = int(round(pos))
        lo, hi = max(0, c0 - rad), min(len(flux), c0 + rad + 1)
        f2 = lo + int(np.argmax(flux[lo:hi]))
        beats.append(f2 / fps + nfft / 2 / sr)
        strength.append(flux[f2])
        pos = (f2 + period) if flux[f2] > 0 else (pos + period)
    strength = np.array(strength)
    thr = 0.5 * np.median(strength[strength > 0]) if (strength > 0).any() else 0
    first = next((i for i, v in enumerate(strength) if v > thr), 0)
    beats = beats[first:]
    global LAST_ENERGY
    rms = np.sqrt((y[idx] ** 2).mean(axis=1))
    LAST_ENERGY = (rms, fps)
    return beats, 60 * fps / period


def beats_for(target_sec, beat_len):
    n = target_sec / beat_len
    return min((1, 2, 4, 8), key=lambda c: (abs(c - n), c))


def cut(mat, max_sec, skip_sec):
    total = mat.duration
    skip = int(skip_sec * SEC)
    if total - skip < SEC:
        skip = 0
    dur = min(int(max_sec * SEC), total - skip)
    return skip, dur


def pick_even(items, n):
    """從 items 平均挑 n 個（保留頭尾）"""
    if n >= len(items):
        return list(items)
    if n <= 0:
        return []
    if n == 1:
        return [items[len(items) // 2]]
    step = (len(items) - 1) / (n - 1)
    return [items[round(i * step)] for i in range(n)]


def select_clips(clips, closeups, clip_len, close_len):
    """控制總長在 MAX_TOTAL_SEC 內：近拍約佔 3 成，其餘平均分給每位模特"""
    groups = []
    for f, full in clips:
        name = (parse_clip(f)[2] or [""])[0].upper()
        if groups and groups[-1][0] == name:
            groups[-1][1].append((f, full))
        else:
            groups.append((name, [(f, full)]))

    n_close = min(len(closeups), max(1, round(MAX_TOTAL_SEC * CLOSEUP_SHARE / close_len))) if closeups else 0
    budget = MAX_TOTAL_SEC - n_close * close_len
    n_clip = max(len(groups), int(budget // clip_len)) if groups else 0

    quota = [0] * len(groups)
    left = n_clip
    while left > 0:
        moved = False
        for i, (_, g) in enumerate(groups):
            if left > 0 and quota[i] < len(g):
                quota[i] += 1
                left -= 1
                moved = True
        if not moved:
            break
    if closeups and left > 0:
        n_close = min(len(closeups), n_close + int(left * clip_len // close_len))

    picked = []
    for (_, g), q in zip(groups, quota):
        picked += pick_even(g, q)
    return picked, pick_even(closeups, n_close)


STABILIZE_IDS = set()
MODEL_BY_PATH = {}        # 介面模式：影片路徑 → 模特名字（取代從檔名解析）
LAYOUT_FONT_PATHS = {}    # 版型文字 → 本機字型檔（例如 C:/WINDOWS/Fonts/modern.fon）
LAYOUT_EFFECTS = {}       # 片段 id → [CapCut 特效設定]（版型底圖層的特效）
LAYOUT_ADJUSTS = {}       # 片段 id → [調節項目]（例：顆粒 particle），寫法照 CapCut 自己存的 materials.effects


def maybe_stabilize(mat, full, start, dur, how):
    """判斷這段要不要開穩定；要的話記下素材 id，存檔後寫進草稿"""
    if not AUTO_STABILIZE or vision is None:
        return how
    try:
        pct = vision.shake_percent(full, start / SEC, dur / SEC)
    except Exception:
        pct = None
    if pct is None:
        return how
    if pct >= SHAKE_THRESHOLD:
        STABILIZE_IDS.add(mat.material_id)
        return how + f"，穩定(抖{pct:.1f}%)"
    return how


def build(product_dir, product, models):
    clips, closeups, bgm = [], [], None
    for f in sorted(os.listdir(product_dir)):
        low = f.lower()
        full = os.path.join(product_dir, f)
        if low.endswith(AUDIO_EXT):
            if bgm is None or low.startswith("bgm"):
                bgm = full
        elif low.endswith(VIDEO_EXT):
            if "近拍" in f:
                closeups.append((f, full))
            else:
                clips.append((f, full))

    if not clips and not closeups:
        print(f"  略過「{product}」：沒有影片")
        return False

    clips = sort_clips(clips, models)
    closeups.sort(key=lambda x: parse_clip(x[0])[1])
    STABILIZE_IDS.clear()

    draft_folder = cc.DraftFolder(DRAFT_FOLDER)
    script = draft_folder.create_draft(product, WIDTH, HEIGHT, allow_replace=True)
    script.add_track(cc.TrackType.video, "影片")
    script.add_track(cc.TrackType.text, "MODEL卡", relative_index=1)
    script.add_track(cc.TrackType.text, "商品名稱", relative_index=2)
    script.add_track(cc.TrackType.text, "網址", relative_index=3)

    card_style = TextStyle(size=5.5, bold=True, color=(0.0, 0.0, 0.0), align=1, line_spacing=2)
    card_bg = TextBackground(color="#FFFFFF", alpha=1.0, height=0.25, width=0.35)

    beats, bpm, music_start = None, None, 0
    if bgm and BEAT_SYNC:
        beats, bpm = detect_beats(bgm)
        if beats:
            si, how = music_start_index(beats, bgm, MAX_TOTAL_SEC)
            music_start = beats[si]
            beats = [b - music_start for b in beats[si:]]
            mm, ss = divmod(int(music_start), 60)
            print(f"  音樂從 {mm}:{ss:02d} 開始（{how}）")
            beat_len = 60 / bpm
            print(f"\n  音樂 {bpm:.0f} BPM，已自動卡點")
        else:
            print("\n  ⚠ 卡點失敗，改用固定秒數")
    beat_idx = 0

    if beats:
        clip_len = beats_for(CLIP_SEC, beat_len) * beat_len
        close_len = beats_for(CLOSEUP_SEC, beat_len) * beat_len
    else:
        clip_len, close_len = CLIP_SEC, CLOSEUP_SEC
    total_clips, total_close = len(clips), len(closeups)
    clips, closeups = select_clips(clips, closeups, clip_len, close_len)
    skipped = total_clips + total_close - len(clips) - len(closeups)
    if skipped:
        print(f"  為控制在 {MAX_TOTAL_SEC:.0f} 秒內，略過 {skipped} 段")

    def length_for(mat, target_sec, skip, sec_range=None):
        """有卡點：長度對齊到下一個拍點；沒有：固定秒數。sec_range=(最短,最長) 時挑落在範圍內的拍數"""
        nonlocal beat_idx
        avail = mat.duration - skip
        if not beats:
            return min(int(target_sec * SEC), avail)
        nb = beats_for(target_sec, beat_len)
        if sec_range:
            lo, hi = sec_range
            fits = [k for k in range(1, 9) if lo <= k * beat_len <= hi]
            nb = fits[0] if fits else min(range(1, 9), key=lambda k: abs(k * beat_len - (lo + hi) / 2))
        while nb >= 1:
            end_idx = beat_idx + nb
            if end_idx < len(beats):
                dur = int((beats[end_idx] - beats[beat_idx]) * SEC)
                if dur <= avail:
                    beat_idx = end_idx
                    return dur
            nb //= 2
        return min(int(target_sec * SEC), avail)

    cursor = 0
    warnings = []
    card_runs = []   # [文字, 開始, 長度]，同一張卡連續出現就合併

    print(f"\n  {product}")

    # 排列：前面的模特 → 近拍 → 最後一位模特 → 剩下的近拍
    groups = []
    for f, full in clips:
        name = (parse_clip(f)[2] or [""])[0].upper()
        if groups and groups[-1][0] == name:
            groups[-1][1].append((f, full))
        else:
            groups.append((name, [(f, full)]))
    close_queue = list(closeups)
    sequence = []
    for gi, (_, g) in enumerate(groups):
        if gi == len(groups) - 1 and gi > 0 and close_queue:
            sequence.append(("close_mid",) + close_queue.pop(0))
        sequence += [("clip", f, full) for f, full in g]
    sequence += [("close",) + c for c in close_queue]

    close_runs = []  # [開始, 長度]
    for kind, f, full in sequence:
        mat = cc.VideoMaterial(full)
        if kind in ("close", "close_mid"):
            dur = length_for(mat, CLOSEUP_SEC, 0, MID_CLOSEUP_RANGE if kind == "close_mid" else None)
            info = analyze_clip(full) if AUTO_BEST_SEGMENT else None
            start, how = choose_start(f, full, mat, dur, 0, info=info, want_person=False)
            seg = cc.VideoSegment(mat, cc.Timerange(cursor, dur), source_timerange=cc.Timerange(start, dur), volume=0.0)
            seg.add_keyframe(KeyframeProperty.uniform_scale, 0, 1.0)
            seg.add_keyframe(KeyframeProperty.uniform_scale, dur, ZOOM_SCALE)
            how = maybe_stabilize(mat, full, start, dur, how)
            tgt = zoom_target(info, start / SEC, (start + dur) / SEC)
            if tgt:
                tx, ty = tgt
                px = max(-(ZOOM_SCALE - 1), min(ZOOM_SCALE - 1, -(2 * tx - 1) * ZOOM_SCALE))
                py = max(-(ZOOM_SCALE - 1), min(ZOOM_SCALE - 1, (2 * ty - 1) * ZOOM_SCALE))
                seg.add_keyframe(KeyframeProperty.position_x, 0, 0.0)
                seg.add_keyframe(KeyframeProperty.position_x, dur, px)
                seg.add_keyframe(KeyframeProperty.position_y, 0, 0.0)
                seg.add_keyframe(KeyframeProperty.position_y, dur, py)
                how += f"，放大到({tx:.1f},{ty:.1f})"
            script.add_segment(seg, "影片")
            print(f"    {f}  從 {start / SEC:.1f}s 取 {dur / SEC:.1f}s（{how}，近拍）")
            if close_runs and close_runs[-1][0] + close_runs[-1][1] == cursor:
                close_runs[-1][1] += dur
            else:
                close_runs.append([cursor, dur])
            cursor += dur
            continue

        _, _, parts = parse_clip(f)
        skip, _ = cut(mat, CLIP_SEC, CLIP_SKIP)
        dur = length_for(mat, CLIP_SEC, skip)
        info = analyze_clip(full) if AUTO_BEST_SEGMENT else None
        skip, how = choose_start(f, full, mat, dur, skip, info=info)
        how = maybe_stabilize(mat, full, skip, dur, how)
        cs = center_settings(info, skip / SEC, (skip + dur) / SEC)
        clip_settings = None
        if cs:
            sc, tx = cs
            clip_settings = ClipSettings(scale_x=sc, scale_y=sc, transform_x=tx)
            how += f"，置中（放大{sc:.2f}）"
        script.add_segment(cc.VideoSegment(mat, cc.Timerange(cursor, dur),
                                           source_timerange=cc.Timerange(skip, dur), volume=0.0,
                                           clip_settings=clip_settings), "影片")
        print(f"    {f}  從 {skip / SEC:.1f}s 取 {dur / SEC:.1f}s（{how}）")

        model = models.get(parts[0].upper()) if parts else None
        if model is None:
            warnings.append(f"「{f}」的模特不在 模特資料.csv，沒加 MODEL 卡")
        else:
            size = size_of(parts)
            fit = parts[3] if len(parts) > 3 else FIT_DEFAULT
            text = card_text(model, size, fit)
            if card_runs and card_runs[-1][0] == text and card_runs[-1][1] + card_runs[-1][2] == cursor:
                card_runs[-1][2] += dur
            else:
                card_runs.append([text, cursor, dur])
        cursor += dur

    for text, start, dur in card_runs:
        script.add_segment(cc.TextSegment(text, cc.Timerange(start, dur), style=card_style,
                                          background=card_bg,
                                          clip_settings=ClipSettings(transform_y=CARD_Y)), "MODEL卡")

    if close_runs:
        script.add_track(cc.TrackType.text, "近拍標示", relative_index=4)
        for start, dur in close_runs:
            script.add_segment(cc.TextSegment("- CLOSE UP DETAIL -", cc.Timerange(start, dur),
                                              style=TextStyle(size=5.0, bold=True, align=1, letter_spacing=2),
                                              clip_settings=ClipSettings(transform_y=CLOSEUP_Y)), "近拍標示")

    # 仿文字預設組8：白底黑字、單行
    title_size = min(5.0, 5.0 * 30 / max(len(product), 1))
    script.add_segment(cc.TextSegment(product, cc.Timerange(0, cursor),
                                      style=TextStyle(size=title_size, color=(0.0, 0.0, 0.0), align=1),
                                      background=TextBackground(color="#FFFFFF", alpha=1.0, height=0.2, width=0.12),
                                      clip_settings=ClipSettings(transform_y=TITLE_Y)), "商品名稱")
    script.add_segment(cc.TextSegment(SITE_TEXT, cc.Timerange(0, cursor),
                                      style=TextStyle(size=4.0, bold=True, align=1, letter_spacing=2),
                                      clip_settings=ClipSettings(transform_y=SITE_Y)), "網址")

    if bgm:
        amat = cc.AudioMaterial(bgm)
        mstart = int(music_start * SEC)
        alen = min(cursor, amat.duration - mstart)
        aseg = cc.AudioSegment(amat, cc.Timerange(0, alen), source_timerange=cc.Timerange(mstart, alen), volume=0.8)
        aseg.add_fade(f"{MUSIC_FADE_IN}s", "1s")
        script.add_track(cc.TrackType.audio, "BGM")
        script.add_segment(aseg, "BGM")

    script.save()
    try:
        for n in apply_presets(product):
            print(f"    ・{n}")
    except Exception as e:
        print(f"    ⚠ 套用預設組失敗：{type(e).__name__}: {e}")
    print(f"  ✔ 完成：{len(clips)} 段實穿＋{len(closeups)} 段近拍，共 {cursor / SEC:.1f} 秒")
    for w in warnings:
        print(f"    ⚠ {w}")
    return True


# ---------- 套用 CapCut 文字預設組與臉部潤飾 ----------

def new_id():
    return str(uuid.uuid4()).upper()


def load_beauty():
    """每位模特的臉部潤飾：有「實穿模板」就從模板讀並更新 潤飾.json；沒有模板就讀 潤飾.json"""
    path = os.path.join(DRAFT_FOLDER, TEMPLATE_DRAFT, "draft_content.json")
    beauty = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            effects = {e["id"]: e for e in d["materials"].get("effects", [])}
            videos = {v["id"]: v for v in d["materials"].get("videos", [])}
            for t in d["tracks"]:
                if t["type"] != "video":
                    continue
                for seg in t["segments"]:
                    fx = [effects[r] for r in seg.get("extra_material_refs", []) if r in effects]
                    # 只有一個空的 auto_beauty 代表潤飾沒套成功（例如偵測不到臉），略過
                    if not fx or not any(e.get("name") for e in fx):
                        continue
                    v = videos.get(seg["material_id"], {})
                    name = v.get("material_name") or os.path.basename(v.get("path", ""))
                    parts = parse_clip(name)[2]
                    if parts and parts[0].upper() not in beauty:
                        beauty[parts[0].upper()] = {
                            "effects": fx,
                            "preset_infos": v.get("beauty_face_preset_infos", []),
                            "check_flag": v.get("check_flag"),
                        }
        except Exception:
            beauty = {}
    if beauty:
        # 模板裡有的就更新到 潤飾.json，沒有的保留舊資料
        saved = {}
        if os.path.exists(BEAUTY_FILE):
            try:
                with open(BEAUTY_FILE, encoding="utf-8") as f:
                    saved = json.load(f)
            except Exception:
                saved = {}
        saved.update(beauty)
        try:
            with open(BEAUTY_FILE, "w", encoding="utf-8") as f:
                json.dump(saved, f, ensure_ascii=False)
        except Exception:
            pass
        return saved
    if os.path.exists(BEAUTY_FILE):
        try:
            with open(BEAUTY_FILE, encoding="utf-8") as f:
                return {k.upper(): v for k, v in json.load(f).items()}
        except Exception:
            return {}
    return {}


def text_role(text):
    if text.startswith("MODEL"):
        return "model"
    if "CLOSE UP" in text:
        return "close"
    if text == SITE_TEXT:
        return "site"
    return "title"


def apply_presets(draft_name):
    path = os.path.join(DRAFT_FOLDER, draft_name, "draft_content.json")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    m = d["materials"]
    notes = []

    # 文字預設組
    styles = {}
    if os.path.exists(STYLE_FILE):
        with open(STYLE_FILE, encoding="utf-8") as f:
            styles = json.load(f)
    if styles:
        m.setdefault("material_animations", [])
        segs = {s["material_id"]: s for t in d["tracks"] if t["type"] == "text" for s in t["segments"]}
        # 版型自己的文字（LOOKBOOK、DETAIL…）保持版型給的樣式，不套實穿模板
        layout_text = {s["material_id"] for t in d["tracks"]
                       if t["type"] == "text" and str(t.get("name", "")).startswith("版型文字") for s in t["segments"]}
        keep_pos = {s["material_id"] for t in d["tracks"]
                    if t["type"] == "text" and str(t.get("name", "")).startswith("網址版型") for s in t["segments"]}
        model_pos = {k.upper(): v for k, v in styles.get("model_positions", {}).items()}
        pos_cycle = list(model_pos.values())
        model_count = 0
        for i, mat in enumerate(m.get("texts", [])):
            if mat["id"] in layout_text:
                content = json.loads(mat["content"])
                fp = LAYOUT_FONT_PATHS.get(content.get("text", ""))
                if fp:
                    # 用 CapCut 自己存過的文字素材當底（欄位齊全，本機字型才會被正確載入），再套上這段的字型／字級／顏色
                    tpl_path = LAYOUT_TEXT_TEMPLATE
                    if os.path.exists(tpl_path):
                        with open(tpl_path, encoding="utf-8") as f:
                            tpl = json.load(f)
                        keep = {k: mat.get(k) for k in ("id", "letter_spacing", "line_spacing", "alignment", "typesetting")}
                        st0 = content["styles"][0]
                        tpl["font_size"] = float(st0.get("size", tpl.get("font_size", 16)))
                        tpl["content"] = json.dumps({"text": content["text"], "styles": [{
                            "fill": st0.get("fill"), "font": {"path": fp, "id": ""},
                            "size": st0.get("size"), "bold": bool(st0.get("bold")),
                            "range": [0, len(content["text"].encode("utf-16-le")) // 2]}]}, ensure_ascii=False)
                        tpl.update({k: v for k, v in keep.items() if v is not None})
                        tpl["font_path"] = fp
                        m["texts"][i] = tpl
                    else:
                        mat["font_path"] = fp
                        mat["font_name"] = ""
                        mat["font_title"] = "none"
                        mat["font_id"] = mat["font_resource_id"] = ""
                        for st in content.get("styles", []):
                            st["font"] = {"path": fp, "id": ""}
                        mat["content"] = json.dumps(content, ensure_ascii=False)
                continue
            text = json.loads(mat["content"])["text"]
            role = text_role(text)
            src = styles.get(role)
            if not src:
                continue
            new = copy.deepcopy(src["material"])
            new["id"] = mat["id"]
            content = json.loads(new["content"])
            content["text"] = text
            n16 = len(text.encode("utf-16-le")) // 2
            first = content["styles"][0]
            first["range"] = [0, n16]
            content["styles"] = [first]
            new["content"] = json.dumps(content, ensure_ascii=False)
            m["texts"][i] = new

            seg = segs.get(mat["id"])
            if seg:
                clip = copy.deepcopy(src["clip"])
                if role == "close":
                    clip["transform"]["x"], clip["transform"]["y"] = 0.0, 0.0
                elif role == "model" and pos_cycle:
                    # 每位模特的卡片位置照「實穿模板」；模板沒有這位就輪流用模板裡的位置
                    name = text.split("\n")[0].replace("MODEL", "").strip().upper()
                    clip = copy.deepcopy(model_pos.get(name) or pos_cycle[model_count % len(pos_cycle)])
                    model_count += 1
                if mat["id"] in keep_pos:
                    clip["transform"] = copy.deepcopy(seg["clip"]["transform"])   # 版型段的網址位置照版型給的
                seg["clip"] = clip      # 其他位置照「實穿模板」裡擺的
                for a in src.get("animations", []):
                    a2 = copy.deepcopy(a)
                    a2["id"] = new_id()
                    m["material_animations"].append(a2)
                    seg.setdefault("extra_material_refs", []).append(a2["id"])
    else:
        notes.append("找不到 樣式.json，文字維持預設樣式")

    # 版型層的特效（寫法照 CapCut 自己存的 video_effects 素材）
    if LAYOUT_EFFECTS:
        m.setdefault("video_effects", [])
        for t in d["tracks"]:
            if t["type"] != "video":
                continue
            for seg in t["segments"]:
                for fx in LAYOUT_EFFECTS.get(seg["id"], []):
                    fid = new_id().upper()
                    m["video_effects"].append({
                        "id": fid, "effect_id": fx["effect_id"], "resource_id": fx["effect_id"], "name": fx["name"],
                        "type": "video_effect", "sub_type": 0, "bind_segment_id": "", "transparent_params": "", "path": "",
                        "value": 1.0, "category_id": fx.get("category_id", ""), "category_name": fx.get("category_name", ""),
                        "platform": "all", "apply_target_type": 0, "source_platform": 1, "version": "", "item_effect_type": 0,
                        "adjust_params": [{"name": n, "value": v, "default_value": dv} for n, v, dv in fx.get("params", [])],
                        "time_range": None, "formula_id": "", "apply_time_range": None, "render_index": 11000,
                        "track_render_index": 0, "common_keyframes": [], "request_id": "", "algorithm_artifact_path": "",
                        "disable_effect_faces": [], "covering_relation_change": 0, "enable_mask": True, "effect_mask": [],
                        "enable_video_mask_stroke": True, "enable_video_mask_shadow": True,
                    })
                    seg.setdefault("extra_material_refs", []).append(fid)

    # 版型層的「調節」（顆粒等）：寫進 materials.effects，跟 CapCut 手動調的寫法一樣
    if LAYOUT_ADJUSTS:
        m.setdefault("effects", [])
        for t in d["tracks"]:
            if t["type"] != "video":
                continue
            for seg in t["segments"]:
                for adj in LAYOUT_ADJUSTS.get(seg["id"], []):
                    fid = new_id().upper()
                    m["effects"].append({
                        "id": fid, "effect_id": adj["effect_id"], "resource_id": adj["effect_id"], "third_resource_id": "",
                        "name": "", "report_name": "", "type": adj["type"], "sub_type": "none", "path": "",
                        "value": float(adj.get("value", 0)), "visible": True, "item_effect_type": 0,
                        "category_id": "", "category_name": "", "category_key": "", "sub_category_id": "", "sub_category_name": "",
                        "platform": "all", "apply_target_type": 0, "source_platform": 1, "version": "v2", "adjust_params": [],
                        "time_range": None, "formula_id": "", "enable_skin_tone_correction": False, "algorithm_artifact_path": "",
                        "intensity_key": "", "face_adjust_params": [], "exclusion_group": [], "panel_id": "", "bloom_params": None,
                        "request_id": "", "color_match_info": {"target_feature_path": "", "source_feature_path": "", "target_image_path": ""},
                        "multi_language_current": "", "lumi_hub_path": "", "covering_relation_change": 0,
                        "beauty_face_auto_preset_id": "", "beauty_body_auto_preset_id": "",
                        "beauty_face_auto_retouch_info": {"face_id": [], "beauty_face_auto_retouch_id": ""},
                        "smart_color_mode": 0, "is_from_intelligent_quality": False,
                    })
                    seg.setdefault("extra_material_refs", []).append(fid)
                    seg["enable_adjust"] = True

    # 原影片靜音（跟 CapCut 手動「關閉原聲」一樣的寫法）
    d.setdefault("config", {})["video_mute"] = True
    for t in d["tracks"]:
        if t["type"] == "video":
            t["attribute"] = 1
            for seg in t["segments"]:
                seg["volume"] = 0.0

    # 穩定
    n_stab = 0
    for v in m.get("videos", []):
        if v["id"] in STABILIZE_IDS:
            st = v.get("stable") or {}
            st.update({"stable_level": STABLE_LEVEL, "matrix_path": st.get("matrix_path", ""),
                       "time_range": st.get("time_range", {"start": 0, "duration": 0})})
            v["stable"] = st
            n_stab += 1
    if n_stab:
        notes.append(f"已開穩定：{n_stab} 段")

    # 臉部潤飾
    beauty = load_beauty()
    videos = {v["id"]: v for v in m.get("videos", [])}
    m.setdefault("effects", [])
    done, missing = set(), set()
    for t in d["tracks"]:
        if t["type"] != "video":
            continue
        for seg in t["segments"]:
            v = videos.get(seg["material_id"], {})
            fname = v.get("material_name") or os.path.basename(v.get("path", ""))
            mapped = MODEL_BY_PATH.get(v.get("path", ""))
            if mapped is None:
                mapped = MODEL_BY_PATH.get(fname)
            if mapped == "":          # 介面標記為近拍／不套
                continue
            if mapped:
                parts = [mapped]
            else:
                if "近拍" in fname:
                    continue
                parts = parse_clip(fname)[2]
                if not parts:
                    continue
            key = parts[0].upper()
            info = beauty.get(key)
            if not info:
                missing.add(parts[0])
                continue
            for e in info["effects"]:
                e2 = copy.deepcopy(e)
                e2["id"] = new_id()
                e2["algorithm_artifact_path"] = ""      # 臉部偵測資料讓 CapCut 自己重算
                m["effects"].append(e2)
                seg.setdefault("extra_material_refs", []).append(e2["id"])
            if info["preset_infos"]:
                v["beauty_face_preset_infos"] = copy.deepcopy(info["preset_infos"])
            if info["check_flag"]:
                v["check_flag"] = info["check_flag"]
            done.add(parts[0])

    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)

    if done:
        notes.append("已套潤飾：" + "、".join(sorted(done)))
    if missing:
        notes.append("沒有潤飾（請在「實穿模板」幫她套一段，或更新 潤飾.json）：" + "、".join(sorted(missing)))
    return notes


def has_videos(folder):
    return any(f.lower().endswith(VIDEO_EXT) for f in os.listdir(folder))


def capcut_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq CapCut.exe"], capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return "capcut.exe" in out.lower()
    except Exception:
        return False


def main():
    print("=== VV DOUBLE 實穿草稿產生器（自動卡點版）===")
    while capcut_running():
        print("\n⚠ CapCut 還開著！開著的話它會蓋掉新草稿。")
        input("  請完全關閉 CapCut（右下角 ^ 也要結束）後按 Enter...")
    if not os.path.isdir(DRAFT_FOLDER):
        fail("找不到 CapCut 草稿資料夾：\n" + DRAFT_FOLDER)
    models = load_models()

    jobs = []
    # 拖曳：把一個或多個商品資料夾拖到 執行.bat 上
    dropped = [a.strip('"') for a in sys.argv[1:] if os.path.isdir(a.strip('"'))]
    if len(dropped) == 1 and has_videos(dropped[0]):
        default = os.path.basename(os.path.normpath(dropped[0]))
        name = input(f"\n商品名稱（直接按 Enter 用「{default}」）：").strip() or default
        jobs.append((dropped[0], name))
    else:
        for d in dropped:
            if has_videos(d):
                jobs.append((d, os.path.basename(os.path.normpath(d))))
    if not jobs and os.path.isdir(PRODUCT_ROOT):
        for d in sorted(os.listdir(PRODUCT_ROOT)):
            p = os.path.join(PRODUCT_ROOT, d)
            if os.path.isdir(p):
                jobs.append((p, d))
    if not jobs and has_videos(HERE):
        default = os.path.basename(HERE)
        name = input(f"\n商品名稱（直接按 Enter 用「{default}」）：").strip() or default
        jobs.append((HERE, name))
    if not jobs:
        fail("找不到影片：請把放影片的資料夾拖到 執行.bat 上")

    done = 0
    for path, name in jobs:
        try:
            if build(path, name, models):
                done += 1
        except Exception as e:
            print(f"  ✘ {name}：{type(e).__name__}: {e}")

    print(f"\n完成 {done} 支草稿，請打開 CapCut 查看")
    input("\n按 Enter 結束...")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")
