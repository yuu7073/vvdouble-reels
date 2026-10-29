# -*- coding: utf-8 -*-
"""排段落：決定放幾段、哪幾支、每段幾拍（開頭／結尾 8 拍、中段 4–6 拍、近拍 4 拍）

長度模式：
  "20"／"25"／"30"  固定總長：放得下幾段就幾段，放不下的略過（每位模特至少 1 段）
  "fit"            依片段：選入幾支放幾段，秒數跟著算；上限 30 秒，超過就先把中段壓到 4 拍，再減近拍、再減滿版
"""
import math

LAYOUT_SEC = 3.2
OPEN_BEATS = 8
CLOSE_BEATS = 8
MID_MAX = 6
MID_MIN = 4
D_BEATS = 4
CLIP_MIN_SEC = 2.0
FIT_CAP = 30.0
D_CAP = {20: 2, 25: 3, 30: 3}


def _mid_min(beat_len):
    """滿版每段不短於 2 秒"""
    return max(MID_MIN, math.ceil(CLIP_MIN_SEC / beat_len - 1e-6))


def m_beats(n_m, mid, beat_len):
    """n_m 段滿版的拍數：第一段 8、最後一段 8、中間 mid（mid 可為單一值或遞減）"""
    if n_m <= 0:
        return []
    if n_m == 1:
        return [OPEN_BEATS]
    if n_m == 2:
        return [OPEN_BEATS, CLOSE_BEATS]
    lo = _mid_min(beat_len)
    k = n_m - 2
    if mid == "decay":
        # 6 → 4 遞減（中段越來越快）
        vals = [round(MID_MAX - (MID_MAX - lo) * i / max(1, k - 1)) for i in range(k)] if k > 1 else [MID_MAX]
        vals = [max(lo, min(MID_MAX, v)) for v in vals]
    else:
        vals = [max(lo, int(mid))] * k
    return [OPEN_BEATS] + vals + [CLOSE_BEATS]


def total_sec(n_m, n_d, mid, beat_len, has_layout):
    mb = m_beats(n_m, mid, beat_len)
    return (sum(mb) + n_d * D_BEATS) * beat_len + (LAYOUT_SEC if has_layout else 0.0)


def choose_counts(n_m_avail, n_d_avail, n_models, mode, beat_len, has_layout):
    """回傳 (n_m, n_d, mid, total)；mid 是 'decay' 或整數拍"""
    n_m_avail = int(n_m_avail)
    n_d_avail = int(n_d_avail)
    lo = _mid_min(beat_len)
    if mode == "fit":
        n_m, n_d = n_m_avail, min(n_d_avail, 3)
        for mid in ("decay", MID_MAX - 1, lo):
            t = total_sec(n_m, n_d, mid, beat_len, has_layout)
            if t <= FIT_CAP:
                return n_m, n_d, mid, t
        # 還是太長：先減近拍，再減滿版（保留每位模特至少 1 段）
        while n_d > 1 and total_sec(n_m, n_d, lo, beat_len, has_layout) > FIT_CAP:
            n_d -= 1
        while n_m > max(1, n_models) and total_sec(n_m, n_d, lo, beat_len, has_layout) > FIT_CAP:
            n_m -= 1
        return n_m, n_d, lo, total_sec(n_m, n_d, lo, beat_len, has_layout)
    T = float(mode) if str(mode).replace(".", "").isdigit() else 20.0
    d_cap = D_CAP.get(int(T), 3)
    n_d = min(n_d_avail, d_cap) if n_d_avail else 0
    best = None
    # 先盡量多放段（中段 4 拍），放得下全部再把中段拉長（5、6、遞減）
    for n_m in range(min(n_m_avail, 12), 0, -1):
        if total_sec(n_m, n_d, lo, beat_len, has_layout) <= T + 1e-6:
            best = n_m
            break
    if best is None:
        # 連 1 段滿版＋近拍都放不下：減近拍
        while n_d > 0 and total_sec(1, n_d, lo, beat_len, has_layout) > T:
            n_d -= 1
        best = 1 if n_m_avail else 0
    n_m = best
    mid = lo
    for cand in ("decay", MID_MAX - 1):
        if total_sec(n_m, n_d, cand, beat_len, has_layout) <= T + 1e-6:
            mid = cand
            break
    return n_m, n_d, mid, total_sec(n_m, n_d, mid, beat_len, has_layout)


def _quota(groups, n_m):
    """n_m 段滿版分給各模特：每位至少 1，其餘依影片數比例，不超過各自擁有的支數"""
    sizes = [len(g) for g in groups]
    if not sizes or n_m <= 0:
        return [0] * len(sizes)
    q = [1 if s > 0 else 0 for s in sizes]
    left = n_m - sum(q)
    if left < 0:          # 段數比模特數還少：照影片多的先
        order = sorted(range(len(sizes)), key=lambda i: -sizes[i])
        q = [0] * len(sizes)
        for i in order[:n_m]:
            q[i] = 1
        return q
    while left > 0:
        # 每輪給「目前配額／擁有數」比例最低的那位
        cands = [i for i in range(len(sizes)) if q[i] < sizes[i]]
        if not cands:
            break
        i = min(cands, key=lambda i: q[i] / sizes[i])
        q[i] += 1
        left -= 1
    return q


