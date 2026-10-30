# -*- coding: utf-8 -*-
# VV 實穿草稿產生器 — 桌面版（pywebview 視窗 + HTML 介面）

import base64
import csv
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import shutil
import traceback

FROZEN = getattr(sys, "frozen", False)
HERE = getattr(sys, "_MEIPASS", None) if FROZEN else os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else HERE   # 可更新的安裝資料夾
CORE = os.path.join(HERE, "core")
sys.path.insert(0, CORE)
sys.path.insert(0, HERE)
from version import VERSION, REPO  # noqa: E402

# HTTPS 憑證：用 Windows 自己的憑證庫（公司防毒／代理會換憑證，Python 內建的憑證包認不得）
try:
    import truststore  # noqa: E402
    truststore.inject_into_ssl()
except Exception:
    try:
        import ssl, certifi  # noqa: E402
        ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass

import numpy as np  # noqa: E402

import generator as g  # noqa: E402
import engine  # noqa: E402
try:
    import layouts  # noqa: E402
except Exception:
    layouts = None
VISION_ERROR = ""
try:
    import vision
    try:
        import onnxruntime  # noqa: F401
    except Exception as _e:
        VISION_ERROR = f"onnxruntime 載入失敗：{_e}"
except Exception as _e:
    vision = None
    VISION_ERROR = f"vision 載入失敗：{_e}"

APP_NAME = "VVReels"
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
os.makedirs(DATA_DIR, exist_ok=True)
LOG_FILE = os.path.join(DATA_DIR, "log.txt")
engine.RENDER_DIR = os.path.join(DATA_DIR, "renders")
engine.LAYOUT_CACHE = os.path.join(DATA_DIR, "layout_cache")


class _Tee:
    """把所有輸出同時寫進 log.txt（打包成 exe 後沒有黑色視窗，靠這個回報錯誤）"""

    def __init__(self, stream):
        self.stream = stream
        self.lock = threading.Lock()

    def write(self, data):
        try:
            if self.stream:
                self.stream.write(data)
                self.stream.flush()
        except Exception:
            pass
        try:
            with self.lock:
                with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
                    f.write(data)
        except Exception:
            pass

    def flush(self):
        try:
            if self.stream:
                self.stream.flush()
        except Exception:
            pass


def _setup_log():
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 2_000_000:
            os.replace(LOG_FILE, LOG_FILE + ".old")
    except Exception:
        pass
    sys.stdout = _Tee(sys.__stdout__)
    sys.stderr = _Tee(sys.__stderr__)
    print(f"\n===== 啟動 {dt.datetime.now().isoformat(timespec='seconds')} 版本 {VERSION} =====")


_setup_log()
g.STYLE_FILE = os.path.join(CORE, "樣式.json")
g.LAYOUT_TEXT_TEMPLATE = os.path.join(CORE, "版型文字模板.json")
# 潤飾.json 需要可寫入：打包版放在使用者資料夾，第一次從安裝包複製
BEAUTY_PATH = os.path.join(CORE, "潤飾.json")
if FROZEN:
    import shutil
    BEAUTY_PATH = os.path.join(DATA_DIR, "潤飾.json")
    try:
        src = os.path.join(CORE, "潤飾.json")
        if os.path.exists(src) and (not os.path.exists(BEAUTY_PATH) or os.path.getmtime(src) > os.path.getmtime(BEAUTY_PATH) + 60):
            shutil.copy2(src, BEAUTY_PATH)
    except Exception:
        traceback.print_exc()
