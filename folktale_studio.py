"""昔ばなし制作スタジオ: 人間が設定画面で入力した内容でパイプラインを実行する。

使い方:
  python folktale_studio.py [--port 8787]

- ブラウザで http://127.0.0.1:8787 を開く
- 設定フォーム → 保存 → 各工程ボタン(シート/台本/シーン/連結/公開)
- 進捗(実施/完了時刻・件数)は未実行でも常時表示
- 既存CLI(make_script/char_sheet/comfy_story/concat_story/yt_upload)を子プロセスで呼ぶ
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STUDIO_DIR = ROOT / "output" / "_studio"
LOG_DIR = STUDIO_DIR / "logs"
SPEC_PATH = ROOT / "data" / "char_specs.json"
COMFY_PY = r"C:\wk\ComfyUI\venv\Scripts\python.exe"
STAGES = ["sheets", "script", "scenes", "concat", "publish"]
STAGE_LABELS = {"sheets": "① キャラシート生成", "script": "② 台本生成",
                "scenes": "③ シーン生成", "concat": "④ 連結", "publish": "⑤ 公開"}

_state_lock = threading.Lock()
STATE: dict = {
    "config": None,
    "config_path": None,
    "stages": {s: {"state": "pending"} for s in STAGES},
    "procs": {},
}


def _slug_ok(slug: str) -> bool:
    return bool(re.fullmatch(r"[0-9A-Za-z_-]+", slug))


def merge_chars(slug: str, config: dict) -> list[str]:
    """設定のキャラを data/char_specs.json に登録し、キャラキーを返す。

    chars_key(既存エントリ)があればそれを使い、custom_chars があれば slug キーで保存。
    """
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    if config.get("chars_key"):
        if config["chars_key"] not in spec:
            raise ValueError(f"char_specs.json に {config['chars_key']} がない")
        names = list(spec[config["chars_key"]]["characters"])
        return _sheet_chars(config, names)
    chars = config.get("custom_chars") or []
    if not chars:
        raise ValueError("キャラが未設定です(chars_key か custom_chars)")
    entry = {"title": config["title"],
             "characters": {c["key"]: {"name": c["name"], "desc": c["desc"],
                                       "voice_style": int(c.get("voice_style", 2))}
                            for c in chars}}
    spec[slug] = entry
    SPEC_PATH.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    return _sheet_chars(config, list(entry["characters"]))


def _sheet_chars(config: dict, names: list[str]) -> list[str]:
    """シート生成対象キャラ(指定なければ全員)。"""
    sel = config.get("char_keys")
    return [n for n in names if not sel or n in sel]


def validate_config(cfg: dict) -> list[str]:
    errs = []
    if not cfg.get("title"):
        errs.append("title が未入力")
    if not _slug_ok(cfg.get("slug", "")):
        errs.append("slug は半角英数_-のみ")
    if not (cfg.get("outline") or []):
        errs.append("あらすじ(outline)が空")
    if cfg.get("detail", "detailed") not in ("detailed", "about"):
        errs.append("detail が不正")
    if not (cfg.get("chars_key") or cfg.get("custom_chars")):
        errs.append("キャラが未設定")
    if cfg.get("style_note") and cfg.get("style_free"):
        errs.append("作風の「指定」と「自由」は併用できません")
    for k in ("min_chars", "max_chars"):
        v = cfg.get(k)
        if v and (not isinstance(v, int) or not 100 <= v <= 3000):
            errs.append(f"{k} は100〜3000の整数")
    if cfg.get("min_chars") and cfg.get("max_chars") and cfg["min_chars"] >= cfg["max_chars"]:
        errs.append("min_chars < max_chars にしてください")
    pub = cfg.get("publish") or {}
    if pub.get("privacy") not in (None, "private", "unlisted", "public"):
        errs.append("privacy が不正")
    return errs


def build_desc(cfg: dict) -> str:
    """YouTube説明文をピノキオ以降の形式で組み立てる。"""
    pub = cfg.get("publish") or {}
    lines = [pub.get("youtube_title") or f"【AI昔話】{cfg['title']}|昔ばなしアニメ|3分で観る日本昔話", "",
             pub.get("intro") or f"むかしむかし、{cfg['title']}のお話です。", "",
             "おしまい。", "", f"No.{pub.get('video_no', 0):03d}", "",
             "■このチャンネルについて",
             "AIで生成した日本の昔話・世界の名作童話アニメを公開しています。",
             "生成パイプラインはオープンソース(Apache-2.0)で公開しています。詳細は概要欄またはリポジトリまで。", "",
             "■あらすじ"]
    for i, s in enumerate(pub.get("synopsis") or [], 1):
        lines.append(f"{['①','②','③','④','⑤','⑥'][i-1]} {s}")
    lines += ["", "━" * 10, "【制作】",
              "・映像・音声: MiniMax H3 (ComfyUI / RTX 5060 Ti)",
              "・キャラクターデザイン: Qwen-Image + 参照画像(R2V)",
              "・台本構成: GLM-5.3-Flash-EXL3 (local LLM)",
              "━" * 10,
              "※本動画はAIによる自動生成コンテンツです。物語の表現には創作的な脚色を含みます。"]
    if pub.get("footer"):
        lines += ["", pub["footer"]]
    return "\n".join(lines)


def _count_files(pat: str) -> int:
    return len(list(ROOT.glob(pat)))


def _scene_progress(slug: str) -> dict:
    expected = ((STATE.get("config") or {}).get("scenes")
                or len((STATE.get("config") or {}).get("outline") or []) or 12)
    done = _count_files(f"output/{slug}/scene_*.mp4")
    return {"done": done, "expected": expected}


def _tail(path: Path, n: int = 30) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return ""


def run_stage(stage: str):
    """ステージを子プロセスで実行し、STATE を更新する(スレッド内で呼ぶ)。"""
    cfg = STATE["config"] or {}
    slug = cfg.get("slug", "")
    out = ROOT / "output" / slug
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"{stage}.txt"
    ts = time.strftime("%H:%M:%S")
    with _state_lock:
        STATE["stages"][stage] = {"state": "running", "start": ts, "items": ""}
    cmd: list[str] = []
    if stage == "sheets":
        keys = merge_chars(slug, cfg)
        note = cfg.get("style_note") or ""
        style_base = cfg.get("style_base") or None
        base_seed = int(cfg.get("sheet_seed", 7000))
        for i, k in enumerate(keys):
            cmd = [COMFY_PY, str(ROOT / "char_sheet.py"), "--out", str(out / "charsheets"),
                   "--name", k, "--count", "2", "--seed", str(base_seed + i * 100),
                   "--prompt", _char_desc(cfg, k)]
            if note:
                cmd += ["--style-note", note]
            if style_base:
                cmd += ["--style-base", style_base]
            _run_logged(cmd, log)
        with _state_lock:
            STATE["stages"][stage]["items"] = f"{_count_files(f'output/{slug}/charsheets/*.png')}枚"
        return
    if stage == "script":
        style_args = []
        if cfg.get("style_free"):
            style_args = ["--style-free"]
        elif cfg.get("style_note"):
            style_args = ["--style", cfg["style_note"]]
        cmd = [sys.executable, str(ROOT / "make_script.py"), "--title", cfg["title"],
               "--chars", slug, "--outline-file", str(_write_outline(cfg, out)),
               "--out", str(out), "--scenes", str(cfg.get("scenes") or len(cfg["outline"])),
               "--detail", cfg.get("detail", "detailed")] + style_args
        if cfg.get("style_base"):
            cmd += ["--style-base", cfg["style_base"]]
        if cfg.get("min_chars"):
            cmd += ["--min-chars", str(cfg["min_chars"])]
        if cfg.get("max_chars"):
            cmd += ["--max-chars", str(cfg["max_chars"])]
        _run_logged(cmd, log)
        n = len(json.loads((out / "script.json").read_text(encoding="utf-8"))["scenes"])
        with _state_lock:
            STATE["stages"][stage]["items"] = f"{n}シーン"
        return
    if stage == "scenes":
        refs = sorted((out / "charsheets").glob("*_sheet_01_*.png"))
        cmd = [COMFY_PY, str(ROOT / "comfy_story.py"), str(out / "script.json"),
               "--out", str(out)]
        if refs:
            cmd += ["--refs", ",".join(str(p) for p in refs)]
        _run_logged(cmd, log)
        with _state_lock:
            STATE["stages"][stage]["items"] = f"{_scene_progress(slug)['done']}/{_scene_progress(slug)['expected']}シーン"
        return
    if stage == "concat":
        final = ROOT / "output" / f"{slug}_final.mp4"
        cmd = [COMFY_PY, str(ROOT / "concat_story.py"), str(out),
               "--out", str(final), "--title", cfg["title"]]
        if cfg.get("no_endcard"):
            cmd += ["--no-endcard"]
        _run_logged(cmd, log)
        with _state_lock:
            STATE["stages"][stage]["items"] = f"{final.name} ({final.stat().st_size // 1_000_000}MB)"
        return
    if stage == "publish":
        pub = cfg.get("publish") or {}
        final = ROOT / "output" / f"{slug}_final.mp4"
        if not final.exists():
            raise RuntimeError("output/<slug>_final.mp4 がありません(先に④連結)")
        if not (pub.get("video_no") or 0) >= 1:
            raise RuntimeError("作品番号(video_no)が未設定です")
        desc = STUDIO_DIR / f"{slug}_desc.txt"
        STUDIO_DIR.mkdir(parents=True, exist_ok=True)
        desc.write_text(build_desc(cfg), encoding="utf-8")
        final = ROOT / "output" / f"{slug}_final.mp4"
        cmd = [sys.executable, str(ROOT / "yt_upload.py"), str(final),
               "--title", pub.get("youtube_title") or f"【AI昔話】{cfg['title']}|昔ばなしアニメ|3分で観る日本昔話",
               "--desc-file", str(desc), "--tags", pub.get("tags", "昔話,AIアニメ,AI昔話"),
               "--privacy", pub.get("privacy", "public")]
        _run_logged(cmd, log)
        vid = ""
        for ln in reversed(log.read_text(encoding="utf-8", errors="replace").splitlines()):
            m = re.search(r"video_id=([\w-]+)", ln)
            if m:
                vid = m.group(1)
                break
        with _state_lock:
            STATE["stages"][stage]["items"] = vid or "video_id 不明"


def _char_desc(cfg: dict, key: str) -> str:
    for c in cfg.get("custom_chars") or []:
        if c.get("key") == key:
            return c["desc"]
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    entry = spec.get(cfg.get("chars_key") or cfg["slug"], {})
    return entry.get("characters", {}).get(key, {}).get("desc", key)


def _write_outline(cfg: dict, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    p = out / "outline.txt"
    p.write_text("\n".join(cfg["outline"]) + "\n", encoding="utf-8")
    return p


def _run_logged(cmd: list[str], log: Path):
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n$ {' '.join(str(c) for c in cmd)}\n")
        r = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[1]} exit={r.returncode} ({log})")


CREATE_SYSTEM = """あなたは昔ばなし動画の企画担当です。作品名から制作設定を下書きします。

