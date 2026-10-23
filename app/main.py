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
import traceback

FROZEN = getattr(sys, "frozen", False)
HERE = getattr(sys, "_MEIPASS", None) if FROZEN else os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else HERE   # 可更新的安裝資料夾
CORE = os.path.join(HERE, "core")
sys.path.insert(0, CORE)
sys.path.insert(0, HERE)
from version import VERSION, REPO  # noqa: E402

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
            "layouts": layouts.list_layouts() if layouts else [],
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
                person = _has_person(fp, dur)
                out["clips"].append({
                    "id": key, "path": fp, "source": src, "name": os.path.basename(src), "duration": round(dur, 1),
                    "thumb": thumb, "closeup": (person is False), "person": person, "transcoded": bool(note),
                })
            except Exception as e:
                out["clips"].append({"id": _file_key(fp), "path": fp, "name": os.path.basename(fp),
                                     "duration": 0, "thumb": "", "closeup": False, "error": str(e)})
        self._emit("analyzing", {"i": total, "n": total, "name": ""})
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
                spec.setdefault("options", {})
                for k in ("max_total", "mid_closeup", "person_center", "stabilize", "beauty"):
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
                r["job"] = ji
                r["name"] = name
                r["music"] = (spec.get("music") or {}).get("path")
                r["colors"] = spec.get("colors") or []
                r["sku"] = spec.get("sku") or ""
                r["features"] = spec.get("features") or []
                r["background"] = spec.get("background") or ""
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
        self._status_set(rec.get("sku") or rec["name"], {"name": rec["name"], "exported_at": rec["exported_at"],
                                                          "export_file": rec["export_file"]})
        uses = None
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

    def todo_list(self, force=False):
        url = (self.settings.get("todo_url") or "").strip()
        if not url:
            return {"ok": False, "error": "尚未設定待製作清單網址（設定頁）", "tabs": [], "status": {}}
        cache = _load_json(TODO_CACHE, None)
        data, err = None, ""
        fresh = cache and cache.get("url") == url and (dt.datetime.now().timestamp() - cache.get("ts", 0) < 300)
        if fresh and not force:
            data = cache.get("data")
        else:
            try:
                import urllib.request
                req = urllib.request.Request(url, headers={"User-Agent": "VVReels/1.0"})
                with urllib.request.urlopen(req, timeout=120) as resp:   # 試算表分頁多時 Apps Script 要跑幾十秒
                    data = json.loads(resp.read().decode("utf-8"))
                _save_json(TODO_CACHE, {"url": url, "ts": dt.datetime.now().timestamp(), "data": data})
            except Exception as e:
                err = f"讀取失敗：{type(e).__name__}: {e}"
                data = cache.get("data") if cache else None
        if not data:
            return {"ok": False, "error": err or "沒有資料", "tabs": [], "status": {}}
        # 把分頁依檔期代碼由新到舊排
        tabs = sorted(data.get("tabs", []), key=lambda t: (t.get("year") or 0, t.get("code", "")), reverse=True)
        for t in tabs:
            t["ym"] = f"{t.get('year') or ''}-{t.get('month')}"
        cached_at = (cache or {}).get("ts") if (fresh and not force) or err else dt.datetime.now().timestamp()
        return {"ok": True, "tabs": tabs, "status": self._status_index(), "error": err,
                "cached_at": dt.datetime.fromtimestamp(cached_at or dt.datetime.now().timestamp()).strftime("%H:%M"),
                "history_names": [h.get("name") for h in self.history if h.get("ok")],
                "history_skus": [h.get("sku") for h in self.history if h.get("ok") and h.get("sku")]}


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

    def yt_titles(self, hist_id, extra=""):
        rec = next((h for h in self.history if h.get("id") == hist_id), None)
        if not rec:
            return {"ok": False, "error": "找不到紀錄"}
        models = sorted({(s.get("model") or "") for s in rec.get("segments", []) if s.get("model")})
        try:
            r = self._cloud("aiTitle", {"name": rec.get("name", ""), "category": rec.get("category", ""), "features": rec.get("features", []),
                                        "colors": rec.get("colors", []), "models": "、".join(models), "extra": extra, "n": 3})
            return {"ok": True, "titles": r.get("titles", [])}
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

    def _warm_todo(self):
        """開啟時在背景先抓排程表，之後查找才不會等"""
        try:
            if (self.settings.get("todo_url") or "").strip():
                self.todo_list()
        except Exception:
            pass

    def find_products(self, name):
        try:
            r = self._cloud("findProducts", {"name": name})
            return {"ok": True, "items": r.get("items", [])}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def publish(self, hist_id, title, product_ids):
        rec = next((h for h in self.history if h.get("id") == hist_id), None)
        if not rec:
            return {"ok": False, "error": "找不到紀錄"}
        path = rec.get("export_file") or self._find_export(rec["name"])
        if not path or not os.path.exists(path):
            return {"ok": False, "error": "成品資料夾裡沒有這支影片，請先勾「已匯出」"}
        if self._busy:
            return {"ok": False, "error": "正在處理中"}
        self._busy = True
        threading.Thread(target=self._publish_worker, args=(rec, path, title, list(product_ids or [])), daemon=True).start()
        return {"ok": True}

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

    def _publish_worker(self, rec, path, title, product_ids):
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
                        "description": f"{rec['name']}\n\n{SITE_URL}",
                        "categoryId": "22",
                        "defaultLanguage": "zh-Hant",
                    },
                    "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
                }
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
        except Exception as e:
            traceback.print_exc()
            self._emit("pub_done", {"id": hid, "ok": False, "error": str(e), "record": rec})
        finally:
            self._busy = False


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
                    si, _ = g.music_start_index(beats, fp, self.settings.get("max_total", 28))
                    meta["bpm"] = round(bpm)
                    meta["start"] = round(beats[si], 1)
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
    threading.Timer(3.0, api._warm_todo).start()
    # 保險：介面 8 秒內沒回報就直接顯示
    threading.Timer(8.0, api._show_window).start()
    webview.start()


if __name__ == "__main__":
    main()