def _pick_even(items, n):
    if n <= 0:
        return []
    if n >= len(items):
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def build_sequence(groups, closeups, pattern, mode, beat_len, layout_id=""):
    """groups=[(model_key, [clip,...])]（照放入順序）、closeups=[clip,...]
    回傳 {"seq": [(kind, clip|None, beats|None)], "n_m", "n_d", "mid", "total", "skipped": [clip,...]}
    kind: "layout" / "clip" / "close"；layout 段 beats=None（固定 3.2 秒）"""
    has_layout = bool(pattern and "#" in pattern)
    n_m_avail = sum(len(g) for _, g in groups)
    n_models = sum(1 for _, g in groups if g)
    # 影片太少時至少撐到版型本來的段數（沒版型就 4 段），同一支影片會接著上一段的結尾再取一段
    # 固定長度：影片少（<5 支）時允許同一支再取一段（接著上一段結尾），把長度撐到接近目標；依片段不重複
    eff = n_m_avail if (mode == "fit" or n_m_avail >= 5 or n_m_avail == 0) else min(n_m_avail * 2, 8)
    n_m, n_d, mid, total = choose_counts(eff, len(closeups), n_models, mode, beat_len, has_layout)
    quota = _quota([g for _, g in groups], min(n_m, n_m_avail))
    picked = [(k, _pick_even(g, q)) for (k, g), q in zip(groups, quota)]
    # 滿版輪流：每輪各模特出一支（模特交錯，不會一整排同一人）
    m_slots = []
    pools = [list(g) for _, g in picked]
    while any(pools):
        for p in pools:
            if p:
                m_slots.append(p.pop(0))
    if m_slots and n_m > len(m_slots):
        base = list(m_slots)
        while len(m_slots) < n_m:
            m_slots.append(base[len(m_slots) % len(base)])
    d_slots = _pick_even(closeups, n_d)
    used = set(id(c) for c in m_slots) | set(id(c) for c in d_slots)
    skipped = [c for _, g in groups for c in g if id(c) not in used] + [c for c in closeups if id(c) not in used]

    # 骨架：有版型照版型順序（M／D 數量調到 n_m／n_d），沒有就 M 之間穿插 D
    if has_layout:
        toks = list(pattern)
        def adjust(tok, want):
            have = toks.count(tok)
            while have > want:                     # 從中間拿掉
                idxs = [i for i, t in enumerate(toks) if t == tok]
                toks.pop(idxs[len(idxs) // 2])
                have -= 1
            while have < want:                     # 插在中段
                idxs = [i for i, t in enumerate(toks) if t == tok]
                at = idxs[len(idxs) // 2] + 1 if idxs else len(toks)
                toks.insert(at, tok)
                have += 1
        adjust("M", n_m)
        # 近拍：拿掉從中間拿，加的放在離其他近拍最遠的位置（不放第一段、不放最後一段）
        while toks.count("D") > n_d:
            idxs = [i for i, t in enumerate(toks) if t == "D"]
            toks.pop(idxs[len(idxs) // 2])
        while toks.count("D") < n_d:
            ds = [i for i, t in enumerate(toks) if t == "D"]
            best, best_gap = None, -1
            for pos in range(1, len(toks)):
                if toks[pos - 1] == "D" or (pos < len(toks) and toks[pos] == "D"):
                    continue
                gap = min([abs(pos - i) for i in ds] + [pos, len(toks) - pos])
                if gap > best_gap:
                    best, best_gap = pos, gap
            toks.insert(best if best is not None else len(toks) - 1, "D")
    else:
        toks = ["M"] * n_m
        # 近拍平均穿插在滿版之間（不放第一段、不放最後一段）
        for j in range(n_d):
            pos = int(round((j + 1) * len(toks) / (n_d + 1)))
            pos = max(1, min(len(toks) - 1, pos)) if len(toks) > 1 else len(toks)
            toks.insert(pos, "D")
    mb = m_beats(n_m, mid, beat_len)
    seq, mi, di = [], 0, 0
    for t in toks:
        if t == "#":
            seq.append(("layout", None, None))
        elif t == "M" and mi < len(m_slots):
            seq.append(("clip", m_slots[mi], mb[mi] if mi < len(mb) else mb[-1]))
            mi += 1
        elif t == "D" and di < len(d_slots):
            seq.append(("close", d_slots[di], D_BEATS))
            di += 1
    return {"seq": seq, "n_m": n_m, "n_d": n_d, "mid": mid, "total": round(total, 1), "skipped": skipped,
            "beat_len": beat_len}
