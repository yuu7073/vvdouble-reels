# -*- coding: utf-8 -*-
# 人物偵測：用 MediaPipe 人物偵測模型（opencv_zoo 版，ONNX）找出每一格畫面裡人在哪裡
# 需要同資料夾的 person_detection.onnx 與 person_anchors.npy；缺少時所有功能安全退回 None

import os
import re
import subprocess

import numpy as np

import sys


def _asset_dir():
    """模型檔位置：原始碼模式在本檔同層；打包後在 _internal/core"""
    cands = [os.path.dirname(os.path.abspath(__file__))]
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cands = [os.path.join(base, "core"), base] + cands
    for c in cands:
        if os.path.exists(os.path.join(c, "person_detection.onnx")):
            return c
    return cands[0]


HERE = _asset_dir()
MODEL = os.path.join(HERE, "person_detection.onnx")
_sess = None
_det = None
LOAD_ERROR = ""


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def available():
    return _load() is not None


def _load():
    """純 numpy + Pillow + onnxruntime 的人物偵測（不需要 OpenCV）"""
    global _sess, _det, LOAD_ERROR
    if _det is not None:
        return _det
    try:
        import onnxruntime as ort
    except Exception as e:
        LOAD_ERROR = f"缺 onnxruntime 套件（{e}）"
        return None
    try:
        from PIL import Image
    except Exception as e:
        LOAD_ERROR = f"缺 pillow 套件（{e}）"
        return None
    anchors_path = os.path.join(HERE, "person_anchors.npy")
    if not os.path.exists(MODEL) or not os.path.exists(anchors_path):
        LOAD_ERROR = "core 資料夾缺 person_detection.onnx 或 person_anchors.npy"
        return None
    try:
        _sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
        anchors = np.load(anchors_path)
    except Exception as e:
        LOAD_ERROR = f"模型載入失敗：{e}"
        return None
    sess = _sess
    in_name = sess.get_inputs()[0].name

    def nms(boxes, scores, thr=0.3, top=50):
        order = np.argsort(-scores)[:top]
        keep = []
        while len(order):
            i = order[0]
            keep.append(i)
            if len(order) == 1:
                break
            rest = order[1:]
            xx1 = np.maximum(boxes[i, 0], boxes[rest, 0]); yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
            xx2 = np.minimum(boxes[i, 2], boxes[rest, 2]); yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
            inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
            a1 = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
            a2 = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
            iou = inter / (a1 + a2 - inter + 1e-6)
            order = rest[iou <= thr]
        return keep

    class Det:
        score_threshold = 0.45

        def infer(self, image):
            """image: BGR uint8 [h,w,3] → rows [x1,y1,x2,y2, 4 個關鍵點 x,y..., score]（像素座標）"""
            h, w, _ = image.shape
            rgb = image[:, :, ::-1]
            scale = 224.0 / max(h, w)
            nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
            im = Image.fromarray(np.ascontiguousarray(rgb)).resize((nw, nh), Image.BILINEAR)
            canvas = np.zeros((224, 224, 3), dtype=np.float32)
            left, top = (224 - nw) // 2, (224 - nh) // 2
            canvas[top:top + nh, left:left + nw] = (np.asarray(im, dtype=np.float32) / 255.0 - 0.5) * 2
            blob = np.transpose(canvas, (2, 0, 1))[None]
            outs = sess.run(None, {in_name: blob})
            outs = sorted(outs, key=lambda a: -a.shape[-1])
            box_delta = outs[0][0, :, 0:4]
            lm_delta = outs[0][0, :, 4:12]
            score = 1 / (1 + np.exp(-np.clip(outs[1][0, :, 0].astype(np.float64), -100, 100)))
            big = max(w, h)
            cxy = box_delta[:, :2] / 224.0
            wh = box_delta[:, 2:] / 224.0
            xy1 = (cxy - wh / 2 + anchors) * big
            xy2 = (cxy + wh / 2 + anchors) * big
            boxes = np.concatenate([xy1, xy2], axis=1)
            pad = np.array([left, top]) / scale
            boxes -= np.array([pad[0], pad[1], pad[0], pad[1]])
            m = score >= self.score_threshold
            if not m.any():
                return np.empty((0, 13))
            idx = np.where(m)[0]
            keep = nms(boxes[idx], score[idx])
            sel = idx[keep]
            lms = lm_delta[sel].reshape(-1, 4, 2) / 224.0 + anchors[sel][:, None, :]
            lms = lms * big - pad
            return np.c_[boxes[sel], lms.reshape(-1, 8), score[sel]]

    _det = Det()
    return _det