出力はJSONのみ(コードフェンス禁止)。形式:
{"slug_suffix": "ローマ字の作品スラッグ(半角英数_のみ)",
 "outline": ["15秒シーンのあらすじ1行", ... 合計12行],
 "characters": [{"key": "半角英数", "name": "キャラ名", "desc": "外見の固定描写(服装・髪・持ち物を具体的に1〜2文)", "voice_style": 2}, ... 3〜5人],
 "synopsis": ["概要欄のあらすじ①", "②", "③", "④"],
 "intro": "説明文の導入1行(むかしむかし、...)",
 "tags": "カンマ区切りのタグ"}

ルール:
- 作品名が日本の昔話なら定番の筋に従う。世界の名作や特殊な世界の作品ならその世界観に合わせる
- descは動画モデル用の外見固定文として使う(画風の指定は含めない)
- slug_suffix に番号は付けない(番号はシステムが付ける)"""


def draft_config(title: str, cfg: dict) -> dict:
    """作品名から未入力欄をLLMで下書きする(既に入力済みの欄は触らない)。"""
    import requests

    next_no = 1 + max([0] + [int(m.group(1)) for p in (ROOT / "output").glob("[0-9][0-9][0-9]_*")
                              if (m := re.match(r"(\d{3})_", p.name))])
    body = {"model": "GLM-5.3-Flash-EXL3", "temperature": 0.8, "max_tokens": 8000,
            "chat_template_kwargs": {"thinking": False},
            "messages": [{"role": "system", "content": CREATE_SYSTEM},
                         {"role": "user", "content": f"作品名: {title}\n次の作品番号: {next_no:03d}\n"}]}
    r = requests.post("http://192.168.1.21:8888/v1/chat/completions", json=body, timeout=1800)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"].get("content") or ""
    draft = _extract_json_obj(content)

    out = dict(cfg)
    out.setdefault("publish", {})
    if not out.get("slug"):
        out["slug"] = f"{next_no:03d}_{str(draft.get('slug_suffix') or '').strip().replace(' ', '_')}"
        if not _slug_ok(out["slug"]):
            out["slug"] = f"{next_no:03d}_work"
    if not out.get("outline"):
        out["outline"] = [s.strip() for s in draft.get("outline") or [] if s.strip()]
        out["scenes"] = out.get("scenes") or len(out["outline"])
    if not (out.get("custom_chars") or out.get("chars_key")):
        out["custom_chars"] = [{"key": c.get("key", ""), "name": c.get("name", ""),
                                "desc": c.get("desc", ""),
                                "voice_style": int(c.get("voice_style", 2))}
                               for c in draft.get("characters") or [] if c.get("key")]
        out["scenes"] = out.get("scenes") or len(out.get("outline") or [])
    if not (out.get("publish") or {}).get("synopsis"):
        out["publish"]["synopsis"] = [s.strip() for s in draft.get("synopsis") or [] if s.strip()][:4]
    if not out["publish"].get("intro"):
        out["publish"]["intro"] = draft.get("intro", "")
    if not out["publish"].get("tags"):
        out["publish"]["tags"] = draft.get("tags", "")
    if not out["publish"].get("youtube_title"):
        out["publish"]["youtube_title"] = (f"【AI昔話】{title}|昔ばなしアニメ|"
                                           f"3分で観る{'日本昔話' if out.get('detail') != 'free' else '名作童話'}")
    return out


def _extract_json_obj(raw: str) -> dict:
    s = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("JSONが見つかりません")
    return json.loads(s[start:end + 1])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):  # アクセスログは抑制
        pass

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # チャットのプレビューウィジェット(file://等の別オリジン)からも叩けるように
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        if self.path == "/":
            html = (ROOT / "studio.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
        elif self.path.startswith("/api/config?slug="):
            slug = self.path.split("slug=", 1)[1]
            if not _slug_ok(slug):
                self._send(400, b'{"errors": ["bad slug"]}')
                return
            p = STUDIO_DIR / f"{slug}.json"
            if not p.exists():
                self._send(404, b'{"errors": ["not found"]}')
                return
            self._send(200, p.read_bytes())
        elif self.path == "/api/status":
            self._send(200, json.dumps(self._status(), ensure_ascii=False).encode())
        else:
            self._send(404, b"{}", "application/json")

    def do_POST(self):
        if self.path in ("/api/config", "/api/create", "/api/run"):
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self._send(400, json.dumps({"errors": [f"JSON不正: {e}"]}).encode())
                return
        if self.path == "/api/config":
            cfg = body
            errs = validate_config(cfg)
            if errs:
                self._send(400, json.dumps({"errors": errs}, ensure_ascii=False).encode())
                return
            slug = cfg["slug"]
            STUDIO_DIR.mkdir(parents=True, exist_ok=True)
            path = STUDIO_DIR / f"{slug}.json"
            path.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
            with _state_lock:
                STATE["config"] = cfg
                STATE["config_path"] = str(path)
            self._send(200, json.dumps({"ok": True, "path": str(path)}).encode())
        elif self.path == "/api/create":
            cfg = body
            title = (cfg.get("title") or "").strip()
            if not title:
                self._send(400, json.dumps({"errors": ["title が未入力です"]}).encode())
                return
            try:
                filled = draft_config(title, cfg)
            except Exception as e:
                self._send(502, json.dumps({"errors": [f"下書き失敗: {str(e)[:200]}"]}).encode())
                return
            self._send(200, json.dumps({"config": filled}, ensure_ascii=False).encode())
        elif self.path == "/api/run":
            req = body
            stage = req.get("stage", "")
            if stage not in STAGES:
                self._send(400, json.dumps({"errors": ["stage が不正"]}).encode())
                return
            if not STATE.get("config"):
                self._send(400, json.dumps({"errors": ["設定を先に保存してください"]}).encode())
                return
            with _state_lock:
                if STATE["stages"].get(stage, {}).get("state") == "running":
                    self._send(409, json.dumps({"errors": ["実行中です"]}).encode())
                    return
            threading.Thread(target=self._run_stage_safe, args=(stage,), daemon=True).start()
            self._send(200, json.dumps({"ok": True}).encode())
        else:
            self._send(404, b"{}", "application/json")

    def _run_stage_safe(self, stage: str):
        try:
            run_stage(stage)
        except Exception as e:
            with _state_lock:
                STATE["stages"][stage] = {"state": "error", "error": str(e)[:300],
                                          "end": time.strftime("%H:%M:%S")}
        else:
            with _state_lock:
                st = STATE["stages"][stage]
                st["state"] = "done"
                st["end"] = time.strftime("%H:%M:%S")

    def _status(self):
        with _state_lock:
            cfg = STATE.get("config") or {}
            stages = {k: dict(v) for k, v in STATE["stages"].items()}
        slug = cfg.get("slug", "")
        stages["scenes"]["progress"] = _scene_progress(slug) if slug else {"done": 0, "expected": 0}
        stages["sheets"]["files"] = _count_files(f"output/{slug}/charsheets/*.png") if slug else 0
        stages["script"]["exists"] = bool(slug and (ROOT / f"output/{slug}/script.json").exists())
        stages["concat"]["exists"] = bool(slug and (ROOT / f"output/{slug}_final.mp4").exists())
        for s in STAGES:
            stages[s]["log"] = _tail(LOG_DIR / f"{s}.txt", 14)
        return {"config": cfg, "stages": stages,
                "existing_configs": sorted(p.stem for p in STUDIO_DIR.glob("*.json"))
                if STUDIO_DIR.exists() else []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"studio: http://127.0.0.1:{args.port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
