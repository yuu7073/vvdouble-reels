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

    def build_layout_segment(idx, dur):
        """合成版型段：回傳 (mp4 路徑, 用到的模特名字)"""
        grp_clips = [grp for _, grp in picked_groups if grp]
        srcs = {}
        m1 = grp_clips[0][0] if grp_clips else None
        m2 = grp_clips[1][0] if len(grp_clips) > 1 else (grp_clips[0][1] if grp_clips and len(grp_clips[0]) > 1 else m1)
        roles = {r["role"] for r in layout_meta["regions"]}
        if m1 and "M1" in roles:
            srcs["M1"] = layout_source(m1[0], m1[1], dur)
        if m2 and "M2" in roles:
            srcs["M2"] = layout_source(m2[0], m2[1], dur)
        if cl_pairs and ("D" in roles or "D2" in roles):
            srcs["D"] = layout_source(cl_pairs[0][0], cl_pairs[0][1], dur, want_person=False)
            if "D2" in roles:
                d2 = cl_pairs[1] if len(cl_pairs) > 1 else cl_pairs[0]
                srcs["D2"] = layout_source(d2[0], d2[1], dur, want_person=False)
        elif ("D" in roles or "D2" in roles) and m1:
            srcs["D"] = layout_source(m1[0], m1[1], dur)      # 沒近拍就先用模特影片頂著
        photo = (spec.get("background") or "").strip()
        if "P" in roles:
            if photo and os.path.exists(photo):
                srcs["P"] = {"path": photo, "photo": True}
            else:
                _log(log, "這個版型需要底圖，沒選底圖的話紫色區會維持原色")
        os.makedirs(RENDER_DIR, exist_ok=True)
        safe = "".join(ch for ch in name if ch not in '\\/:*?"<>|')[:40]
        out = os.path.join(RENDER_DIR, f"{safe}_{layout_id}_{idx}.mp4")
        layouts.render(layout_meta, srcs, dur / SEC, out)
        return out, ((m1[1].get("model") if m1 else "") or "")

    g.STABILIZE_IDS.clear()
    g.MODEL_BY_PATH.clear()
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
            _log(log, f"合成版型「{L['name']}」{dur / SEC:.1f} 秒…")
            out, m1_name = build_layout_segment(idx, dur)
            g.MODEL_BY_PATH[out] = m1_name.upper() if (m1_name and beauty_on) else ""
            mat = cc.VideoMaterial(out)
            d = min(dur, mat.duration)
            script.add_segment(cc.VideoSegment(mat, cc.Timerange(cursor, d), source_timerange=cc.Timerange(0, d),
                                               volume=0.0), "影片")
            out_segments.append({"path": out, "name": fname, "closeup": False, "layout": layout_id,
                                 "start": 0, "dur": round(d / SEC, 2), "how": "版型", "stabilized": False})
            cursor += d
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
    script.add_segment(cc.TextSegment(name, cc.Timerange(0, cursor),
                                      style=TextStyle(size=title_size, color=(0.0, 0.0, 0.0), align=1),
                                      background=TextBackground(color="#FFFFFF", alpha=1.0, height=0.2, width=0.12),
                                      clip_settings=ClipSettings(transform_y=g.TITLE_Y)), "商品名稱")
    script.add_segment(cc.TextSegment(g.SITE_TEXT, cc.Timerange(0, cursor),
                                      style=TextStyle(size=4.0, bold=True, align=1, letter_spacing=2),
                                      clip_settings=ClipSettings(transform_y=g.SITE_Y)), "網址")
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
