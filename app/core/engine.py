# -*- coding: utf-8 -*-
# 引擎：依介面傳入的片段規格產生 CapCut 草稿（沿用 generator 的卡點／置中／穩定／潤飾／預設組）

import os
import traceback
import json
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


OPENING_MIN_US = int(3.0 * 1_000_000)   # 開頭動畫至少撐 3 秒（第一段不夠長就延到下一個切點）


def _opening_slots(script, layout_track, opening_path, tdur, out_segments, log):
    """多格影片類開頭：讀 XX.json（渲染時逐格取樣的格子軌跡），每格一軌實拍影片，上層文字 XX_top.mov。
    回傳放了幾格；不是多格類（沒有 json）回 0。"""
    base = os.path.splitext(opening_path)[0]
    sj_path = base + ".json"
    if not os.path.exists(sj_path):
        return 0
    with open(sj_path, encoding="utf-8") as f:
        sj = json.load(f)
    fps, n, DW, DH = float(sj["fps"]), int(sj["frames"]), float(sj["w"]), float(sj["h"])
    samples = sj["samples"]
    slots = sj["slots"]
    if not samples or not slots:
        return 0
    k_time = tdur / (n / fps * SEC)            # 設計時間 → 草稿時間（整支開頭被調速塞進 tdur）
    K = 1080.0 / DW                            # 設計 px → 畫布 px
    # 影片來源：照時間軸上排好的實拍（同一支用同一個起點），不夠長的跳過
    pool = []
    for sg in out_segments:
        if sg.get("how") == "版型" or not sg.get("path"):
            continue
        pool.append((sg["path"], float(sg.get("start") or 0.0)))
    if not pool:
        return 0
    mats = {}
    placed = 0
    for i, meta in enumerate(slots):
        s0 = next((sm[i] for sm in samples if sm[i]["op"] > 0), samples[-1][i])
        w0, h0 = float(s0["w0"]), float(s0["h0"])
        if w0 <= 0 or h0 <= 0:
            continue
        # 選一支夠長的實拍
        pick = None
        for j in range(len(pool)):
            path, st = pool[(i + j) % len(pool)]
            if path not in mats:
                mats[path] = cc.VideoMaterial(path)
            m0 = mats[path]
            if m0.duration >= tdur:
                st_us = int(min(st * SEC, max(0, m0.duration - tdur)))
                pick = (path, st_us, m0)
                break
        if not pick:
            continue
        path, st_us, m0 = pick
        # 依格子比例裁切（object-position 決定裁哪邊），裁完 CapCut 會把裁切區「置入」畫布，再算縮放
        a = w0 / h0
        sa = m0.width / m0.height if m0.height else 9 / 16
        op = (meta.get("objpos") or "50% 50%").split()
        try:
            px = float(op[0].rstrip("%")) / 100 if op[0].endswith("%") else 0.5
            py = float(op[1].rstrip("%")) / 100 if len(op) > 1 and op[1].endswith("%") else 0.5
        except Exception:
            px, py = 0.5, 0.5
        if sa > a:
            cw, ch = a / sa, 1.0
            x0, y0 = (1 - cw) * px, 0.0
        else:
            cw, ch = 1.0, sa / a
            x0, y0 = 0.0, (1 - ch) * py
        crop = cc.CropSettings(upper_left_x=x0, upper_left_y=y0, upper_right_x=x0 + cw, upper_right_y=y0,
                               lower_left_x=x0, lower_left_y=y0 + ch, lower_right_x=x0 + cw, lower_right_y=y0 + ch)
        mat = cc.VideoMaterial(path, crop_settings=crop)
        # 裁切區置入 1080x1920：比畫布寬就貼寬、否則貼高
        fit_w = 1080.0 if a >= 1080 / 1920 else 1920.0 * a
        def frame_vals(sm):
            sc = (w0 * sm["s"] * K) / fit_w
            tx = (sm["cx"] * K - 540.0) / 540.0
            ty = (960.0 - sm["cy"] * K) / 960.0
            return (round(tx, 4), round(ty, 4), round(sc, 4), round(sm["rot"], 2), round(sm["op"], 3))
        first = frame_vals(samples[0][i])
        seg = cc.VideoSegment(mat, cc.Timerange(0, tdur), source_timerange=cc.Timerange(st_us, tdur), volume=0.0,
                              clip_settings=ClipSettings(transform_x=first[0], transform_y=first[1], scale_x=first[2], scale_y=first[2],
                                                         rotation=first[3], alpha=first[4]))
        props = (KeyframeProperty.position_x, KeyframeProperty.position_y, KeyframeProperty.uniform_scale,
                 KeyframeProperty.rotation, KeyframeProperty.alpha)
        eps = (0.002, 0.002, 0.002, 0.1, 0.01)
        last_emit = [None] * 5
        prev_vals = None
        for fi, sm in enumerate(samples):
            vals = frame_vals(sm[i])
            t_us = int(round(fi / fps * SEC * k_time))
            t_us = min(t_us, tdur - 1)
            for p_i in range(5):
                v = vals[p_i]
                le = last_emit[p_i]
                if le is None or abs(v - le[1]) > eps[p_i] or fi == len(samples) - 1:
                    # 值有變：先把上一格補成關鍵影格（維持停頓），再放這一格
                    if le is not None and prev_vals is not None and le[0] != fi - 1 and abs(prev_vals[p_i] - le[1]) <= eps[p_i]:
                        seg.add_keyframe(props[p_i], int(round((fi - 1) / fps * SEC * k_time)), prev_vals[p_i])
                    seg.add_keyframe(props[p_i], t_us, v)
                    last_emit[p_i] = (fi, v)
            prev_vals = vals
        script.add_segment(seg, layout_track(cc.TrackType.video, f"開頭格{i + 1}", 25 + i))
        placed += 1
    top = base + "_top.mov"
    if os.path.exists(top) and sj.get("hasTop"):
        tm = cc.VideoMaterial(top)
        script.add_segment(cc.VideoSegment(tm, cc.Timerange(0, tdur), source_timerange=cc.Timerange(0, int(tm.duration)),
                                           speed=tm.duration / max(tdur, 1), volume=0.0),
                           layout_track(cc.TrackType.video, "開頭文字", 45))
    return placed


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
    # 同名草稿已存在就另取名字（名稱 -2、-3…），不要原地覆蓋：
    # CapCut 對同一個草稿資料夾留有快取（穩定矩陣、素材索引），覆蓋後重開會有片段讀不到、播放與匯出凍格
    base_name = name
    n = 2
    while os.path.exists(os.path.join(g.DRAFT_FOLDER, name)):
        name = f"{base_name} -{n}"
        n += 1
    if name != base_name:
        _log(log, f"同名草稿已存在，這支叫「{name}」")
    script = draft_folder.create_draft(name, g.WIDTH, g.HEIGHT, allow_replace=False)
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

    def length_for(mat, target_sec, skip, sec_range=None, exact=False):
        nonlocal beat_idx
        avail = mat.duration - skip
        if not beats:
            return min(int(target_sec * SEC), avail)
        nb = g.beats_for(target_sec, beat_len)
        if exact:
            nb = max(1, min(8, round(target_sec / beat_len)))
        elif sec_range:
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

    # ---- 排段落（planner）：長度模式 20／25／30／依片段，開頭結尾 8 拍、中段 4–6 拍、近拍 4 拍 ----
    import planner as P
    pairs = [(c.get("path"), c) for c in model_clips]
    cl_pairs = [(c.get("path"), c) for c in closeups]
    groups = []
    for path, c in pairs:
        key = (c.get("model") or "").upper()
        if groups and groups[-1][0] == key:
            groups[-1][1].append((path, c))
        else:
            groups.append((key, [(path, c)]))
    # 同一位模特不連續放進來的也合併成一組（輪流時才不會重複）
    merged = {}
    order = []
    for k, grp in groups:
        if k not in merged:
            merged[k] = []
            order.append(k)
        merged[k] += grp
    groups = [(k, merged[k]) for k in order]

    layout_id = (spec.get("layout") or "").strip()
    L = layouts.LAYOUTS.get(layout_id) if (layouts and layout_id) else None
    layout_meta = None
    if L:
        try:
            layout_meta = layouts.prepare(layout_id, LAYOUT_CACHE)
        except Exception as e:
            _log(log, f"版型「{L['name']}」準備失敗（{type(e).__name__}: {e}），改用標準排法")
            L = None
    pattern = L["pattern"].split(",") if L else None
    length_mode = str(opt.get("length_mode") or "").strip() or ("fit" if g.MAX_TOTAL_SEC >= 28 else str(int(g.MAX_TOTAL_SEC)))
    plan = P.build_sequence(groups, cl_pairs, pattern, length_mode, beat_len if beats else 0.5, layout_id)
    sequence = [(kind, item[0] if item else None, item[1] if item else None, nb) for kind, item, nb in plan["seq"]]
    picked_groups = []
    for k, grp in groups:
        used = [it for it in grp if any(sq[1] == it[0] for sq in sequence if sq[0] == "clip")]
        picked_groups.append((k, used or grp[:1]))
    skipped = len(plan["skipped"])
    mode_name = {"fit": "依片段"}.get(length_mode, f"{length_mode} 秒")
    _log(log, f"長度「{mode_name}」：滿版 {plan['n_m']} 段、近拍 {plan['n_d']} 段，預計 {plan['total']} 秒" + (f"，略過 {skipped} 支" if skipped else ""))
    if L:
        _log(log, f"版型「{L['name']}」：{' → '.join('版型' if t[0] == 'layout' else ('滿版' if t[0] == 'clip' else '近拍') for t in sequence)}")

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
        nb = max(1, min(8, round(target_sec / beat_len)))     # 版型段 3.2 秒取最接近的拍數
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
            if t.get("render"):
                # 畫成透明 PNG 一層（CapCut 讀不到的字型用這條路；位置、大小在 CapCut 仍可調，只是不能改字）
                import hashlib
                key = hashlib.md5(json.dumps(t, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:10]
                png = os.path.join(RENDER_DIR, f"{layout_id}_text{ti}_{key}.png")
                if not os.path.exists(png):
                    layouts.render_text_png(t, png)
                mat = cc.VideoMaterial(png)
                seg = cc.VideoSegment(mat, cc.Timerange(t0, dur), clip_settings=ClipSettings(transform_x=tx, transform_y=ty))
                script.add_segment(seg, layout_track(cc.TrackType.video, "版型文字圖" + (str(ti + 1) if ti else ""), 22 + ti))
                continue
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
    first_clip_us = None    # 第一段實拍的精確長度（微秒）
    seg_ends = []           # 每段實拍結束的時間點（微秒），開頭動畫要結束在其中一個切點上
    total_steps = max(1, len(sequence))

    for idx, (kind, full, c, nb) in enumerate(sequence):
        fname = os.path.basename(full) if full else f"版型 {L['name']}"
        target = (nb * beat_len) if (beats and nb) else (g.CLOSEUP_SEC if kind == "close" else g.CLIP_SEC)
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
        if manual is None and reuse_start(full, mat, int(target * SEC)) is not None:
            manual = reuse_start(full, mat, int(target * SEC)) / SEC
        if kind == "close":
            g.MODEL_BY_PATH[full] = ""
            dur = length_for(mat, target, 0, exact=bool(beats and nb))
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
            if first_clip_us is None:
                first_clip_us = dur
            cursor += dur
            seg_ends.append(cursor)
            continue

        model_name = (c.get("model") or "").strip()
        g.MODEL_BY_PATH[full] = model_name.upper() if (model_name and beauty_on) else ""
        skip, _ = g.cut(mat, target, g.CLIP_SKIP)
        dur = length_for(mat, target, skip, exact=bool(beats and nb))
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
        if first_clip_us is None:
            first_clip_us = dur
        cursor += dur
        seg_ends.append(cursor)

    # 開頭動畫：帶 alpha 的 .mov 疊在第一段滿版上（最上層），長度跟著第一段調速；文字（MODEL 卡／商品名稱／網址）等開頭結束再出現
    opening_window = None
    opening_path = (spec.get("opening") or "").strip()
    if opening_path and os.path.exists(opening_path) and out_segments and out_segments[0].get("how") != "版型":
        try:
            omat = cc.VideoMaterial(opening_path)
            odur = int(omat.duration)
            # 結束在切點上：第一段夠長（≥3 秒）就跟第一段切齊，太短就延到下一個切點，整支動畫調速塞進去
            tdur = next((e for e in seg_ends if e >= OPENING_MIN_US), seg_ends[-1] if seg_ends else int(first_clip_us or 0))
            tdur = min(tdur, cursor)
            speed = odur / max(tdur, 1)
            script.add_segment(cc.VideoSegment(omat, cc.Timerange(0, tdur), source_timerange=cc.Timerange(0, odur),
                                               speed=speed, volume=0.0),
                               layout_track(cc.TrackType.video, "開頭動畫", 24))
            opening_window = (0, tdur)
            # 多格影片類開頭：影片格子用 CapCut 原生圖層（位置／縮放／旋轉／透明度關鍵影格），字在最上層
            try:
                n_slots = _opening_slots(script, layout_track, opening_path, tdur, out_segments, log)
                if n_slots:
                    _log(log, f"開頭格子：{n_slots} 格實拍（可在 CapCut 直接換影片）")
            except Exception as e:
                _log(log, f"開頭格子放不上去（{type(e).__name__}: {e}）")
                traceback.print_exc()
            _log(log, f"疊開頭動畫「{os.path.splitext(os.path.basename(opening_path))[0]}」{tdur / SEC:.1f} 秒（速度 {speed:.2f}x）")
        except Exception as e:
            _log(log, f"開頭動畫加不上去（{type(e).__name__}: {e}），這支先不疊")
            opening_window = None
    elif opening_path and out_segments and out_segments[0].get("how") == "版型":
        _log(log, "這個版型第一段是版型卡，不疊開頭動畫")
    if opening_window:
        o_end = opening_window[1]
        trimmed = []
        for text, start, dur in card_runs:
            if start + dur <= o_end:
                continue
            if start < o_end:
                dur -= (o_end - start)
                start = o_end
            trimmed.append([text, start, dur])
        card_runs = trimmed

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
    title_size = min(5.0, 5.0 * 30 / max(len(base_name), 1))

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

    title_windows = list(layout_windows) if (layout_meta and layout_meta.get("hide_title")) else []
    if opening_window:
        title_windows.append(opening_window)
    for st, du in outside_windows(cursor, title_windows):
        script.add_segment(cc.TextSegment(base_name, cc.Timerange(st, du),
                                          style=TextStyle(size=title_size, color=(0.0, 0.0, 0.0), align=1),
                                          background=TextBackground(color="#FFFFFF", alpha=1.0, height=0.2, width=0.12),
                                          clip_settings=ClipSettings(transform_y=g.TITLE_Y)), "商品名稱")
    site_style = TextStyle(size=4.0, bold=True, align=1, letter_spacing=2)
    site_pos = layout_meta.get("site_pos") if layout_meta else None
    hide_site = bool(layout_meta and layout_meta.get("hide_site"))
    site_windows = list(layout_windows) if (site_pos or hide_site) else []
    for st, du in outside_windows(cursor, site_windows + ([opening_window] if opening_window else [])):
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
    return {"ok": True, "name": base_name, "draft_name": name, "layout": layout_id if L else "", "total_sec": round(cursor / SEC, 1), "segments": out_segments,
            "notes": [str(x) for x in post], "skipped": skipped,
            "bpm": round(bpm) if bpm else None, "music_start": round(music_start, 1) if bgm else None,
            "opening": opening_path if opening_window else "",
            "draft_path": os.path.join(g.DRAFT_FOLDER, name)}