g.BEAUTY_FILE = BEAUTY_PATH
if FROZEN:
    # 舊版主程式檔名是中文，更新後留著會混用，啟動時順手清掉
    try:
        for _old in ("VV實穿草稿產生器.exe", "update.bat"):
            _p = os.path.join(APP_DIR, _old)
            if os.path.exists(_p) and os.path.basename(sys.executable) != _old:
                os.remove(_p)
    except Exception:
        pass
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
SESSION_FILE = os.path.join(DATA_DIR, "session.json")     # 上次關掉時製作頁的內容（商品、影片、設定），下次打開自動帶回
MODELS_FILE = os.path.join(DATA_DIR, "models.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
THUMB_DIR = os.path.join(DATA_DIR, "thumbs")
os.makedirs(THUMB_DIR, exist_ok=True)
MUSIC_INDEX_NAME = "音樂庫索引.json"
BG_INDEX_NAME = "底圖庫索引.json"
BG_SUBDIR = "空景"
OPENING_SUBDIR = "開頭動畫"      # 官網部門\開頭動畫\*.mov（帶透明的開頭動畫）
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
VIDEO_EXT = (".mp4", ".mov", ".m4v")
AUDIO_EXT = (".mp3", ".m4a", ".wav", ".aac")

# 公司共用音樂庫（Dropbox 團隊資料夾，每台電腦的使用者名稱不同，所以用家目錄組出來）
SHARED_MUSIC = os.path.join(os.path.expanduser("~"), "VV DOUBLE TEAM Dropbox", "「VV DOUBLE TEAM 」團隊資料夾",
                            "官網部門", "剪輯音樂庫")

DEFAULT_SETTINGS = {
    "draft_folder": g.DRAFT_FOLDER,
    "export_folder": "",
    "music_folder": SHARED_MUSIC,
    "max_total": 28,
    "mid_closeup": [1.5, 2.0],
    "person_center": True,
    "stabilize": True,
    "beauty": True,
    "last_model": "",
    "last_size": "",
    "todo_url": "",
    "length_mode": "20",
    "theme": "light",
    "bg_root": "",
    "cloud_url": "https://asia-east1-vvdouble-shopline.cloudfunctions.net/reels",
    "tool_password": "",
}
SITE_URL = "https://www.vvdouble.com"
TODO_CACHE = os.path.join(DATA_DIR, "todo_cache.json")
STATUS_NAME = "實穿影片製作狀態.json"


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _run(cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _probe_duration(path):
    r = _run([_ffmpeg(), "-hide_banner", "-i", path])
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr.decode("utf-8", "ignore"))
    if not m:
        return 0.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


HEAVY_MBPS = 80          # 超過這個碼率（或 >50fps）的相機檔，CapCut 時間軸會播不動、匯出凍格，先轉成 30fps H.264
TRANSCODE_DIR = os.path.join(DATA_DIR, "transcode")


def _probe_info(path):
    """回傳 (秒數, fps, Mbps)"""
    r = _run([_ffmpeg(), "-hide_banner", "-i", path])
    err = r.stderr.decode("utf-8", "ignore")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    dur = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0.0
    f = re.search(r"(\d+(?:\.\d+)?)\s*fps", err)
    fps = float(f.group(1)) if f else 30.0
    try:
        mbps = os.path.getsize(path) * 8 / max(0.1, dur) / 1e6
    except Exception:
        mbps = 0.0
    return dur, fps, mbps


def _maybe_transcode(path, emit=None):
    """太重的檔轉成輕量版（1080p 以內、30fps、H.264 CRF18、保留旋轉與聲音）；回傳要用的路徑與說明"""
    dur, fps, mbps = _probe_info(path)
    if mbps <= HEAVY_MBPS and fps <= 50:
        return path, ""
    os.makedirs(TRANSCODE_DIR, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(TRANSCODE_DIR, f"{stem}_{_file_key(path)[:8]}.mp4")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out, f"已用轉檔版（原檔 {mbps:.0f}Mbps/{fps:.0f}fps）"
    if emit:
        emit("analyzing", {"i": 0, "n": 1, "name": f"{os.path.basename(path)} 太重，轉檔中…（{mbps:.0f}Mbps/{fps:.0f}fps）"})
    cmd = [_ffmpeg(), "-y", "-v", "error", "-i", path,
           "-vf", "scale='min(1080,iw)':-2:flags=lanczos,fps=30",
           "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
           "-c:a", "aac", "-b:a", "128k", out + ".part.mp4"]
    r = _run(cmd, timeout=1800)
    if r.returncode != 0 or not os.path.exists(out + ".part.mp4"):
        return path, "轉檔失敗，使用原檔"
    os.replace(out + ".part.mp4", out)
    return out, f"已轉檔（原檔 {mbps:.0f}Mbps/{fps:.0f}fps）"


def _to_rfc3339(local_str):
    """'2026-11-02T22:00'（本機／台灣時間）→ '2026-11-02T14:00:00Z'"""
    d = dt.datetime.fromisoformat(local_str)
    if d.tzinfo is None:
        d = d.astimezone()          # 當成本機時區
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_key(path):
    st = os.stat(path)
    return hashlib.md5(f"{path}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8")).hexdigest()


def _thumb(path, t, key):
    out = os.path.join(THUMB_DIR, key + ".jpg")
    if not os.path.exists(out):
        _run([_ffmpeg(), "-hide_banner", "-y", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
              "-vf", "scale=-2:360", "-q:v", "5", out])
    if os.path.exists(out):
        with open(out, "rb") as f:
            return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")
    return ""


def _thumb_strip(path, dur, key, n=6):
    """滑過看片段用：抽 n 張小圖橫向拼成一張（每格 160×284）。只解關鍵影格，4K 大檔也只要一兩秒"""
    out = os.path.join(THUMB_DIR, key + "_strip2.jpg")
    if not os.path.exists(out):
        dur = max(0.5, float(dur or 0))
        # 關鍵影格少（手機長 GOP）時 fps 會不足 n 張 → tile 補黑格；tpad 用最後一格補滿
        vf = (f"fps={n}/{dur:.3f}:round=up,tpad=stop_mode=clone:stop={n},"
              f"scale=160:284:force_original_aspect_ratio=increase,crop=160:284,setsar=1,tile={n}x1")
        _run([_ffmpeg(), "-hide_banner", "-y", "-skip_frame", "nokey", "-i", path, "-vf", vf,
              "-frames:v", "1", "-q:v", "6", out], timeout=120)
        # 關鍵影格太少（整條都是同一格）→ 改完整解碼一次（先縮小再取，4K 也只要幾秒）
        try:
            from PIL import Image, ImageChops
            im = Image.open(out).convert("L")
            tiles = [im.crop((i * 160, 0, (i + 1) * 160, 284)) for i in range(n)]
            same = all(ImageChops.difference(tiles[0], t).getbbox() is None for t in tiles[1:])
            if same and dur > 3:
                vf2 = (f"scale=160:284:force_original_aspect_ratio=increase,crop=160:284,setsar=1,"
                       f"fps={n}/{dur:.3f}:round=up,tpad=stop_mode=clone:stop={n},tile={n}x1")
                _run([_ffmpeg(), "-hide_banner", "-y", "-i", path, "-vf", vf2, "-frames:v", "1", "-q:v", "6", out], timeout=180)
        except Exception:
            pass
    if os.path.exists(out):
        with open(out, "rb") as f:
            return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")
    return ""


def _has_person(path, duration):
    """快速看 3 格有沒有人；偵測不到 → 當近拍"""
    if vision is None or not vision.available():
        return None
    found = 0
    for frac in (0.25, 0.5, 0.75):
        t = max(0.0, duration * frac)
        r = _run([_ffmpeg(), "-hide_banner", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                  "-vf", "scale=320:-2", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"])
        raw = r.stdout
        if not raw or len(raw) % (320 * 3):
            continue
        h = len(raw) // (320 * 3)
        img = np.frombuffer(raw, dtype=np.uint8).reshape(h, 320, 3)
        try:
            people = vision.detect_people(np.stack([img]))
        except Exception:
            people = None
        if people and people[0] is not None:
            found += 1
    return found >= 1


class Api:
    """注意：公開屬性只能放簡單資料（dict/list/str），pywebview 啟動時會遞迴掃描所有公開屬性。"""
    def __init__(self):
        self._window = None
        self.settings = {**DEFAULT_SETTINGS, **_load_json(SETTINGS_FILE, {})}
        self.models = _load_json(MODELS_FILE, None)
        if self.models is None:
            self.models = self._migrate_models_csv()
        self.history = _load_json(HISTORY_FILE, [])
        self._busy = False
        self._events = []
        self._lock = threading.Lock()

    # ---------- 基本 ----------
    def _js(self, code):
        if self._window:
            try:
                self._window.evaluate_js(code)
            except Exception:
                pass

    def _emit(self, event, payload):
        with self._lock:
            self._events.append({"event": event, "payload": payload})
            if len(self._events) > 500:
                self._events = self._events[-500:]
        try:
            print(f"[{event}] {json.dumps(payload, ensure_ascii=False)[:160]}")
        except Exception:
            pass

    def poll_events(self):
        """介面每半秒來拿一次事件（不依賴後端推送）"""
        with self._lock:
            ev, self._events = self._events, []
        return {"events": ev, "busy": self._busy}

    def _migrate_models_csv(self):
        models = []
        for cand in (os.path.join(CORE, "模特資料.csv"), os.path.join(HERE, "模特資料.csv")):
            if os.path.exists(cand):
                for enc in ("utf-8-sig", "cp950"):
                    try:
                        with open(cand, encoding=enc, newline="") as f:
                            for r in csv.DictReader(f):
                                r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
                                if r.get("名字"):
                                    models.append({"name": r["名字"], "height": re.sub(r"\D", "", r.get("身高", "")),
                                                   "weight": re.sub(r"\D", "", r.get("體重", ""))})
                        break
                    except UnicodeDecodeError:
                        continue
                break
        _save_json(MODELS_FILE, models)
        return models

    def _models_map(self):
        return {m["name"].upper(): {"名字": m["name"], "身高": m.get("height", ""), "體重": m.get("weight", "")}
                for m in self.models if m.get("name")}

    def _beauty_names(self, force=False):
        if not force and getattr(self, "_beauty_cache", None) is not None:
            return self._beauty_cache
        try:
            g.DRAFT_FOLDER = self.settings["draft_folder"]
            self._beauty_cache = sorted(g.load_beauty().keys())
        except Exception:
            self._beauty_cache = []
        return self._beauty_cache

    def get_state(self):
        g.DRAFT_FOLDER = self.settings["draft_folder"]
        return {
            "settings": self.settings,
            "models": self.models,
            "beauty": self._beauty_names(),
            "history": self.history[-50:],
            "music": self.music_list(),
            "capcut_running": g.capcut_running(),
            "draft_folder_ok": os.path.isdir(self.settings["draft_folder"]),
            "vision_ok": bool(vision and vision.available()),
            "vision_error": VISION_ERROR or ("" if (vision and vision.available()) else getattr(vision, "LOAD_ERROR", "模型或套件缺少")),
            "version": VERSION,
            # 版型（LOOKBOOK／相機／郵票…）已停用，改用開頭動畫；設定 show_layouts=true 可叫回來
            "layouts": layouts.list_layouts() if (layouts and self.settings.get("show_layouts")) else [],
            "openings": self.opening_list(),
            "frozen": FROZEN,
            "log_file": LOG_FILE,
        }

    def capcut_running(self):
        return g.capcut_running()

    def ui_ready(self):
        """介面初始化完成後才把視窗顯示出來（載入中拖曳視窗會卡死）"""
        self._show_window()
        return True

    def _show_window(self):
        if getattr(self, "_shown", False) or not self._window:
            return
        self._shown = True
        try:
            self._window.show()
        except Exception:
            pass

    def session_save(self, data):
        """製作頁內容本機暫存（每次變動都存，關掉再開會回來）"""
        try:
            _save_json(SESSION_FILE, {"ts": dt.datetime.now().timestamp(), "products": data.get("products") or [], "cur": int(data.get("cur") or 0)})
        except Exception as e:
            print(f"session_save 失敗：{e}")
        return True

    def session_load(self):
        """讀回上次的製作頁內容；影片檔已經不存在的段落自動略過，沒有影片的商品也略過"""
        s = _load_json(SESSION_FILE, None)
        if not s:
            return None
        products = []
        for p in s.get("products") or []:
            clips = [c for c in (p.get("clips") or []) if c.get("path") and os.path.exists(c["path"])]
            if not clips and not (p.get("name") or "").strip():
                continue
            p = dict(p, clips=clips)
            if p.get("music") and not os.path.exists((p["music"] or {}).get("path", "")):
                p["music"] = None
            if p.get("background") and not os.path.exists(p["background"]):
                p["background"] = ""
            products.append(p)
        if not products:
            return None
        return {"products": products, "cur": min(int(s.get("cur") or 0), len(products) - 1), "ts": s.get("ts")}

    def session_clear(self):
        try:
            if os.path.exists(SESSION_FILE):
                os.remove(SESSION_FILE)
        except Exception:
            pass
        return True

    def save_settings(self, patch):
        self.settings.update(patch or {})
        _save_json(SETTINGS_FILE, self.settings)
        return self.settings

    def choose_folder(self, title=""):
        import webview
        r = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return r[0] if r else None

    def open_path(self, path):
        try:
            os.startfile(path)
            return True
        except Exception:
            return False

    def open_capcut(self):
        for cand in (os.path.join(os.environ.get("LOCALAPPDATA", ""), "CapCut", "Apps", "CapCut.exe"),):
            if os.path.exists(cand):
                subprocess.Popen([cand])
                return True
        return False

    # ---------- 片段 ----------
    def choose_videos(self):
        import webview
        r = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True,
                                           file_types=("影片 (*.mp4;*.mov;*.m4v)", "所有檔案 (*.*)"))
        return self.add_paths(list(r or []))

    def add_paths(self, paths):
        """分析影片：長度、縮圖、有沒有人；資料夾會展開"""
        files = []
        for p in paths or []:
            if os.path.isdir(p):
                for f in sorted(os.listdir(p)):
                    fp = os.path.join(p, f)
                    if f.lower().endswith(VIDEO_EXT):
                        files.append(fp)
                    elif f.lower().endswith(AUDIO_EXT):
                        files.append(fp)
            elif p.lower().endswith(VIDEO_EXT + AUDIO_EXT):
                files.append(p)
        out = {"clips": [], "music": []}
        total = len(files)
        for i, fp in enumerate(files):
            self._emit("analyzing", {"i": i, "n": total, "name": os.path.basename(fp)})
            if fp.lower().endswith(AUDIO_EXT):
                out["music"].append({"path": fp, "name": os.path.basename(fp)})
                continue
            try:
                src = fp
                fp, note = _maybe_transcode(fp, self._emit)
                if note:
                    print(f"{os.path.basename(src)}：{note}")
                dur = _probe_duration(fp)
                key = _file_key(fp)
                thumb = _thumb(fp, min(1.0, dur / 2), key)
                try:
                    strip = _thumb_strip(fp, dur, key)
                except Exception:
                    strip = ""
                person = _has_person(fp, dur)
                out["clips"].append({
                    "id": key, "path": fp, "source": src, "name": os.path.basename(src), "duration": round(dur, 1),
                    "thumb": thumb, "strip": strip, "closeup": (person is False), "person": person, "transcoded": bool(note),
                })
            except Exception as e:
                out["clips"].append({"id": _file_key(fp), "path": fp, "name": os.path.basename(fp),
                                     "duration": 0, "thumb": "", "closeup": False, "error": str(e)})
        self._emit("analyzing", {"i": total, "n": total, "name": ""})
        return out

    # ---------- 音樂試聽：能量曲線、拍點、建議起點 ----------
    def music_info(self, path):
        if not path or not os.path.exists(path):
            return {"ok": False, "error": "找不到音樂檔"}
        key = os.path.basename(path)
        meta = dict(self._music_index().get(key, {}))
        if not meta.get("wave") or not meta.get("bpm"):
            try:
                beats, bpm = g.detect_beats(path)
                if beats:
                    si, _ = g.music_start_index(beats, path, 20)
                    wave, dur = g.energy_curve(path)
                    meta.update({"bpm": round(bpm), "start": round(beats[si], 1), "wave": wave, "duration": dur, "beat0": round(beats[0], 3)})
                    idx = self._music_index()
                    idx.setdefault(key, {}).update({k: meta[k] for k in ("bpm", "start", "wave", "duration", "beat0")})
                    self._music_save(idx)
            except Exception as e:
                return {"ok": False, "error": str(e)}
        from urllib.parse import quote
        return {"ok": True, "bpm": meta.get("bpm"), "start": meta.get("start"), "wave": meta.get("wave") or [],
                "duration": meta.get("duration") or 0, "beat0": meta.get("beat0") or 0, "url": "/media?p=" + quote(path)}

    # ---------- 節奏預覽（和產生時同一套 planner） ----------
    def plan_preview(self, spec):
        import planner as P
        clips = [c for c in (spec.get("clips") or []) if c.get("path")]
        models = [c for c in clips if not c.get("closeup")]
        closes = [c for c in clips if c.get("closeup")]
        groups, order = {}, []
        for c in models:
            k = (c.get("model") or "").upper()
            if k not in groups:
                groups[k] = []
                order.append(k)
            groups[k].append(c)
        groups = [(k, groups[k]) for k in order]
        lay = layouts.LAYOUTS.get(spec.get("layout") or "") if layouts else None
        pattern = lay["pattern"].split(",") if lay else None
        bpm = None
        mp = (spec.get("music") or {}).get("path")
        if mp:
            meta = self._music_index().get(os.path.basename(mp), {})
            bpm = meta.get("bpm")
        beat_len = 60.0 / float(bpm) if bpm else 0.5
        out = {"bpm": bpm, "modes": {}}
        for mode in ("20", "25", "fit"):
            try:
                r = P.build_sequence(groups, closes, pattern, mode, beat_len, spec.get("layout") or "")
            except Exception as e:
                out["modes"][mode] = {"error": str(e)}
                continue
            seq = []
            for kind, c, nb in r["seq"]:
                sec = P.LAYOUT_SEC if kind == "layout" else round(nb * beat_len, 2)
                seq.append({"kind": kind, "name": (c or {}).get("name") or os.path.basename((c or {}).get("path", "")) if c else "版型",
                            "model": (c or {}).get("model", "") if c else "", "beats": nb, "sec": sec})
            out["modes"][mode] = {"seq": seq, "n_m": r["n_m"], "n_d": r["n_d"], "total": r["total"],
                                  "skipped": [x.get("name") or os.path.basename(x.get("path", "")) for x in r["skipped"]]}
        return out

    # ---------- 產生 ----------
    def generate(self, jobs):
        """jobs = [spec, ...]；在背景執行，進度用事件回報"""
        if self._busy:
            return {"ok": False, "error": "還在產生中"}
        if g.capcut_running():
            return {"ok": False, "error": "CapCut 還開著，請先完全關閉"}
        self._busy = True
        threading.Thread(target=self._generate_worker, args=(jobs,), daemon=True).start()
        return {"ok": True}

    def _generate_worker(self, jobs):
        # 卡住時每 30 秒把所有執行緒在哪一行印到黑色視窗，方便除錯
        try:
            import faulthandler
            faulthandler.dump_traceback_later(30, repeat=True)
        except Exception:
            pass
        g.DRAFT_FOLDER = self.settings["draft_folder"]
        g.BEAUTY_FILE = BEAUTY_PATH
        g.STYLE_FILE = os.path.join(CORE, "樣式.json")
        models = self._models_map()
        results = []
        try:
            for ji, spec in enumerate(jobs):
                name = spec.get("name") or f"草稿{ji + 1}"
                # 太重的相機檔（舊的工作階段帶回來、或轉檔失敗的）在產生前再轉一次；轉過的直接用快取
                for c in spec.get("clips") or []:
                    try:
                        p2, note = _maybe_transcode(c.get("path", ""), lambda ev, p: self._emit("log", {"job": ji, "msg": p.get("name", "")}))
                        if p2 != c.get("path"):
                            self._emit("log", {"job": ji, "msg": f"{os.path.basename(c.get('path', ''))}：{note}"})
                            c["path"] = p2
                    except Exception as e:
                        self._emit("log", {"job": ji, "msg": f"轉檔檢查失敗：{e}"})
                # 需要底圖的版型沒選底圖 → 自動用當期「形象擺拍」（從影片所在的 Dropbox 檔期資料夾找，找不到就最新檔期）
                try:
                    lay = layouts.LAYOUTS.get(spec.get("layout") or "") if layouts else None
                    needs = bool(lay and any(r.get("role") == "P" for r in lay.get("regions", [])))
                    if needs and not (spec.get("background") or "").strip():
                        first = next((c.get("path") for c in spec.get("clips") or [] if c.get("path")), "")
                        pool, period = self._period_bg_pool(os.path.dirname(first) if first else (self._bg_root() or ""))
                        if pool:
                            spec["background"] = pool[0]["path"]
                            self._emit("log", {"job": ji, "msg": f"自動帶底圖：{os.path.basename(period)}／{pool[0]['name']}"})
                        else:
                            self._emit("log", {"job": ji, "msg": "這個版型需要底圖，但找不到當期的形象擺拍／空景"})
                except Exception:
                    traceback.print_exc()
                # 開頭動畫：random → 從開頭動畫庫挑一支（這批不重複、最近用過的往後排）
                try:
                    op = (spec.get("opening") or "").strip()
                    if op == "random":
                        used = [j.get("opening") for j in jobs if j.get("opening") and j.get("opening") != "random"]
                        pick = self.opening_pick(exclude=used)
                        spec["opening"] = pick or ""
                        if pick:
                            self._emit("log", {"job": ji, "msg": f"自動挑開頭動畫：{os.path.splitext(os.path.basename(pick))[0]}"})
                        else:
                            self._emit("log", {"job": ji, "msg": "找不到開頭動畫庫（官網部門\\開頭動畫），這支不疊開頭"})
                    elif op and not os.path.exists(op):
                        self._emit("log", {"job": ji, "msg": f"開頭動畫檔不在了：{os.path.basename(op)}，這支不疊開頭"})
                        spec["opening"] = ""
                except Exception:
                    traceback.print_exc()
                spec.setdefault("options", {})
                for k in ("max_total", "mid_closeup", "person_center", "stabilize", "beauty", "length_mode"):
                    spec["options"].setdefault(k, self.settings.get(k))
                self._emit("job_start", {"job": ji, "name": name})

                def log(msg, ji=ji):
                    self._emit("log", {"job": ji, "msg": str(msg)})

                def prog(i, n, f, ji=ji):
                    self._emit("progress", {"job": ji, "i": i, "n": n, "name": f})

                try:
                    r = engine.build_draft(spec, models, log=log, progress=prog)
                except Exception as e:
                    r = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()}
                    self._report_error(f"產生草稿：{name}", r["error"], r["trace"])
                r["job"] = ji
                r["name"] = name
                r["music"] = (spec.get("music") or {}).get("path")
                r["colors"] = spec.get("colors") or []
                r["sku"] = spec.get("sku") or ""
                r["features"] = spec.get("features") or []
                r["background"] = spec.get("background") or ""
                r["opening"] = r.get("opening") or ""
                r["opening_name"] = os.path.splitext(os.path.basename(r["opening"]))[0] if r["opening"] else ""
                r["layout"] = spec.get("layout") or ""
                r["layout_name"] = (layouts.LAYOUTS.get(r["layout"], {}).get("name") if (layouts and r["layout"]) else "") or ""
                r["category"] = spec.get("category") or ""
                r["created"] = dt.datetime.now().isoformat(timespec="seconds")
                r["exported"] = False
                r["id"] = hashlib.md5(f"{name}|{r['created']}".encode("utf-8")).hexdigest()[:10]
                results.append(r)
                if r.get("ok"):
                    self.history.append({k: v for k, v in r.items() if k not in ("trace",)})
                    _save_json(HISTORY_FILE, self.history)
                    self._status_set(r.get("sku") or name, {"name": name, "draft_at": r["created"],
                                                             "draft_by": os.environ.get("COMPUTERNAME", "")})
                    if r.get("music"):
                        self._music_touch(r["music"], bpm=r.get("bpm"), start=r.get("music_start"))
                    self._cloud_event("draft", name, r.get("layout_name") or "標準")
                self._emit("job_done", r)
        finally:
            try:
                import faulthandler
                faulthandler.cancel_dump_traceback_later()
            except Exception:
                pass
            self._busy = False
            self._emit("all_done", {"results": results})

    # ---------- 已匯出 ----------
    def history_delete(self, ids):
        """刪除完成頁的紀錄（只刪 app 內的紀錄，不動 CapCut 草稿、YouTube、商品頁）"""
        ids = set(ids or [])
        before = len(self.history)
        self.history = [h for h in self.history if h.get("id") not in ids]
        _save_json(HISTORY_FILE, self.history)
        return {"ok": True, "removed": before - len(self.history)}

    def rebind_product(self, hist_id, item):
        """完成頁：草稿選錯商品、影片已剪好 → 把這筆紀錄改綁到另一個商品（名稱／貨號／顏色換掉，成品檔沿用）"""
        rec = next((h for h in self.history if h.get("id") == hist_id), None)
        if not rec:
            return {"ok": False, "error": "找不到紀錄"}
        if rec.get("youtube_id"):
            return {"ok": False, "error": "這支已經上傳 YouTube，請先「撤下」再改綁"}
        new_name = (item or {}).get("name", "").strip()
        if not new_name:
            return {"ok": False, "error": "沒有選商品"}
        old_key = rec.get("sku") or rec.get("name")
        rec["rebound_from"] = rec.get("name")
        rec["name"] = new_name
        rec["sku"] = (item.get("sku") or "").strip()
        rec["colors"] = item.get("colors") or []
        rec["features"] = item.get("features") or []
        rec["category"] = item.get("category") or ""
        _save_json(HISTORY_FILE, self.history)
        # 製作狀態：舊商品的「已產草稿」拿掉（若沒有別筆），新商品記上
        try:
            idx = self._status_index()
            if old_key in idx and not any(h is not rec and (h.get("sku") or h.get("name")) == old_key and h.get("ok") for h in self.history):
                idx.pop(old_key, None)
                _save_json(self._status_path(), idx)
        except Exception:
            traceback.print_exc()
        self._status_set(rec.get("sku") or new_name, {"name": new_name, "draft_at": rec.get("created"),
                                                      "exported_at": rec.get("exported_at"), "export_file": rec.get("export_file")})
        return {"ok": True, "record": rec}

    def pick_export_file(self, hist_id):
        """手動指定這筆的成品檔（改綁商品後檔名對不上時用）"""
        import webview
        rec = next((h for h in self.history if h.get("id") == hist_id), None)
        if not rec:
            return {"ok": False, "error": "找不到紀錄"}
        r = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                                            directory=self.settings.get("export_folder") or "", file_types=("影片 (*.mp4;*.mov)",))
        if not r:
            return {"ok": False, "error": "取消"}
        rec["export_file"] = r[0]
        rec["exported"] = True
        rec["exported_at"] = rec.get("exported_at") or dt.datetime.now().isoformat(timespec="seconds")
        _save_json(HISTORY_FILE, self.history)
        self._status_set(rec.get("sku") or rec["name"], {"name": rec["name"], "exported_at": rec["exported_at"], "export_file": r[0]})
        return {"ok": True, "file": r[0], "record": rec}

    def mark_exported(self, hist_id, exported):
        rec = next((h for h in self.history if h.get("id") == hist_id), None)
        if not rec:
            return {"ok": False, "error": "找不到紀錄"}
        found = self._find_export(rec["name"])
        if exported and not found:
            return {"ok": False, "error": "成品資料夾裡沒有這支影片", "export_folder": self.settings.get("export_folder")}
        rec["exported"] = bool(exported)
        rec["export_file"] = found if exported else None
        rec["exported_at"] = dt.datetime.now().isoformat(timespec="seconds") if exported else None
        _save_json(HISTORY_FILE, self.history)
        for key in {rec.get("sku") or "", rec["name"]}:
            self._status_set(key, {"name": rec["name"], "exported_at": rec["exported_at"], "export_file": rec["export_file"]})
        uses = None
        if exported:
            self._cloud_event("export", rec["name"])
        if rec.get("music"):
            uses = self._music_use(rec["music"], rec["name"], +1 if exported else -1)
        if rec.get("background"):
            self._bg_use(rec["background"], rec["name"], +1 if exported else -1)
        return {"ok": True, "file": found, "music_uses": uses}

    def _find_export(self, name):
        folder = self.settings.get("export_folder")
        if not folder or not os.path.isdir(folder):
            return None
        safe = re.sub(r"[\\/:*?\"<>|]", "", name)
        cands = []
        for f in os.listdir(folder):
            if f.lower().endswith((".mp4", ".mov")) and (safe in f or f.startswith(safe[:12])):
                fp = os.path.join(folder, f)
                cands.append((os.path.getmtime(fp), fp))
        return max(cands)[1] if cands else None

    # ---------- 音樂庫 ----------
    # ---------- 待製作清單（Google 試算表 → Apps Script 網址） ----------
    def _status_path(self):
        """全公司共用的製作狀態檔：放在音樂庫的上一層（官網部門），沒有就放本機"""
        mf = self.settings.get("music_folder") or ""
        parent = os.path.dirname(mf.rstrip("\\/")) if mf else ""
        if parent and os.path.isdir(parent):
            return os.path.join(parent, STATUS_NAME)
        return os.path.join(DATA_DIR, STATUS_NAME)

    def _status_index(self):
        return _load_json(self._status_path(), {}) or {}

    def _status_set(self, key, patch):
        if not key:
            return
        try:
            idx = self._status_index()
            rec = idx.get(key, {})
            rec.update(patch)
            idx[key] = rec
            _save_json(self._status_path(), idx)
        except Exception:
            traceback.print_exc()

    def todo_status(self):
        """待製作頁切回來時只重讀製作狀態（本機，很快），不用重抓 Shopline"""
        return {"ok": True, "status": self._status_index(),
                "history_names": [h.get("name") for h in self.history if h.get("ok")],
                "history_skus": [h.get("sku") for h in self.history if h.get("ok") and h.get("sku")]}

    def todo_list(self, force=False, days=90):
        """待製作清單：直接從 Shopline 抓近期商品（依排程上架日分檔期）；沒有工具密碼時才退回上架排程試算表"""
        cache = _load_json(TODO_CACHE, None)
        data, err, source = None, "", "shopline"
        fresh = cache and cache.get("source") == "shopline" and cache.get("days") == days and (dt.datetime.now().timestamp() - cache.get("ts", 0) < 300)
        if (self.settings.get("tool_password") or "").strip():
            if fresh and not force:
                data = cache.get("data")
            else:
                try:
                    data = self._cloud("upcoming", {"days": days}, timeout=60)
                    if not data.get("ok"):
                        raise RuntimeError(data.get("error") or "雲端沒有回應")
                    _save_json(TODO_CACHE, {"source": "shopline", "days": days, "ts": dt.datetime.now().timestamp(), "data": data})
                except Exception as e:
                    err = f"Shopline 讀取失敗：{type(e).__name__}: {e}"
                    data = cache.get("data") if (cache and cache.get("source") == "shopline") else None
        else:
            err = "設定頁還沒填工具密碼"
        if not data:
            url = (self.settings.get("todo_url") or "").strip()
            if url:
                source = "sheet"
                try:
                    import urllib.request
                    req = urllib.request.Request(url, headers={"User-Agent": "VVReels/1.0"})
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    err = ""
                except Exception as e:
                    err = (err + "；" if err else "") + f"排程表讀取失敗：{type(e).__name__}: {e}"
        if not data:
            return {"ok": False, "error": err or "沒有資料", "tabs": [], "status": {}}
        tabs = data.get("tabs", [])
        if source == "sheet":
            # 試算表分頁：只認 YYYY-MMDD（前面可帶 emoji），其餘分頁不列
            kept = []
            for t in tabs:
                m = re.search(r"(20\d{2})-(\d{2})(\d{2})", t.get("tab", ""))
                if not m:
                    continue
                t["year"], t["month"], t["day"], t["code"] = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(2) + m.group(3)
                kept.append(t)
            tabs = sorted(kept, key=lambda t: (t["year"], t["month"], t["day"]), reverse=True)[:12]
        for t in tabs:
            t["ym"] = f"{t.get('year') or ''}-{t.get('month')}"
        cached_at = (cache or {}).get("ts") if (fresh and not force) or (err and source == "shopline") else dt.datetime.now().timestamp()
        return {"ok": True, "tabs": tabs, "status": self._status_index(), "error": err, "source": source,
                "cached_at": dt.datetime.fromtimestamp(cached_at or dt.datetime.now().timestamp()).strftime("%H:%M"),
                "history_names": [h.get("name") for h in self.history if h.get("ok")],
                "history_skus": [h.get("sku") for h in self.history if h.get("ok") and h.get("sku")]}


    # ---------- 匯出自動偵測：成品資料夾一出現對應檔案就自動勾「已匯出」 ----------
    def _watch_exports(self):
        seen_size = {}
        while True:
            try:
                time.sleep(5)
                folder = self.settings.get("export_folder")
                if not folder or not os.path.isdir(folder):
                    continue
                latest = {}
                for h in self.history:
                    if h.get("ok") and not h.get("exported"):
                        k = h.get("name")
                        if k and (k not in latest or h.get("created", "") > latest[k].get("created", "")):
                            latest[k] = h
                for name, rec in latest.items():
                    f = self._find_export(name)
                    if not f:
                        continue
                    try:
                        created = dt.datetime.fromisoformat(rec.get("created")).timestamp()
                    except Exception:
                        created = 0
                    if os.path.getmtime(f) < created:
                        continue                      # 更早以前匯出的舊檔，不算
                    size = os.path.getsize(f)
                    if seen_size.get(f) != size:      # CapCut 還在寫檔：等到大小連續兩次一樣
                        seen_size[f] = size
                        continue
                    r = self.mark_exported(rec["id"], True)
                    if r.get("ok"):
                        print(f"[auto_exported] {name} ← {os.path.basename(f)}")
                        self._emit("auto_exported", {"id": rec["id"], "name": name, "file": f})
            except Exception:
                traceback.print_exc()

    # ---------- 錯誤自動回報到雲端（同事那邊出錯不用截圖） ----------
    _err_sent = {}

    def _report_error(self, context, err, trace=""):
        try:
            key = f"{context}|{str(err)[:80]}"
            now = time.time()
            if now - self._err_sent.get(key, 0) < 600:
                return
            self._err_sent[key] = now
            if not (self.settings.get("tool_password") or "").strip():
                return
            try:
                with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
                    tail = "\n".join(f.read().splitlines()[-80:])
            except Exception:
                tail = ""
            payload = {"version": VERSION, "computer": os.environ.get("COMPUTERNAME", ""), "user": os.environ.get("USERNAME", ""),
                       "context": context, "error": str(err)[:500], "trace": (trace or "")[-3000:], "log": tail[-6000:]}
            threading.Thread(target=lambda: self._cloud("errorReport", payload, timeout=20), daemon=True).start()
        except Exception:
            pass

    def _cloud_event(self, kind, name, extra=""):
        """產草稿／匯出／上傳 回報雲端（每日 LINE 摘要用），背景送、失敗不管"""
        if not (self.settings.get("tool_password") or "").strip():
            return
        payload = {"kind": kind, "name": name, "by": os.environ.get("COMPUTERNAME", "") or os.environ.get("USERNAME", ""), "extra": extra}
        threading.Thread(target=lambda: self._safe_cloud("event", payload), daemon=True).start()

    def _safe_cloud(self, action, payload):
        try:
            self._cloud(action, payload, timeout=20)
        except Exception:
            pass

    def line_setup(self, token, secret):
        try:
            return self._cloud("lineSetup", {"lineToken": token or "", "lineSecret": secret or ""}, timeout=20)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def line_ping(self, send=False):
        try:
            return self._cloud("linePing", {"send": bool(send)}, timeout=30)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def line_summary_now(self):
        try:
            return self._cloud("lineSummaryNow", {}, timeout=60)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def error_list(self):
        try:
            return self._cloud("errorList", {}, timeout=20)
        except Exception as e:
            return {"ok": False, "error": str(e), "items": []}

    # ---------- 更新與錯誤回報 ----------
    def check_update(self):
        """到 GitHub Releases 看有沒有新版本"""
        import urllib.request
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                         headers={"User-Agent": "VVReels", "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                j = json.loads(resp.read().decode("utf-8"))
            tag = (j.get("tag_name") or "").lstrip("v")
            asset = next((a for a in j.get("assets", []) if "-win-" in a.get("name", "") and a.get("name", "").endswith(".zip")), None)
            setup = next((a for a in j.get("assets", []) if a.get("name", "").endswith(".exe")), None)

            def ver(v):
                return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)
            newer = bool(tag) and ver(tag) > ver(VERSION)
            return {"ok": True, "current": VERSION, "latest": tag, "available": newer,
                    "zip": asset and asset.get("browser_download_url"), "setup": setup and setup.get("browser_download_url"),
                    "notes": (j.get("body") or "")[:600], "can_auto": bool(FROZEN and asset)}
        except Exception as e:
            return {"ok": False, "error": str(e), "current": VERSION}

    def do_update(self, zip_url):
        """下載新版 → 解壓到 _update → 交給 update.bat 在程式關閉後覆蓋並重開"""
        import urllib.request
        import zipfile
        import tempfile
        if not FROZEN:
            return {"ok": False, "error": "開發模式不支援自動更新"}
        try:
            self._emit("update_progress", {"msg": "下載中…", "pct": 0})
            tmp = os.path.join(tempfile.gettempdir(), "vvreels_update.zip")
            req = urllib.request.Request(zip_url, headers={"User-Agent": "VVReels"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        self._emit("update_progress", {"msg": f"下載中 {done * 100 // total}%", "pct": done / total})
            upd = os.path.join(APP_DIR, "_update")
            import shutil
            shutil.rmtree(upd, ignore_errors=True)
            os.makedirs(upd, exist_ok=True)
            self._emit("update_progress", {"msg": "解壓縮…", "pct": 1})
            with zipfile.ZipFile(tmp) as z:
                z.extractall(upd)
            # 壓縮檔裡可能多一層資料夾
            inner = [d for d in os.listdir(upd) if os.path.isdir(os.path.join(upd, d))]
            src = os.path.join(upd, inner[0]) if len(inner) == 1 and not os.path.exists(os.path.join(upd, os.path.basename(sys.executable))) else upd
            exe = os.path.basename(sys.executable)
            bat = os.path.join(APP_DIR, "update.bat")
            # 批次檔用系統編碼寫（cmd 不吃 UTF-8），並等舊程式真的結束、覆蓋失敗會重試
            with open(bat, "w", encoding="mbcs", errors="replace") as f:
                f.write("@echo off\r\n")
                f.write(f'cd /d "{APP_DIR}"\r\n')
                f.write("set n=0\r\n")
                f.write(":wait\r\n")
                f.write("set /a n+=1\r\n")
                f.write(f'tasklist /FI "IMAGENAME eq {exe}" 2>nul | find /I "{exe}" >nul\r\n')
                f.write("if not errorlevel 1 ( if %n% LSS 60 ( timeout /t 1 /nobreak >nul & goto wait ) else ( taskkill /F /IM \"" + exe + "\" >nul 2>&1 & timeout /t 2 /nobreak >nul ) )\r\n")
                f.write(f'robocopy "{src}" "{APP_DIR}" /E /R:5 /W:2 /NFL /NDL /NJH /NJS /NP >nul\r\n')
                f.write(f'rmdir /S /Q "{upd}"\r\n')
                f.write(f'start "" "{os.path.join(APP_DIR, exe)}"\r\n')
                f.write('del "%~f0"\r\n')
            subprocess.Popen(["cmd", "/c", bat], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)
            self._emit("update_progress", {"msg": "重新啟動中…", "pct": 1})
            threading.Timer(1.0, lambda: os._exit(0)).start()
            return {"ok": True}
        except Exception as e:
            traceback.print_exc()
            return {"ok": False, "error": str(e)}

    def error_report(self):
        """把最近的紀錄複製到剪貼簿，並回傳檔案位置"""
        try:
            with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()[-300:]
        except Exception:
            lines = ["(沒有紀錄檔)"]
        text = "\n".join([f"VV 實穿草稿產生器 {VERSION}  {os.environ.get('COMPUTERNAME', '')}  {dt.datetime.now().isoformat(timespec='seconds')}", ""] + lines)
        ok = False
        try:
            p = subprocess.Popen(["clip"], stdin=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            p.communicate(("\ufeff" + text).encode("utf-16le"))
            ok = p.returncode == 0
        except Exception:
            pass
        return {"ok": ok, "file": LOG_FILE, "lines": len(lines)}

    # ---------- 雲端（Cloud Function：YouTube 授權／AI 標題／商品頁嵌入） ----------
    def _cloud(self, action, payload=None, timeout=60):
        import urllib.request
        import urllib.error
        url = (self.settings.get("cloud_url") or "").strip()
        pw = (self.settings.get("tool_password") or "").strip()
        if not url:
            raise RuntimeError("尚未設定雲端網址")
        if not pw:
            raise RuntimeError("尚未輸入工具密碼（設定頁）")
        body = json.dumps(dict(payload or {}, action=action), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "X-Tool-Password": pw})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                j = json.loads(e.read().decode("utf-8"))
                raise RuntimeError(j.get("error") or f"HTTP {e.code}")
            except ValueError:
                raise RuntimeError(f"HTTP {e.code}")

    def cloud_ping(self):
        try:
            return dict(self._cloud("ping", timeout=20), ok=True)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def cloud_setup(self, client_id="", client_secret="", anthropic_key=None, todo_url=None):
        try:
            payload = {"clientId": client_id or "", "clientSecret": client_secret or ""}
            if anthropic_key is not None:
                payload["anthropicKey"] = anthropic_key
            if todo_url is not None:
                payload["todoUrl"] = todo_url
            r = self._cloud("ytSetup", payload)
            return {"ok": True, "authUrl": r.get("authUrl")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_url(self, url):
        import webbrowser
        webbrowser.open(url)
        return True

    def yt_titles(self, hist_id, extra="", avoid=None):
        rec = next((h for h in self.history if h.get("id") == hist_id), None)
        if not rec:
            return {"ok": False, "error": "找不到紀錄"}
        models = sorted({(s.get("model") or "") for s in rec.get("segments", []) if s.get("model")})
        try:
            r = self._cloud("aiTitle", {"name": rec.get("name", ""), "category": rec.get("category", ""), "features": rec.get("features", []),
                                        "colors": rec.get("colors", []), "models": "、".join(models), "extra": extra, "n": 3,
                                        "avoid": [a.split("｜")[0] for a in (avoid or [])][-12:]})
            return {"ok": True, "titles": r.get("titles", []), "ai_error": r.get("ai_error") or ""}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def product_lookup(self, q):
        """製作頁查找商品：Shopline 優先（商品描述裡有模特著用尺碼），排程表快取當備援"""
        q = (q or "").strip()
        if len(q) < 2:
            return {"ok": True, "items": []}
        ql = q.lower().replace(" ", "")
        out, hints, err = [], [], ""
        # ---- Shopline ----
        if (self.settings.get("tool_password") or "").strip():
            try:
                r = self._cloud("findProducts", {"name": q}, timeout=20)
                groups = {}
                for it in r.get("items", []):
                    title = it.get("title", "")
                    base, color = title, ""
                    if "-" in title:
                        base, color = title.rsplit("-", 1)
                        base, color = base.strip(), color.strip()
                    g_ = groups.setdefault(base, {"source": "shopline", "name": base, "sku": "", "colors": [], "features": [],
                                                  "category": "", "models": [], "specs": [], "product_ids": [], "statuses": []})
                    if color and color not in g_["colors"]:
                        g_["colors"].append(color)
                    g_["product_ids"].append(it.get("id"))
                    g_["statuses"].append(it.get("status"))
                    for sz in it.get("sizes") or []:
                        if sz not in g_["specs"]:
                            g_["specs"].append(sz)
                    if not g_["models"] and it.get("models"):
                        g_["models"] = [dict(m) for m in it["models"]]
                    if not g_["sku"] and it.get("sku"):
                        # 26AU0406-050-F（顏色-尺碼）→ 26AU0406
                        g_["sku"] = re.sub(r"^([A-Z0-9]{6,10})(?:[-_ ].*)?$", r"\1", it["sku"].strip().upper())
                for g_ in groups.values():
                    if len(g_["specs"]) == 1:
                        for m in g_["models"]:
                            if not m.get("size"):
                                m["size"] = g_["specs"][0]
                    out.append(g_)
            except Exception as e:
                err = str(e)
        else:
            hints.append("Shopline：設定頁還沒填工具密碼")
        # ---- 排程表（快取）：補 Shopline 沒有的款，或補模特尺碼 ----
        try:
            cache = _load_json(TODO_CACHE, None) or {}
            for t in (cache.get("data") or {}).get("tabs", []):
                for it in t.get("items", []):
                    hay = (it.get("name", "") + it.get("sku", "")).lower().replace(" ", "")
                    if ql not in hay:
                        continue
                    models = [dict(m) for m in it.get("models", [])]
                    specs = [x for x in it.get("specs", []) if x]
                    if len(specs) == 1:
                        for m in models:
                            if not m.get("size"):
                                m["size"] = specs[0]
                    hit = next((x for x in out if x["name"] == it.get("name")), None)
                    if hit:
                        hit["tab"] = t.get("tab")
                        if not hit.get("sku"):
                            hit["sku"] = it.get("sku", "")
                        if not hit.get("features"):
                            hit["features"] = it.get("features", [])
                        if not hit.get("category"):
                            hit["category"] = it.get("category", "")
                        if not any(m.get("size") for m in hit.get("models", [])) and models:
                            hit["models"] = models
                        continue
                    out.append({"source": "sheet", "tab": t.get("tab"), "name": it.get("name"), "sku": it.get("sku", ""),
                                "colors": it.get("colors", []), "features": it.get("features", []), "specs": specs,
                                "category": it.get("category", ""), "models": models})
            if not cache.get("data") and not (self.settings.get("todo_url") or "").strip():
                hints.append("排程表：設定頁還沒填待製作清單網址")
        except Exception:
            traceback.print_exc()
        if hints and not err:
            err = "；".join(hints)
        return {"ok": True, "items": out[:20], "error": err}

    # ---------- 批次製作 ----------
    _FW = str.maketrans('\\/:*?"<>|', '＼／：＊？＂＜＞｜')

    @staticmethod
    def _norm_name(s):
        import unicodedata
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).lower()

    def make_folders(self, parent, names):
        """待製作頁：把勾選的商品名稱一個一個建成資料夾（Windows 不能用的符號換全形）"""
        made, existed, failed = [], [], []
        for n in names or []:
            safe = (n or "").strip().translate(self._FW).rstrip(". ").strip()
            if not safe:
                continue
            fp = os.path.join(parent, safe)
            try:
                if os.path.isdir(fp):
                    existed.append(safe)
                else:
                    os.makedirs(fp)
                    made.append(safe)
            except Exception as e:
                failed.append(f"{safe}：{e}")
        return {"ok": True, "made": made, "existed": existed, "failed": failed}

    def _match_product(self, folder_name):
        """資料夾名 → 商品（排程表快取精確比對 → 包含比對 → Shopline）"""
        q = self._norm_name(folder_name)
        if not q:
            return None
        cache = _load_json(TODO_CACHE, None) or {}
        items = [it for t in (cache.get("data") or {}).get("tabs", []) for it in t.get("items", [])]

        def pack(it, source):
            models = [dict(m) for m in it.get("models", [])]
            specs = [x for x in it.get("specs", []) if x]
            if len(specs) == 1:
                for m in models:
                    if not m.get("size"):
                        m["size"] = specs[0]
            return {"source": source, "name": it.get("name", ""), "sku": it.get("sku", ""), "colors": it.get("colors", []),
                    "features": it.get("features", []), "category": it.get("category", ""), "specs": specs, "models": models}
        for it in items:
            if self._norm_name(it.get("name")) == q or (it.get("sku") and self._norm_name(it["sku"]) == q):
                return pack(it, "sheet")
        hits = [it for it in items if q in self._norm_name(it.get("name")) or self._norm_name(it.get("name")) in q]
        if len(hits) == 1:
            return pack(hits[0], "sheet~")
        try:
            r = self.product_lookup(folder_name)
            for it in r.get("items", []):
                if self._norm_name(it.get("name")) == q:
                    return dict(it, source="shopline")
            if len(r.get("items", [])) == 1:
                return dict(r["items"][0], source="shopline~")
        except Exception:
            traceback.print_exc()
        return None

    def _period_bg_pool(self, parent):
        """當期底圖池：找到 官網部門\\<檔期>，用裡面的「形象擺拍」（沒有就「空景」），依用過次數排序"""
        root = self._bg_root()
        period = ""
        if root:
            p = os.path.normpath(parent)
            rootn = os.path.normcase(os.path.normpath(root))
            while True:
                up = os.path.dirname(p)
                if not up or up == p:
                    break
                if os.path.normcase(up) == rootn:
                    period = p
                    break
                p = up
            if not period:
                try:
                    for d in sorted(os.listdir(root), reverse=True):
                        if os.path.isdir(os.path.join(root, d, "形象擺拍")) or os.path.isdir(os.path.join(root, d, BG_SUBDIR)):
                            period = os.path.join(root, d)
                            break
                except Exception:
                    pass
        if not period:
            return [], ""
        idx = self._bg_index()
        pool = []
        for sub in ("形象擺拍", BG_SUBDIR):
            folder = os.path.join(period, sub)
            if not os.path.isdir(folder):
                continue
            for dp, dns, fns in os.walk(folder):
                if dp[len(folder):].count(os.sep) >= 2:
                    dns[:] = []
                for f in sorted(fns):
                    if f.lower().endswith(IMAGE_EXT) and not f.startswith("."):
                        fp = os.path.join(dp, f)
                        pool.append({"path": fp, "name": os.path.splitext(f)[0], "group": f"{os.path.basename(period)}/{sub}",
                                     "uses": len(idx.get(self._bg_key(fp), {}).get("uses", []))})
            if pool:
                break
        pool.sort(key=lambda i: i["uses"])
        return pool, period

    def scan_batch(self, parent):
        """製作頁匯入大資料夾：每個子資料夾一個商品（子資料夾再一層也收），配對商品、附當期底圖池"""
        if not parent or not os.path.isdir(parent):
            return {"ok": False, "error": "資料夾不存在"}
        items = []
        try:
            subs = sorted(os.listdir(parent))
        except Exception as e:
            return {"ok": False, "error": str(e)}
        for i, d in enumerate(subs):
            fp = os.path.join(parent, d)
            if not os.path.isdir(fp) or d.startswith(".") or d.startswith("_"):
                continue
            vids = []
            for dp, dns, fns in os.walk(fp):
                if dp[len(fp):].count(os.sep) >= 1:
                    dns[:] = []
                vids += [os.path.join(dp, f) for f in sorted(fns) if f.lower().endswith(VIDEO_EXT) and not f.startswith(".")]
            if not vids:
                continue
            self._emit("analyzing", {"i": i, "n": len(subs), "name": f"配對 {d}"})
            items.append({"folder": fp, "folder_name": d, "videos": vids, "match": self._match_product(d)})
        self._emit("analyzing", {"i": 0, "n": 0, "name": ""})
        pool, period = self._period_bg_pool(parent)
        return {"ok": True, "items": items, "bg_pool": pool, "period": period}

    def _warm_todo(self):
        """開啟時在背景先抓排程表，之後查找才不會等"""
        try:
            if (self.settings.get("tool_password") or self.settings.get("todo_url") or "").strip():
                self.todo_list()
        except Exception:
            pass

    def find_products(self, name):
        try:
            r = self._cloud("findProducts", {"name": name})
            return {"ok": True, "items": r.get("items", [])}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def publish(self, hist_id, title, product_ids, options=None):
        rec = next((h for h in self.history if h.get("id") == hist_id), None)
        if not rec:
            return {"ok": False, "error": "找不到紀錄"}
        path = rec.get("export_file") or self._find_export(rec["name"])
        if not path or not os.path.exists(path):
            return {"ok": False, "error": "成品資料夾裡沒有這支影片，請先勾「已匯出」"}
        if self._busy:
            return {"ok": False, "error": "正在處理中"}
        self._busy = True
        threading.Thread(target=self._publish_worker, args=(rec, path, title, list(product_ids or []), options or {}), daemon=True).start()
        return {"ok": True}

    def batch_publish(self, items, options=None):
        """批次：items=[{id, title?, product_ids?}]；沒給標題就用 AI 第一句、沒給商品就用同名各色。一支一支做，失敗不中斷"""
        if self._busy:
            return {"ok": False, "error": "正在處理中"}
        jobs = []
        for it in items or []:
            rec = next((h for h in self.history if h.get("id") == it.get("id")), None)
            if rec:
                jobs.append((rec, it))
        if not jobs:
            return {"ok": False, "error": "沒有可處理的項目"}
        self._busy = True
        threading.Thread(target=self._batch_worker, args=(jobs, options or {}), daemon=True).start()
        return {"ok": True, "n": len(jobs)}

    def _batch_worker(self, jobs, options):
        n = len(jobs)
        done = 0
        try:
            for i, (rec, it) in enumerate(jobs):
                hid = rec["id"]
                self._emit("batch_progress", {"i": i, "n": n, "id": hid, "name": rec.get("name")})
                try:
                    path = rec.get("export_file") or self._find_export(rec["name"])
                    if not rec.get("youtube_id") and (not path or not os.path.exists(path)):
                        raise RuntimeError("找不到成品檔")
                    title = it.get("title") or rec.get("youtube_title") or ""
                    if not title and not rec.get("youtube_id"):
                        try:
                            t = self.yt_titles(hid)
                            title = (t.get("titles") or [""])[0]
                        except Exception:
                            title = ""
                    ids = it.get("product_ids")
                    if ids is None and options.get("embed", True):
                        try:
                            fp = self.find_products(rec["name"])
                            ids = [x["id"] for x in fp.get("items", []) if x.get("exact")]
                        except Exception:
                            ids = []
                    if not options.get("embed", True):
                        ids = []
                    ok = self._publish_one(rec, path, title, list(ids or []), options)
                    done += 1 if ok else 0
                except Exception as e:
                    traceback.print_exc()
                    self._emit("pub_done", {"id": hid, "ok": False, "error": str(e), "record": rec})
        finally:
            self._busy = False
            self._emit("batch_done", {"n": n, "done": done})

    def yt_set_status(self, hist_id, privacy="public", publish_at=""):
        """改單支的公開狀態（立即公開／私人／定時公開）"""
        rec = next((h for h in self.history if h.get("id") == hist_id), None)
        if not rec or not rec.get("youtube_id"):
            return {"ok": False, "error": "這支還沒上傳 YouTube"}
        try:
            r = self._cloud("ytStatus", {"videoId": rec["youtube_id"], "privacy": privacy,
                                         "publishAt": _to_rfc3339(publish_at) if publish_at else ""}, timeout=30)
            if r.get("ok"):
                rec["privacy"] = "private" if publish_at else privacy
                rec["publish_at"] = publish_at or ""
                _save_json(HISTORY_FILE, self.history)
            return {**r, "record": rec}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def unpublish(self, hist_id):
        """撤下：從商品頁移除嵌入的影片，並刪除 YouTube 影片；成功後這筆紀錄回到「已匯出、未上傳」"""
        rec = next((h for h in self.history if h.get("id") == hist_id), None)
        if not rec or not rec.get("youtube_id"):
            return {"ok": False, "error": "這筆沒有上傳紀錄"}
        vid = rec["youtube_id"]
        msgs = []
        try:
            if rec.get("embedded"):
                r = self._cloud("removeVideo", {"productIds": rec["embedded"], "videoId": vid, "by": os.environ.get("COMPUTERNAME", "")})
                bad = [x for x in r.get("results", []) if not x.get("ok")]
                if bad:
                    return {"ok": False, "error": "商品頁移除失敗：" + "；".join(f"{x.get('title') or x.get('id')}：{x.get('error')}" for x in bad)}
                msgs.append(f"已從 {len(rec['embedded'])} 個商品頁移除")
            d = self._cloud("ytDelete", {"videoId": vid})
            if not d.get("ok"):
                rec["embedded"] = []
                _save_json(HISTORY_FILE, self.history)
                return {"ok": False, "error": (d.get("error") or "YouTube 刪除失敗"), "need_auth": bool(d.get("needAuth")), "partial": "；".join(msgs)}
            msgs.append("YouTube 影片已刪除")
        except Exception as e:
            return {"ok": False, "error": str(e)}
        for k in ("youtube_id", "youtube_url", "youtube_title", "embedded"):
            rec.pop(k, None)
        _save_json(HISTORY_FILE, self.history)
        self._status_set(rec.get("sku") or rec["name"], {"youtube": "", "embedded": []})
        return {"ok": True, "msg": "；".join(msgs)}

    @staticmethod
    def _yt_description(rec, product_ids):
        """YouTube 說明欄固定格式：品名、商品頁連結（帶 UTM）、品牌一句、官網、標籤"""
        sku = (rec.get("sku") or "").strip()
        pid = (product_ids or [None])[0]
        link = f"https://www.vvdouble.com/products/{pid}" if pid else "https://www.vvdouble.com"
        utm = f"?utm_source=youtube&utm_medium=shorts&utm_campaign={sku or 'reels'}"
        lines = [rec.get("name", ""), "",
                 f"商品頁 → {link}{utm}", "",
                 "VV DOUBLE｜大尺碼不是特例，而是我的日常",
                 "SM–5L6L 全尺碼實穿，棉花糖女孩的日常衣櫃。",
                 "官網 https://www.vvdouble.com", "",
                 "#VVDOUBLE #大尺碼不是特例 #肉肉女孩"]
        return "\n".join(lines)[:4900]

    def _publish_worker(self, rec, path, title, product_ids, options=None):
        try:
            self._publish_one(rec, path, title, product_ids, options or {})
        finally:
            self._busy = False

    def _publish_one(self, rec, path, title, product_ids, options):
        """上傳 YouTube（可選 公開／私人／定時公開）＋放到商品頁；回傳成功與否，事件照舊發 pub_progress／pub_done"""
        import urllib.request
        import urllib.error
        hid = rec["id"]
        emit = lambda step, msg, pct=None: self._emit("pub_progress", {"id": hid, "step": step, "msg": msg, "pct": pct})
        try:
            if not rec.get("youtube_id"):
                emit("auth", "取得 YouTube 授權…")
                tk = self._cloud("ytAccessToken")["access_token"]
                size = os.path.getsize(path)
                meta = {
                    "snippet": {
                        "title": (title or rec["name"])[:100],
                        "description": self._yt_description(rec, product_ids),
                        "categoryId": "22",
                        "defaultLanguage": "zh-Hant",
                    },
                    "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
                }
                publish_at = (options.get("publish_at") or "").strip()
                privacy = (options.get("privacy") or "public").strip()
                if publish_at:
                    # 定時公開：先設私人，YouTube 到時間自動切公開（要 RFC3339，介面給的是台灣時間）
                    meta["status"]["privacyStatus"] = "private"
                    meta["status"]["publishAt"] = _to_rfc3339(publish_at)
                elif privacy in ("private", "unlisted"):
                    meta["status"]["privacyStatus"] = privacy
                rec["privacy"] = meta["status"]["privacyStatus"]
                rec["publish_at"] = publish_at
                emit("upload", "開始上傳 YouTube…", 0)
                req = urllib.request.Request(
                    "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status&notifySubscribers=false",
                    data=json.dumps(meta, ensure_ascii=False).encode("utf-8"), method="POST",
                    headers={"Authorization": "Bearer " + tk, "Content-Type": "application/json; charset=UTF-8",
                             "X-Upload-Content-Type": "video/*", "X-Upload-Content-Length": str(size)})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    session = resp.headers.get("Location")
                if not session:
                    raise RuntimeError("YouTube 沒有回傳上傳位址")
                chunk = 8 * 1024 * 1024
                sent = 0
                video = None
                with open(path, "rb") as f:
                    while sent < size:
                        data = f.read(chunk)
                        end = sent + len(data) - 1
                        r2 = urllib.request.Request(session, data=data, method="PUT",
                                                    headers={"Authorization": "Bearer " + tk, "Content-Type": "video/*",
                                                             "Content-Length": str(len(data)),
                                                             "Content-Range": f"bytes {sent}-{end}/{size}"})
                        try:
                            with urllib.request.urlopen(r2, timeout=300) as resp:
                                body = resp.read().decode("utf-8", "ignore")
                                if resp.status in (200, 201):
                                    video = json.loads(body)
                        except urllib.error.HTTPError as e:
                            if e.code == 308:
                                pass
                            else:
                                raise RuntimeError(f"上傳失敗 HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}")
                        sent = end + 1
                        emit("upload", f"上傳中 {sent * 100 // size}%", sent / size)
                if not video or not video.get("id"):
                    raise RuntimeError("上傳完成但沒有拿到影片 ID")
                rec["youtube_id"] = video["id"]
                rec["youtube_title"] = meta["snippet"]["title"]
                rec["youtube_url"] = "https://youtu.be/" + video["id"]
                _save_json(HISTORY_FILE, self.history)
                try:
                    self._cloud("ytLog", {"videoId": video["id"], "title": meta["snippet"]["title"], "product": rec["name"],
                                          "sku": rec.get("sku", ""), "by": os.environ.get("COMPUTERNAME", "")})
                except Exception:
                    pass
                emit("upload", "YouTube 上傳完成", 1)
            vid = rec["youtube_id"]
            results = []
            if product_ids:
                emit("embed", f"放到 {len(product_ids)} 個商品頁…")
                r = self._cloud("embedVideo", {"productIds": product_ids, "videoId": vid,
                                               "title": rec.get("youtube_title") or title, "by": os.environ.get("COMPUTERNAME", "")}, timeout=110)
                results = r.get("results", [])
                rec["embedded"] = sorted(set((rec.get("embedded") or []) + [x["id"] for x in results if x.get("ok")]))
                _save_json(HISTORY_FILE, self.history)
            self._status_set(rec.get("sku") or rec["name"], {"youtube": rec["youtube_url"], "embedded": rec.get("embedded", [])})
            self._emit("pub_done", {"id": hid, "ok": True, "youtube_url": rec["youtube_url"], "results": results, "record": rec})
            self._cloud_event("upload", rec.get("name", ""), rec.get("youtube_url", ""))
            return True
        except Exception as e:
            traceback.print_exc()
            self._emit("pub_done", {"id": hid, "ok": False, "error": str(e), "record": rec})
            self._report_error(f"上傳：{rec.get('name', '')}", e, traceback.format_exc())
            return False


    # ---------- 底圖庫（官網部門\<拍攝日期>\空景 自動收集） ----------
    def _bg_root(self):
        r = (self.settings.get("bg_root") or "").strip()
        if r and os.path.isdir(r):
            return r
        mf = self.settings.get("music_folder") or ""
        parent = os.path.dirname(mf.rstrip("\\/")) if mf else ""
        return parent if parent and os.path.isdir(parent) else ""

    def _bg_index_path(self):
        root = self._bg_root()
        return os.path.join(root, BG_INDEX_NAME) if root else os.path.join(DATA_DIR, BG_INDEX_NAME)

    def _bg_index(self):
        return _load_json(self._bg_index_path(), {}) or {}

    def _bg_key(self, path):
        root = self._bg_root()
        try:
            return os.path.relpath(path, root).replace("\\", "/") if root else os.path.basename(path)
        except ValueError:
            return os.path.basename(path)

    def _bg_use(self, path, product, delta):
        idx = self._bg_index()
        meta = idx.setdefault(self._bg_key(path), {})
        uses = meta.setdefault("uses", [])
        if delta > 0:
            uses.append({"date": dt.date.today().isoformat(), "product": product})
        else:
            for i in range(len(uses) - 1, -1, -1):
                if uses[i].get("product") == product:
                    uses.pop(i)
                    break
        try:
            _save_json(self._bg_index_path(), idx)
        except Exception:
            pass
        return len(uses)

    def _bg_thumb(self, fp):
        try:
            from PIL import Image, ImageOps
            key = hashlib.md5(f"bg|{fp}|{os.path.getmtime(fp)}".encode("utf-8")).hexdigest()[:16]
            out = os.path.join(THUMB_DIR, key + ".jpg")
            if not os.path.exists(out):
                im = Image.open(fp)
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((240, 240))
                im.save(out, "JPEG", quality=80)
            with open(out, "rb") as f:
                return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")
        except Exception:
            return ""

    def bg_list(self, with_thumbs=True):
        """掃 官網部門 底下每個拍攝日期資料夾的「空景」，回傳依資料夾分組的底圖"""
        root = self._bg_root()
        if not root:
            return {"root": "", "groups": [], "total": 0}
        idx = self._bg_index()
        groups = []
        total = 0
        cands = []
        direct = os.path.join(root, BG_SUBDIR)
        if os.path.isdir(direct):
            cands.append(("空景", direct))
        try:
            for d in sorted(os.listdir(root), reverse=True):
                sub = os.path.join(root, d, BG_SUBDIR)
                if os.path.isdir(sub):
                    cands.append((d, sub))
        except Exception:
            pass
        for label, folder in cands:
            items = []
            try:
                files = sorted(os.listdir(folder))
            except Exception:
                continue
            for f in files:
                if not f.lower().endswith(IMAGE_EXT) or f.startswith("."):
                    continue
                fp = os.path.join(folder, f)
                meta = idx.get(self._bg_key(fp), {})
                uses = meta.get("uses", [])
                items.append({"path": fp, "name": os.path.splitext(f)[0], "group": label,
                              "uses": len(uses), "last_use": uses[-1] if uses else None,
                              "style": meta.get("style", ""),
                              "thumb": self._bg_thumb(fp) if with_thumbs else ""})
            if items:
                total += len(items)
                groups.append({"label": label, "folder": folder, "items": items})
        return {"root": root, "groups": groups, "total": total}

    # ---------- 開頭動畫庫（官網部門\開頭動畫\*.mov） ----------
    def _opening_root(self):
        r = (self.settings.get("opening_folder") or "").strip()
        if r and os.path.isdir(r):
            return r
        root = self._bg_root()
        p = os.path.join(root, OPENING_SUBDIR) if root else ""
        return p if p and os.path.isdir(p) else ""

    def _opening_thumb(self, fp):
        """開頭動畫縮圖：取 60% 處一格，疊在深灰底上（透明看得到字）"""
        try:
            key = hashlib.md5(f"op2|{fp}|{os.path.getmtime(fp)}".encode("utf-8")).hexdigest()[:16]
            out = os.path.join(THUMB_DIR, key + ".jpg")
            if not os.path.exists(out):
                d = _probe_duration(fp) or 5.0
                _run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x55524e:s=180x320:r=30",
                      "-ss", f"{d * 0.6:.2f}", "-i", fp, "-filter_complex", "[1]format=rgba,scale=180:320[o];[0][o]overlay=shortest=1",
                      "-frames:v", "1", "-q:v", "4", out], timeout=60)
            with open(out, "rb") as f:
                return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")
        except Exception:
            return ""

    def _opening_frame(self, fp):
        """開頭動畫 60% 處的透明 PNG（270x480），介面拿來疊在首支影片縮圖上做預覽"""
        try:
            key = hashlib.md5(f"opf|{fp}|{os.path.getmtime(fp)}".encode("utf-8")).hexdigest()[:16]
            out = os.path.join(THUMB_DIR, key + ".png")
            if not os.path.exists(out):
                d = _probe_duration(fp) or 5.0
                _run([_ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{d * 0.6:.2f}", "-i", fp,
                      "-vf", "format=rgba,scale=270:480", "-frames:v", "1", out], timeout=60)
            with open(out, "rb") as f:
                return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
        except Exception:
            return ""

    def opening_list(self):
        root = self._opening_root()
        sync = dict(getattr(self, "_opening_sync_state", {}) or {})
        if not root:
            return {"root": "", "items": [], "sync": sync}
        items = []
        try:
            files = sorted(os.listdir(root))
        except Exception:
            files = []
        for f in files:
            if not f.lower().endswith((".mov", ".webm")) or f.startswith("."):
                continue
            fp = os.path.join(root, f)
            items.append({"path": fp, "name": os.path.splitext(f)[0], "thumb": self._opening_thumb(fp),
                          "frame": self._opening_frame(fp)})
        return {"root": root, "items": items, "sync": sync}

    def _opening_sync(self):
        """開頭動畫庫同步：GitHub Release（tag=openings）裡有、Dropbox 開頭動畫資料夾沒有的 .mov 就下載進去
        （資料夾放 官網部門\開頭動畫，一台電腦載過全公司都有）"""
        import urllib.request
        self._opening_sync_state = {"running": True, "done": 0, "total": 0}
        try:
            root = self._opening_root()
            if not root:
                base = self._bg_root()
                if not base:
                    print("[開頭動畫] 找不到 官網部門 資料夾，略過同步")
                    return
                root = os.path.join(base, OPENING_SUBDIR)
                os.makedirs(root, exist_ok=True)
            req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/tags/openings",
                                         headers={"User-Agent": "VVReels", "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                j = json.loads(resp.read().decode("utf-8"))
            got = 0
            assets = [a for a in j.get("assets", []) if (a.get("name") or "").lower().endswith(".mov")]
            self._opening_sync_state["total"] = len(assets)
            for a in assets:
                name = a.get("name") or ""
                dst = os.path.join(root, name)
                if os.path.exists(dst) and os.path.getsize(dst) == int(a.get("size") or -1):
                    self._opening_sync_state["done"] += 1
                    continue
                print(f"[開頭動畫] 下載 {name}（{int(a.get('size') or 0) // 1048576} MB）")
                self._emit("opening_sync", dict(self._opening_sync_state, name=name))
                tmp = dst + ".part"
                if os.path.exists(tmp) and time.time() - os.path.getmtime(tmp) < 600:
                    continue        # 別台電腦正在載
                r2 = urllib.request.Request(a["browser_download_url"], headers={"User-Agent": "VVReels"})
                with urllib.request.urlopen(r2, timeout=300) as resp, open(tmp, "wb") as f:
                    shutil.copyfileobj(resp, f, 1024 * 256)
                os.replace(tmp, dst)
                got += 1
                self._opening_sync_state["done"] += 1
                self._emit("opening_sync", dict(self._opening_sync_state, name=name))
            print(f"[開頭動畫] 同步完成，新載 {got} 支")
            if got:
                self._emit("openings_changed", {"root": root, "added": got})
        except Exception as e:
            print(f"[開頭動畫] 同步失敗：{type(e).__name__}: {e}")
            traceback.print_exc()
        finally:
            self._opening_sync_state["running"] = False

    def opening_pick(self, exclude=None):
        items = self.opening_list()["items"]
        if not items:
            return None
        import random
        ex = set(exclude or [])
        recent = [h.get("opening") for h in self.history[-12:] if h.get("opening")]
        pool = [i["path"] for i in items if i["path"] not in ex] or [i["path"] for i in items]
        fresh = [p for p in pool if p not in recent] or pool
        return random.choice(fresh)

    def bg_set_root(self, path):
        self.settings["bg_root"] = path
        _save_json(SETTINGS_FILE, self.settings)
        return self.bg_list()

    def bg_set_style(self, path, style):
        idx = self._bg_index()
        idx.setdefault(self._bg_key(path), {})["style"] = style
        _save_json(self._bg_index_path(), idx)
        return True

    def bg_pick(self, mode="least_used", group=None):
        data = self.bg_list(with_thumbs=False)
        items = [i for g in data["groups"] if (not group or g["label"] == group) for i in g["items"]]
        if not items:
            return None
        import random
        if mode == "random":
            return random.choice(items)["path"]
        items.sort(key=lambda i: (i["uses"], (i["last_use"] or {}).get("date", "")))
        return items[0]["path"]

    def _music_index_path(self):
        folder = self.settings.get("music_folder")
        return os.path.join(folder, MUSIC_INDEX_NAME) if folder else None

    def _music_index(self):
        p = self._music_index_path()
        return _load_json(p, {}) if p else {}

    def _music_save(self, idx):
        p = self._music_index_path()
        if p:
            try:
                _save_json(p, idx)
            except Exception:
                pass

    def music_list(self):
        folder = self.settings.get("music_folder")
        if not folder or not os.path.isdir(folder):
            return {"folder": folder, "tracks": [], "last_added": None}
        idx = self._music_index()
        tracks = []
        newest = 0
        for f in sorted(os.listdir(folder)):
            if not f.lower().endswith(AUDIO_EXT):
                continue
            fp = os.path.join(folder, f)
            key = f
            meta = idx.get(key, {})
            mtime = os.path.getmtime(fp)
            newest = max(newest, mtime)
            uses = meta.get("uses", [])
            tracks.append({
                "key": key, "name": os.path.splitext(f)[0], "path": fp,
                "bpm": meta.get("bpm"), "start": meta.get("start"), "duration": meta.get("duration"),
                "style": meta.get("style", ""),
                "uses": len(uses), "last_use": uses[-1] if uses else None,
                "added": dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d"),
            })
        return {"folder": folder, "tracks": tracks,
                "last_added": dt.datetime.fromtimestamp(newest).strftime("%Y-%m-%d") if newest else None}

    def music_set_folder(self, path):
        if path and os.path.isdir(path):
            self.settings["music_folder"] = path
            _save_json(SETTINGS_FILE, self.settings)
        return self.music_list()

    def music_analyze(self, key=None):
        """算 BPM、自動挑段、長度；key=None 代表全部沒算過的"""
        folder = self.settings.get("music_folder")
        if not folder:
            return self.music_list()
        idx = self._music_index()
        names = [key] if key else [f for f in os.listdir(folder) if f.lower().endswith(AUDIO_EXT) and not idx.get(f, {}).get("bpm")]
        for i, f in enumerate(names):
            fp = os.path.join(folder, f)
            self._emit("music_analyzing", {"i": i, "n": len(names), "name": f})
            try:
                beats, bpm = g.detect_beats(fp)
                meta = idx.setdefault(f, {})
                meta["duration"] = round(_probe_duration(fp), 1)
                if beats:
                    si, _ = g.music_start_index(beats, fp, 20)
                    meta["bpm"] = round(bpm)
                    meta["start"] = round(beats[si], 1)
                    wave, dur = g.energy_curve(fp)
                    if wave:
                        meta["wave"] = wave
                        meta["duration"] = dur
                    meta["beat0"] = round(beats[0], 3)
                else:
                    meta["bpm"] = None
            except Exception:
                idx.setdefault(f, {})["bpm"] = None
            self._music_save(idx)
        self._emit("music_analyzing", {"i": len(names), "n": len(names), "name": ""})
        return self.music_list()

    def music_set_style(self, key, style):
        idx = self._music_index()
        idx.setdefault(key, {})["style"] = style
        self._music_save(idx)
        return True

    def _music_touch(self, path, bpm=None, start=None):
        folder = self.settings.get("music_folder")
        if not folder or not path.startswith(folder):
            return
        idx = self._music_index()
        meta = idx.setdefault(os.path.basename(path), {})
        if bpm and not meta.get("bpm"):
            meta["bpm"] = bpm
        if start is not None and meta.get("start") is None:
            meta["start"] = start
        self._music_save(idx)

    def _music_use(self, path, product, delta):
        folder = self.settings.get("music_folder")
        if not folder or not path.startswith(folder):
            return None
        idx = self._music_index()
        meta = idx.setdefault(os.path.basename(path), {})
        uses = meta.setdefault("uses", [])
        if delta > 0:
            uses.append({"date": dt.date.today().isoformat(), "product": product})
        else:
            for i in range(len(uses) - 1, -1, -1):
                if uses[i].get("product") == product:
                    uses.pop(i)
                    break
        self._music_save(idx)
        return len(uses)

    def music_pick(self, mode="least_used", exclude=None):
        lst = self.music_list()["tracks"]
        lst = [t for t in lst if t["path"] != exclude] or lst
        if not lst:
            return None
        if mode == "random":
            import random
            return random.choice(lst)
        return sorted(lst, key=lambda t: (t["uses"], (t["last_use"] or {}).get("date", "")))[0]

    # ---------- 模特 ----------
    def save_models(self, models):
        self.models = [m for m in (models or []) if (m.get("name") or "").strip()]
        _save_json(MODELS_FILE, self.models)
        return self.models

    def refresh_beauty(self):
        g.DRAFT_FOLDER = self.settings["draft_folder"]
        g.BEAUTY_FILE = BEAUTY_PATH
        return self._beauty_names(force=True)


def _serve(api):
    """本機 HTTP 伺服器：介面所有呼叫走這裡（不經 pywebview 橋接，拖曳視窗時不會卡住）"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    ui_dir = os.path.join(HERE, "ui")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/media":
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                fp = (q.get("p") or [""])[0]
                if not fp or not os.path.isfile(fp) or not fp.lower().endswith(AUDIO_EXT + VIDEO_EXT):
                    return self._send(404, b"not found", "text/plain")
                size = os.path.getsize(fp)
                rng = self.headers.get("Range")
                start, end = 0, size - 1
                if rng and rng.startswith("bytes="):
                    a, _, b = rng[6:].partition("-")
                    start = int(a) if a else max(0, size - int(b))
                    end = int(b) if (b and a) else size - 1
                    end = min(end, size - 1)
                ext = os.path.splitext(fp)[1].lower()
                ctype = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg", ".mp4": "video/mp4", ".mov": "video/quicktime"}.get(ext, "application/octet-stream")
                self.send_response(206 if rng else 200)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                if rng:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                with open(fp, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = f.read(min(1 << 20, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
                return
            name = "index.html" if path in ("/", "") else os.path.basename(path)
            fp = os.path.join(ui_dir, name)
            if not os.path.isfile(fp):
                return self._send(404, b"not found", "text/plain")
            ctype = "text/html; charset=utf-8" if name.endswith(".html") else "application/octet-stream"
            with open(fp, "rb") as f:
                self._send(200, f.read(), ctype)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
                name = req.get("name", "")
                fn = getattr(api, name, None) if name and not name.startswith("_") else None
                if fn is None:
                    raise ValueError(f"unknown api: {name}")
                result = fn(*(req.get("args") or []))
                body = json.dumps({"result": result}, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                traceback.print_exc()
                body = json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8")
            self._send(200, body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def main():
    import webview
    api = Api()
    port = _serve(api)
    print(f"[啟動] 介面位址 http://127.0.0.1:{port}/")
    bg = "#0e1014" if api.settings.get("theme") == "dark" else "#f3f5f9"
    win = webview.create_window("VV 實穿草稿產生器", f"http://127.0.0.1:{port}/", js_api=api, hidden=True,
                                width=1360, height=860, min_size=(1100, 700), background_color=bg)
    api._window = win  # 底線開頭：pywebview 不會去掃描這個物件

    def _on_drop(ev):
        # pywebview 的 DOM 事件：Windows 上只有走這條路才拿得到拖進來的檔案完整路徑（pywebviewFullPath）
        try:
            files = (ev.get("dataTransfer") or {}).get("files") or []
            paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
            if not paths:
                api._emit("drop_nopath", {"n": len(files)})
                return
            api._emit("analyzing", {"i": 0, "n": len(paths), "name": "讀取中…"})
            res = api.add_paths(paths)
            api._emit("dropped", res)
        except Exception as e:
            api._emit("drop_nopath", {"n": 0, "error": str(e)})

    def _hook_drop():
        try:
            win.dom.document.events.drop += _on_drop
        except Exception as e:
            print(f"拖曳掛載失敗：{e}")

    win.events.loaded += _hook_drop
    threading.Timer(3.0, api._warm_todo).start()
    threading.Thread(target=api._watch_exports, daemon=True).start()
    def _opening_sync_loop():
        while True:
            api._opening_sync()
            time.sleep(30 * 60)      # 之後每 30 分鐘再看一次有沒有新的開頭動畫
    threading.Thread(target=_opening_sync_loop, daemon=True).start()

    def _hook(args):
        traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)
        api._report_error("背景執行緒", args.exc_value, "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
    threading.excepthook = _hook
    # 保險：介面 8 秒內沒回報就直接顯示
    threading.Timer(8.0, api._show_window).start()
    webview.start()


if __name__ == "__main__":
    if os.environ.get("VVREELS_SMOKE"):      # 打包後的匯入檢查（GitHub Actions 用）：所有模組載得起來就寫檔結束
        with open(os.environ["VVREELS_SMOKE"], "w", encoding="utf-8") as _f:
            _f.write(f"smoke ok {VERSION} {REPO}")
        sys.exit(0)
    main()
