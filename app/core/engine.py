# -*- coding: utf-8 -*-
# 引擎：依介面傳入的片段規格產生 CapCut 草稿（沿用 generator 的卡點／置中／穩定／潤飾／預設組）

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pycapcut as cc
from pycapcut import TextStyle, ClipSettings, TextBackground, KeyframeProperty, SEC

import generator as g
try:
    import layouts
except Exception:
    layouts = None

RENDER_DIR = os.path.join(HERE, "renders")     # 版型段合成的 MP4 放這裡（main.py 會改成資料夾）
LAYOUT_CACHE = os.path.join(HERE, "layout_cache")


def _log(cb, msg):
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


def _ffmpeg_duration(path):
    """用 ffmpeg 讀媒體長度（秒）；讀不到回傳 None"""
    import re
    import subprocess
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        r = subprocess.run([exe, "-hide_banner", "-i", path], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        m = re.search(rb"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return None


def _with_timeout(fn, seconds):
    """在背景執行緒跑 fn，超過秒數就放棄（回傳 None）"""
    import threading
    box = {}

    def run():
        try:
            box["v"] = fn()
        except Exception as e:
            box["e"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"超過 {seconds} 秒沒有回應")
    if "e" in box:
        raise box["e"]
    return box.get("v")


def audio_material(path):
    """建立音樂素材：不走 pymediainfo（某些電腦上會卡住），直接用 ffmpeg 抓長度"""
    import uuid
    dur = _with_timeout(lambda: _ffmpeg_duration(path), 40)
    if dur is None:
        raise ValueError("ffmpeg 讀不到音樂長度")
    amat = cc.AudioMaterial.__new__(cc.AudioMaterial)
    amat.path = os.path.abspath(path)
    amat.material_name = os.path.basename(path)
    amat.material_id = uuid.uuid4().hex
    amat.duration = int(round(dur * SEC))
    return amat


def build_draft(spec, models, log=None, progress=None):
    """
    spec = {
      "name": 商品名稱（草稿名稱）,
      "clips": [ {"path", "model": 名字或"", "size_text": "著大碼舒適", "closeup": bool, "start": 秒或None, "order": int} ... ],
      "music": {"path": ..., "start": 秒或None} 或 None,
      "options": {"max_total","mid_closeup","person_center","stabilize","beauty","closeup_share"}
    }
    models = {名字大寫: {"名字","身高","體重"}}
    回傳 {"ok", "name", "total_sec", "segments":[...], "notes":[...], "skipped": n}
    """
    opt = spec.get("options") or {}
    g.MAX_TOTAL_SEC = float(opt.get("max_total", g.MAX_TOTAL_SEC))
    g.PERSON_CENTER = bool(opt.get("person_center", True))
    g.AUTO_STABILIZE = bool(opt.get("stabilize", True))
    mid = opt.get("mid_closeup")
    if mid and len(mid) == 2:
        g.MID_CLOSEUP_RANGE = (float(mid[0]), float(mid[1]))
    beauty_on = bool(opt.get("beauty", True))

    name = (spec.get("name") or "未命名").strip()
    clips_in = sorted(spec.get("clips") or [], key=lambda c: c.get("order", 0))
    model_clips = [c for c in clips_in if not c.get("closeup")]
    closeups = [c for c in clips_in if c.get("closeup")]
    if not model_clips and not closeups:
        return {"ok": False, "error": "沒有影片"}

    music = spec.get("music") or None
    bgm = music.get("path") if music else None
    if bgm and not os.path.exists(bgm):
        bgm = None

    draft_folder = cc.DraftFolder(g.DRAFT_FOLDER)
    script = draft_folder.create_draft(name, g.WIDTH, g.HEIGHT, allow_replace=True)
    script.add_track(cc.TrackType.video, "影片")
    script.add_track(cc.TrackType.text, "MODEL卡", relative_index=1)
    script.add_track(cc.TrackType.text, "商品名稱", relative_index=2)
    script.add_track(cc.TrackType.text, "網址", relative_index=3)

    card_style = TextStyle(size=5.5, bold=True, color=(0.0, 0.0, 0.0), align=1, line_spacing=2)
    card_bg = TextBackground(color="#FFFFFF", alpha=1.0, height=0.25, width=0.35)

    # ---- 音樂 ----
    beats, bpm, music_start = None, None, 0.0
    notes = []
    if bgm and g.BEAT_SYNC:
        beats, bpm = g.detect_beats(bgm)
        if beats:
            want = music.get("start")
            if want is not None:
                si = min(range(len(beats)), key=lambda i: abs(beats[i] - float(want)))
                how = "指定"
            else:
                si, how = g.music_start_index(beats, bgm, g.MAX_TOTAL_SEC)
            music_start = beats[si]
            beats = [b - music_start for b in beats[si:]]
            beat_len = 60 / bpm
            mm, ss = divmod(int(music_start), 60)
            _log(log, f"音樂 {bpm:.0f} BPM，從 {mm}:{ss:02d} 開始（{how}）")
            notes.append({"bpm": round(bpm), "music_start": round(music_start, 1)})
        else:
            _log(log, "卡點失敗，改用固定秒數")
    beat_idx = 0

    def length_for(mat, target_sec, skip, sec_range=None):
        nonlocal beat_idx
        avail = mat.duration - skip
        if not beats:
            return min(int(target_sec * SEC), avail)
        nb = g.beats_for(target_sec, beat_len)
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

    # ---- 控制總長：每位模特平均、近拍約三成 ----
    if beats:
        clip_len = g.beats_for(g.CLIP_SEC, beat_len) * beat_len
        close_len = g.beats_for(g.CLOSEUP_SEC, beat_len) * beat_len
    else:
        clip_len, close_len = g.CLIP_SEC, g.CLOSEUP_SEC
    pairs = [(c.get("path"), c) for c in model_clips]
    cl_pairs = [(c.get("path"), c) for c in closeups]
    # select_clips 需要用 parse_clip 認模特分組；這裡改用介面給的 model 分組
    groups = []
    for path, c in pairs:
        key = (c.get("model") or "").upper()
        if groups and groups[-1][0] == key:
            groups[-1][1].append((path, c))
        else:
            groups.append((key, [(path, c)]))
    n_close = min(len(cl_pairs), max(1, round(g.MAX_TOTAL_SEC * g.CLOSEUP_SHARE / close_len))) if cl_pairs else 0
    budget = g.MAX_TOTAL_SEC - n_close * close_len
    n_clip = max(len(groups), int(budget // clip_len)) if groups else 0
    quota = [0] * len(groups)
    left = n_clip
    while left > 0:
        moved = False
        for i, (_, grp) in enumerate(groups):
            if left > 0 and quota[i] < len(grp):
                quota[i] += 1
                left -= 1
                moved = True
        if not moved:
            break
    if cl_pairs and left > 0:
        n_close = min(len(cl_pairs), n_close + int(left * clip_len // close_len))
    picked_groups = [(k, g.pick_even(grp, q)) for (k, grp), q in zip(groups, quota)]
    picked_close = g.pick_even(cl_pairs, n_close)
    total_in = len(pairs) + len(cl_pairs)
    skipped = total_in - sum(len(x[1]) for x in picked_groups) - len(picked_close)
    if skipped:
        _log(log, f"為控制在 {g.MAX_TOTAL_SEC:.0f} 秒內，略過 {skipped} 段")

    # ---- 版型：照版型的固定順序排（#＝版型段、M＝滿版模特、D＝滿版近拍） ----
    layout_id = (spec.get("layout") or "").strip()
    L = layouts.LAYOUTS.get(layout_id) if (layouts and layout_id) else None
    layout_meta = None
    if L:
        try:
            layout_meta = layouts.prepare(layout_id, LAYOUT_CACHE)
        except Exception as e:
            _log(log, f"版型「{L['name']}」準備失敗（{type(e).__name__}: {e}），改用標準排法")
            L = None
    if L:
        pattern = L["pattern"].split(",")
        n_m, n_d = pattern.count("M"), pattern.count("D")
        quota = [0] * len(groups)
        left = n_m
        while left > 0 and groups:
            moved = False
            for i, (_, grp) in enumerate(groups):
                if left > 0 and quota[i] < len(grp):
                    quota[i] += 1
                    left -= 1
                    moved = True
            if not moved:
                break
        picked_groups = [(k, g.pick_even(grp, q)) for (k, grp), q in zip(groups, quota)]
        m_slots = [item for _, grp in picked_groups for item in grp]
        d_slots = g.pick_even(cl_pairs, min(n_d, len(cl_pairs)))
        used = set(p for p, _ in m_slots) | set(p for p, _ in d_slots)
        skipped = total_in - len(used)
        sequence = []
        mi = di = 0
        for tok in pattern:
            if tok == "#":
                sequence.append(("layout", None, None))
            elif tok == "M" and m_slots:
                sequence.append(("clip",) + m_slots[mi % len(m_slots)])
                mi += 1
            elif tok == "D" and d_slots:
                sequence.append(("close",) + d_slots[di % len(d_slots)])
                di += 1
        _log(log, f"版型「{L['name']}」：{' → '.join('版型' if t == '#' else ('滿版' if t == 'M' else '近拍') for t in pattern)}")
    else:
        # ---- 排列：前面模特 → 近拍 → 最後一位模特 → 剩下近拍 ----
        close_queue = list(picked_close)
        sequence = []
        for gi, (_, grp) in enumerate(picked_groups):
            if gi == len(picked_groups) - 1 and gi > 0 and close_queue:
                sequence.append(("close_mid",) + close_queue.pop(0))
            sequence += [("clip", p, c) for p, c in grp]
        sequence += [("close",) + c for c in close_queue]

    used_starts = {}    # 同一支影片用第二次時，從上一段的結尾接著取

    def next_start(full, start, dur):
        used_starts.setdefault(full, []).append((start, dur))

    def reuse_start(full, mat, dur):
        """這支影片前面用過：回傳接在上一段後面的開始點（放得下才回傳）"""
        runs = used_starts.get(full)
        if not runs:
            return None
        s0, d0 = runs[-1]
        if s0 + d0 + dur <= mat.duration:
            return s0 + d0
        return None

    def length_fixed(target_sec):
        nonlocal beat_idx
        if not beats:
            return int(target_sec * SEC)
        nb = g.beats_for(target_sec, beat_len)
        while nb >= 1:
            end_idx = beat_idx + nb
            if end_idx < len(beats):
                dur = int((beats[end_idx] - beats[beat_idx]) * SEC)
                beat_idx = end_idx
                return dur
            nb //= 2
        return int(target_sec * SEC)

    def layout_source(full, c, dur, want_person=True):
        """挑這支影片要給版型區塊的開始秒數與人物位置"""
        mat = cc.VideoMaterial(full)
        d = min(dur, mat.duration)
        manual = c.get("start")
        info = g.analyze_clip(full) if g.AUTO_BEST_SEGMENT else None
        if manual is not None:
            start = int(max(0.0, min(float(manual), mat.duration / SEC - d / SEC)) * SEC)
        else:
            start = reuse_start(full, mat, d)
            if start is None:
                start, _ = g.choose_start(os.path.basename(full), full, mat, d, 0, info=info, want_person=want_person)
        next_start(full, start, d)
        focus_x = 0.5
        if want_person and info is not None and info.get("people") and g.vision is not None:
            off = g.vision.person_offset(info["people"], info["times"], start / SEC, (start + d) / SEC)
            if off is not None:
                focus_x = max(0.0, min(1.0, 0.5 + off))
        return {"path": full, "start": start / SEC, "focus_x": focus_x, "focus_y": 0.3 if want_person else 0.5}

    layout_tracks = {}      # 版型用到的額外軌道（建一次就好）

    def layout_track(kind, name, rel):
        if name not in layout_tracks:
            script.add_track(kind, name, relative_index=rel)
            layout_tracks[name] = True
        return name

    def build_layout_segment(idx, dur, t0):
        """版型段：每個區塊一層（裁切＋縮放到框位）、框線一層、文字一層，全部在 CapCut 裡可以再調；回傳用到的模特名字"""
        grp_clips = [grp for _, grp in picked_groups if grp]
        m1 = grp_clips[0][0] if grp_clips else None
        m2 = grp_clips[1][0] if len(grp_clips) > 1 else (grp_clips[0][1] if grp_clips and len(grp_clips[0]) > 1 else m1)
        d1 = cl_pairs[0] if cl_pairs else m1
        d2 = (cl_pairs[1] if len(cl_pairs) > 1 else d1) if cl_pairs else m2
        by_role = {"M1": (m1, True), "M2": (m2, True), "D": (d1, not cl_pairs), "D2": (d2, not cl_pairs)}
        photo = (spec.get("background") or "").strip()
        os.makedirs(RENDER_DIR, exist_ok=True)
        safe = "".join(ch for ch in name if ch not in '\\/:*?"<>|')[:40]
        layer_no = 0
        for reg in layout_meta["regions"]:
            role, box = reg["role"], reg["box"]
            if role == "P":
                if not (photo and os.path.exists(photo)):
                    _log(log, "這個版型需要底圖，沒選底圖的話那一塊會是空的")
                    continue
                if reg.get("raw"):
                    # 原圖直接鋪滿畫布再放大一點（照 CapCut 裡調好的數值）
                    mat = cc.VideoMaterial(photo)
                    scale = layouts.cover_scale(mat.width, mat.height) * float(reg.get("zoom", 1.0))
                    tx, ty = reg.get("offset", [0, 0])
                else:
                    # 檔名帶內容雜湊：同一張底圖重做不會覆蓋舊檔（CapCut 已開的草稿若底下的檔被換掉會讀不到）
                    import hashlib
                    key = hashlib.md5(f"{photo}|{os.path.getmtime(photo)}|{box}|{reg.get('blur', 0)}|{reg.get('dark', 0)}".encode("utf-8")).hexdigest()[:10]
                    png = os.path.join(RENDER_DIR, f"{safe}_{layout_id}_bg_{key}.png")
                    if not os.path.exists(png):
                        layouts.background_photo(photo, box, png, reg.get("blur", 0), reg.get("dark", 0))
                    mat = cc.VideoMaterial(png)
                    crop, scale, tx, ty = layouts.place(box, box[2] - box[0], box[3] - box[1])
                seg = cc.VideoSegment(mat, cc.Timerange(t0, dur),
                                      clip_settings=ClipSettings(scale_x=scale, scale_y=scale, transform_x=tx, transform_y=ty))
                tname = "影片" if layer_no == 0 else layout_track(cc.TrackType.video, f"版型層{layer_no + 1}", layer_no)
            else:
                pair, want_person = by_role.get(role, (None, True))
                if not pair:
                    continue
                full, c = pair
                if want_person:
                    mn = c.get("model") or ""
                    g.MODEL_BY_PATH.setdefault(full, mn.upper() if (mn and beauty_on) else "")
                s = layout_source(full, c, dur, want_person=want_person)
                if not reg.get("rect", True):
                    # 區塊有弧度（撕紙）：先照遮罩形狀裁成透明 .mov，放進 CapCut 仍是獨立一層
                    import hashlib, time as _t
                    mkey = hashlib.md5(f"{full}|{s['start']:.3f}|{dur}|{box}|{_t.time():.0f}".encode("utf-8")).hexdigest()[:10]
                    mov = os.path.join(RENDER_DIR, f"{safe}_{layout_id}_{idx}_{layer_no}_{mkey}.mov")   # 每次新檔，不覆蓋舊草稿引用的
                    layouts.render_alpha_clip(full, s["start"], dur / SEC, box, os.path.join(layout_meta["dir"], reg["mask"]),
                                              mov, s["focus_x"], s["focus_y"])
                    mat = cc.VideoMaterial(mov)
                    d = min(dur, mat.duration)
                    seg = cc.VideoSegment(mat, cc.Timerange(t0, d), source_timerange=cc.Timerange(0, d), volume=0.0)
                    tname = "影片" if layer_no == 0 else layout_track(cc.TrackType.video, f"版型層{layer_no + 1}", layer_no)
                    script.add_segment(seg, tname)
                    for adj in reg.get("adjusts") or []:
                        g.LAYOUT_ADJUSTS.setdefault(seg.segment_id, []).append(adj)
                    layer_no += 1
                    continue
                sw, sh = layouts.probe(full)
                crop, scale, tx, ty = layouts.place(box, sw, sh, s["focus_x"], s["focus_y"])
                scale *= float(reg.get("zoom", 1.0) or 1.0)
                ox, oy = reg.get("offset") or [0, 0]
                tx, ty = tx + float(ox), ty + float(oy)
                mat = cc.VideoMaterial(full, crop_settings=cc.CropSettings(**crop))
                st = int(s["start"] * SEC)
                d = min(dur, mat.duration - st)
                seg = cc.VideoSegment(mat, cc.Timerange(t0, d), source_timerange=cc.Timerange(st, d), volume=0.0,
                                      clip_settings=ClipSettings(scale_x=scale, scale_y=scale, transform_x=tx, transform_y=ty))
                tname = "影片" if layer_no == 0 else layout_track(cc.TrackType.video, f"版型層{layer_no + 1}", layer_no)
                if reg.get("mask") == "stamp":
                    import stamp_overlay
                    mk = stamp_overlay.video_mask(float(layout_meta.get("overlay_scale") or 1.0), float(reg.get("mask_video_scale") or scale))
                    seg.add_mask(cc.MaskType.矩形, center_x=mk["center_x"], center_y=mk["center_y"],
                                 size=mk["size"], rect_width=mk["rect_width"], rotation=mk["rotation"])
            script.add_segment(seg, tname)
            for fx in reg.get("effects") or []:
                g.LAYOUT_EFFECTS.setdefault(seg.segment_id, []).append(fx)
            for adj in reg.get("adjusts") or []:
                g.LAYOUT_ADJUSTS.setdefault(seg.segment_id, []).append(adj)
            layer_no += 1
        # 框線／裝飾（設計稿去掉色塊和文字後的部分）
        if layout_meta.get("overlay"):
            ov = cc.VideoMaterial(os.path.join(layout_meta["dir"], layout_meta["overlay"]))
            oox, ooy = layout_meta.get("overlay_offset") or [0, 0]
            osc = float(layout_meta.get("overlay_scale") or 1.0)
            script.add_segment(cc.VideoSegment(ov, cc.Timerange(t0, dur),
                                               clip_settings=ClipSettings(transform_x=float(oox), transform_y=float(ooy),
                                                                          scale_x=osc, scale_y=osc)),
                               layout_track(cc.TrackType.video, "版型框", 20))
        # 相片格：裁成框的比例、縮放到框位，再加矩形遮罩（圓角），使用者可在 CapCut 直接換圖或拖遮罩
        th = layout_meta.get("thumb")
        if th:
            pair, want_person = by_role.get(th.get("role", "D"), (None, False))
            if pair:
                full, c = pair
                box = th["box"]
                s = layout_source(full, c, dur, want_person=want_person)
                sw, sh = layouts.probe(full)
                crop, scale, tx, ty = layouts.place(box, sw, sh, s["focus_x"], s["focus_y"])
                mat = cc.VideoMaterial(full, crop_settings=cc.CropSettings(**crop))
                st = int(s["start"] * SEC)
                d = min(dur, mat.duration - st)
                seg = cc.VideoSegment(mat, cc.Timerange(t0, d), source_timerange=cc.Timerange(st, d), volume=0.0,
                                      clip_settings=ClipSettings(scale_x=scale, scale_y=scale, transform_x=tx, transform_y=ty))
                seg.add_mask(cc.MaskType.矩形, size=1.0, rect_width=1.0, round_corner=float(th.get("round_corner", 0)))
                script.add_segment(seg, layout_track(cc.TrackType.video, "版型相片", 21))
        # 文字
        for ti, t in enumerate(layout_meta.get("texts", [])):
            tx, ty = t["pos"] if t.get("pos") else layouts.text_place(t["box"])
            font = getattr(cc.FontType, t["font"], None) if t.get("font") else None
            if t.get("font_path"):
                g.LAYOUT_FONT_PATHS[t["text"]] = t["font_path"]
            style = TextStyle(size=float(t.get("size", 8)), bold=bool(t.get("bold")), color=tuple(t.get("color", (1, 1, 1))),
                              align=int(t.get("align", 1)), letter_spacing=int(t.get("letter_spacing", 0)),
                              line_spacing=int(t.get("line_spacing", 0)))
            script.add_segment(cc.TextSegment(t["text"], cc.Timerange(t0, dur), font=font, style=style,
                                              clip_settings=ClipSettings(transform_x=tx, transform_y=ty,
                                                                         scale_x=float(t.get("scale", 1.0)), scale_y=float(t.get("scale", 1.0)))),
                               layout_track(cc.TrackType.text, "版型文字" + (str(ti + 1) if ti else ""), 5 + ti))
        return ((m1[1].get("model") if m1 else "") or "")

    g.STABILIZE_IDS.clear()
    g.MODEL_BY_PATH.clear()
    g.LAYOUT_FONT_PATHS.clear()
    g.LAYOUT_EFFECTS.clear()
    g.LAYOUT_ADJUSTS.clear()
    layout_windows = []     # [(t0, dur)] 版型段的時間，網址／商品名稱要避開或改位置
    cursor = 0
    card_runs, close_runs, out_segments = [], [], []
    total_steps = max(1, len(sequence))

    for idx, (kind, full, c) in enumerate(sequence):
        fname = os.path.basename(full) if full else f"版型 {L['name']}"
        if progress:
            try:
                progress(idx, total_steps, fname)
            except Exception:
                pass
        if kind == "layout":
            dur = length_fixed(layouts.LAYOUT_SEC)
            _log(log, f"排版型「{L['name']}」{dur / SEC:.1f} 秒（{len(layout_meta['regions'])} 個區塊，各自一層）…")
            m1_name = build_layout_segment(idx, dur, cursor)
            layout_windows.append((cursor, dur))
            out_segments.append({"path": os.path.join(layout_meta["dir"], layout_meta["overlay"]), "name": fname,
                                 "closeup": False, "layout": layout_id, "start": 0, "dur": round(dur / SEC, 2),
                                 "how": "版型", "stabilized": False})
            cursor += dur
            continue
        mat = cc.VideoMaterial(full)
        manual = c.get("start")
        if manual is None and reuse_start(full, mat, int(g.CLIP_SEC * SEC)) is not None and kind != "close_mid":
            manual = reuse_start(full, mat, int(g.CLIP_SEC * SEC)) / SEC
        if kind in ("close", "close_mid"):
            g.MODEL_BY_PATH[full] = ""
            dur = length_for(mat, g.CLOSEUP_SEC, 0, g.MID_CLOSEUP_RANGE if kind == "close_mid" else None)
            info = g.analyze_clip(full) if g.AUTO_BEST_SEGMENT else None
            if manual is not None:
                start = int(max(0.0, min(float(manual), mat.duration / SEC - dur / SEC)) * SEC)
                how = "指定"
            else:
                start, how = g.choose_start(fname, full, mat, dur, 0, info=info, want_person=False)
            seg = cc.VideoSegment(mat, cc.Timerange(cursor, dur), source_timerange=cc.Timerange(start, dur), volume=0.0)
            seg.add_keyframe(KeyframeProperty.uniform_scale, 0, 1.0)
            seg.add_keyframe(KeyframeProperty.uniform_scale, dur, g.ZOOM_SCALE)
            how = g.maybe_stabilize(mat, full, start, dur, how)
            tgt = g.zoom_target(info, start / SEC, (start + dur) / SEC)
            if tgt:
                tx, ty = tgt
                px = max(-(g.ZOOM_SCALE - 1), min(g.ZOOM_SCALE - 1, -(2 * tx - 1) * g.ZOOM_SCALE))
                py = max(-(g.ZOOM_SCALE - 1), min(g.ZOOM_SCALE - 1, (2 * ty - 1) * g.ZOOM_SCALE))
                seg.add_keyframe(KeyframeProperty.position_x, 0, 0.0)
                seg.add_keyframe(KeyframeProperty.position_x, dur, px)
                seg.add_keyframe(KeyframeProperty.position_y, 0, 0.0)
                seg.add_keyframe(KeyframeProperty.position_y, dur, py)
            script.add_segment(seg, "影片")
            if close_runs and close_runs[-1][0] + close_runs[-1][1] == cursor:
                close_runs[-1][1] += dur
            else:
                close_runs.append([cursor, dur])
            out_segments.append({"path": full, "name": fname, "closeup": True, "start": round(start / SEC, 2),
                                 "dur": round(dur / SEC, 2), "how": how,
                                 "stabilized": mat.material_id in g.STABILIZE_IDS})
            _log(log, f"{fname} 從 {start / SEC:.1f}s 取 {dur / SEC:.1f}s（{how}，近拍）")
            next_start(full, start, dur)
            cursor += dur
            continue

        model_name = (c.get("model") or "").strip()
        g.MODEL_BY_PATH[full] = model_name.upper() if (model_name and beauty_on) else ""
        skip, _ = g.cut(mat, g.CLIP_SEC, g.CLIP_SKIP)
        dur = length_for(mat, g.CLIP_SEC, skip)
        info = g.analyze_clip(full) if g.AUTO_BEST_SEGMENT else None
        if manual is not None:
            skip = int(max(0.0, min(float(manual), mat.duration / SEC - dur / SEC)) * SEC)
            how = "指定"
        else:
            skip, how = g.choose_start(fname, full, mat, dur, skip, info=info)
        how = g.maybe_stabilize(mat, full, skip, dur, how)
        cs = g.center_settings(info, skip / SEC, (skip + dur) / SEC)
        clip_settings = None
        scale = 1.0
        if cs:
            scale, tx = cs
            clip_settings = ClipSettings(scale_x=scale, scale_y=scale, transform_x=tx)
            how += f"，置中（放大{scale:.2f}）"
        script.add_segment(cc.VideoSegment(mat, cc.Timerange(cursor, dur),
                                           source_timerange=cc.Timerange(skip, dur), volume=0.0,
                                           clip_settings=clip_settings), "影片")
        model = models.get(model_name.upper()) if model_name else None
        if model:
            size_text = (c.get("size_text") or "").strip()
            text = g.card_text(model, size_text, g.FIT_DEFAULT)
            if card_runs and card_runs[-1][0] == text and card_runs[-1][1] + card_runs[-1][2] == cursor:
                card_runs[-1][2] += dur
            else:
                card_runs.append([text, cursor, dur])
        out_segments.append({"path": full, "name": fname, "closeup": False, "model": model_name,
                             "start": round(skip / SEC, 2), "dur": round(dur / SEC, 2), "how": how,
                             "scale": round(scale, 2), "stabilized": mat.material_id in g.STABILIZE_IDS})
        _log(log, f"{fname} 從 {skip / SEC:.1f}s 取 {dur / SEC:.1f}s（{how}）")
        next_start(full, skip, dur)
        cursor += dur

    _log(log, "加入字卡…")
    for text, start, dur in card_runs:
        script.add_segment(cc.TextSegment(text, cc.Timerange(start, dur), style=card_style, background=card_bg,
                                          clip_settings=ClipSettings(transform_y=g.CARD_Y)), "MODEL卡")
    if close_runs:
        script.add_track(cc.TrackType.text, "近拍標示", relative_index=4)
        for start, dur in close_runs:
            script.add_segment(cc.TextSegment("- CLOSE UP DETAIL -", cc.Timerange(start, dur),
                                              style=TextStyle(size=5.0, bold=True, align=1, letter_spacing=2),
                                              clip_settings=ClipSettings(transform_y=g.CLOSEUP_Y)), "近拍標示")
    title_size = min(5.0, 5.0 * 30 / max(len(name), 1))

    def outside_windows(total, windows):
        """把 0~total 扣掉版型段，回傳剩下的 (start, dur) 片段"""
        pieces, t = [], 0
        for w0, wd in sorted(windows):
            if w0 > t:
                pieces.append((t, w0 - t))
            t = max(t, w0 + wd)
        if total > t:
            pieces.append((t, total - t))
        return pieces

    title_windows = layout_windows if (layout_meta and layout_meta.get("hide_title")) else []
    for st, du in outside_windows(cursor, title_windows):
        script.add_segment(cc.TextSegment(name, cc.Timerange(st, du),
                                          style=TextStyle(size=title_size, color=(0.0, 0.0, 0.0), align=1),
                                          background=TextBackground(color="#FFFFFF", alpha=1.0, height=0.2, width=0.12),
                                          clip_settings=ClipSettings(transform_y=g.TITLE_Y)), "商品名稱")
    site_style = TextStyle(size=4.0, bold=True, align=1, letter_spacing=2)
    site_pos = layout_meta.get("site_pos") if layout_meta else None
    hide_site = bool(layout_meta and layout_meta.get("hide_site"))
    site_windows = layout_windows if (site_pos or hide_site) else []
    for st, du in outside_windows(cursor, site_windows):
        script.add_segment(cc.TextSegment(g.SITE_TEXT, cc.Timerange(st, du), style=site_style,
                                          clip_settings=ClipSettings(transform_y=g.SITE_Y)), "網址")
    if site_windows and site_pos:
        script.add_track(cc.TrackType.text, "網址版型", relative_index=8)   # 版型段期間網址換位置（apply_presets 會保留這軌的位置）
    for st, du in (site_windows if site_pos else []):
        script.add_segment(cc.TextSegment(g.SITE_TEXT, cc.Timerange(st, du), style=site_style,
                                          clip_settings=ClipSettings(transform_x=site_pos[0], transform_y=site_pos[1])), "網址版型")
    if bgm:
        _log(log, "加入音樂…")
        try:
            amat = audio_material(bgm)
            mstart = int(music_start * SEC)
            alen = min(cursor, amat.duration - mstart)
            aseg = cc.AudioSegment(amat, cc.Timerange(0, alen), source_timerange=cc.Timerange(mstart, alen), volume=0.8)
            aseg.add_fade(f"{g.MUSIC_FADE_IN}s", "1s")
            script.add_track(cc.TrackType.audio, "BGM")
            script.add_segment(aseg, "BGM")
        except Exception as e:
            _log(log, f"音樂讀取失敗（{type(e).__name__}: {e}），這支先不放音樂，請在 CapCut 手動加")
            bgm = None

    _log(log, "儲存草稿…")
    script.save()
    _log(log, "套用文字預設組與潤飾…")
    post = []
    try:
        post = g.apply_presets(name)
    except Exception as e:
        post = [f"套用預設組失敗：{type(e).__name__}: {e}"]
    for n in post:
        _log(log, n)
    if progress:
        try:
            progress(total_steps, total_steps, "完成")
        except Exception:
            pass
    return {"ok": True, "name": name, "layout": layout_id if L else "", "total_sec": round(cursor / SEC, 1), "segments": out_segments,
            "notes": [str(x) for x in post], "skipped": skipped,
            "bpm": round(bpm) if bpm else None, "music_start": round(music_start, 1) if bgm else None,
            "draft_path": os.path.join(g.DRAFT_FOLDER, name)}