def decode_keyframes(path, width=320):
    """只解關鍵影格（快），回傳 (frames BGR uint8 [n,h,w,3], times)"""
    exe = _ffmpeg()
    try:
        r = subprocess.run([exe, "-hide_banner", "-skip_frame", "nokey", "-i", path,
                            "-vf", f"scale={width}:-2,showinfo", "-vsync", "0",
                            "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                           capture_output=True, timeout=300,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return None, None
    log = r.stderr.decode("utf-8", "ignore")
    times = [float(x) for x in re.findall(r"pts_time:([\d.]+)", log)]
    size = re.search(r"\bs:(\d+)x(\d+)", log)
    if not size or not times:
        return None, None
    w, h = int(size.group(1)), int(size.group(2))
    n = min(len(r.stdout) // (w * h * 3), len(times))
    if n == 0:
        return None, None
    frames = np.frombuffer(r.stdout[: n * w * h * 3], dtype=np.uint8).reshape(n, h, w, 3)
    return frames, np.array(times[:n])


def detect_people(frames):
    """每格回傳 dict 或 None：cx, top, bottom（0~1 比例）, size（人佔畫面高度比例）"""
    det = _load()
    if det is None or frames is None:
        return None
    out = []
    for img in frames:
        h, w = img.shape[:2]
        try:
            r = det.infer(np.ascontiguousarray(img))
        except Exception:
            out.append(None)
            continue
        if len(r) == 0:
            out.append(None)
            continue
        row = max(r, key=lambda x: x[-1])
        lm = row[4:12].reshape(4, 2) / [w, h]
        hip, top, shoulder = lm[0], lm[1], lm[2]
        cx = float((hip[0] + shoulder[0]) / 2)
        # 身體圓：中心在 hip，半徑到 top；腳大約在 hip + (hip - top) 的位置
        bottom = float(min(1.0, hip[1] + (hip[1] - top[1])))
        out.append({"cx": cx, "top": float(top[1]), "bottom": bottom,
                    "size": float(bottom - top[1]), "score": float(row[-1])})
    return out


def framing_score(p):
    """構圖分數 0~1：人在畫面中央、頭沒被切到"""
    if p is None:
        return 0.0
    center = 1.0 - min(1.0, abs(p["cx"] - 0.5) / 0.35)
    head_ok = 1.0 if p["top"] > 0.02 else 0.3
    return 0.6 * center + 0.4 * head_ok


def person_offset(people, times, t0, t1):
    """某時間窗內人物平均水平位置相對中心的偏移（-0.5~0.5），沒人回傳 None"""
    if not people:
        return None
    xs = [p["cx"] for p, t in zip(people, times) if p is not None and t0 <= t <= t1]
    if not xs:
        return None
    return float(np.median(xs) - 0.5)


def detail_point(frames, times, t0, t1, grid=6):
    """近拍：找時間窗內紋理最豐富的區塊中心 (tx, ty) 0~1"""
    if frames is None:
        return None
    sel = [f for f, t in zip(frames, times) if t0 <= t <= t1]
    if not sel:
        sel = [frames[len(frames) // 2]]
    acc = None
    for img in sel:
        g = img.astype(np.float32).mean(axis=2)
        lap = np.abs(g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:])
        acc = lap if acc is None else acc + lap
    h, w = acc.shape
    cells = np.zeros((grid, grid))
    for i in range(grid):
        for j in range(grid):
            cells[i, j] = acc[i * h // grid:(i + 1) * h // grid, j * w // grid:(j + 1) * w // grid].mean()
    # 2x2 視窗找最密的區塊，避免挑到單一噪點
    best, bi, bj = -1, 0, 0
    for i in range(grid - 1):
        for j in range(grid - 1):
            s = cells[i:i + 2, j:j + 2].sum()
            if s > best:
                best, bi, bj = s, i, j
    return ((bj + 1) / grid, (bi + 1) / grid)


def shake_percent(path, t0, dur, width=160, fps=15):
    """某時間窗的手震程度（去掉平移後的抖動量，佔畫面寬度 %）；失敗回傳 None"""
    exe = _ffmpeg()
    try:
        r = subprocess.run([exe, "-hide_banner", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", path,
                            "-vf", f"fps={fps},scale={width}:-2,format=gray,showinfo",
                            "-f", "rawvideo", "-"],
                           capture_output=True, timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return None
    size = re.search(r"\bs:(\d+)x(\d+)", r.stderr.decode("utf-8", "ignore"))
    if not size:
        return None
    w, h = int(size.group(1)), int(size.group(2))
    n = len(r.stdout) // (w * h)
    if n < fps:
        return None
    g = np.frombuffer(r.stdout[: n * w * h], dtype=np.uint8).reshape(n, h, w).astype(np.float32)
    g = g - g.mean(axis=(1, 2), keepdims=True)
    win = np.outer(np.hanning(h), np.hanning(w))
    F = np.fft.fft2(g * win)
    sh = []
    for i in range(1, n):
        R = F[i] * np.conj(F[i - 1])
        R /= (np.abs(R) + 1e-6)
        c = np.fft.ifft2(R).real
        dy, dx = np.unravel_index(np.argmax(c), c.shape)
        if dy > h // 2:
            dy -= h
        if dx > w // 2:
            dx -= w
        sh.append((dx, dy))
    path_xy = np.cumsum(np.array(sh, dtype=float), axis=0)
    k = max(3, int(fps * 0.5))
    ker = np.ones(k) / k
    smooth = np.stack([np.convolve(path_xy[:, i], ker, mode="same") for i in range(2)], 1)
    res = path_xy - smooth
    m = k // 2
    if len(res) > 2 * m:
        res = res[m:-m]
    return float(np.sqrt((res ** 2).sum(axis=1).mean())) / w * 100
