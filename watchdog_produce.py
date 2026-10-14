"""制作ウォッチドッグ: シーン生成を監視し、落ちたら自動で巻き取って再開する。

使い方: python watchdog_produce.py <plan.json>
plan例: {"phases": [
  {"kind": "wait_scenes", "out": "...", "expected": 12, "timeout_min": 60},
  {"kind": "script", "args": [...], "env": {"YUKKURI_MAX_TOKENS": "30000"}, "mark": "..."},
  {"kind": "scenes", "out": "...", "refs": [...], "ref_names": [...], "expected": 12},
  {"kind": "concat", "out_dir": "...", "title": "...", "final": "..."}
]}
- comfy_story が死んでいる/15分進捗なし → 殺して冪等再実行
- ComfyUI が落ちていたら再起動
- make_script が失敗したら最大5回やり直す
"""
from __future__ import annotations

import glob
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from yukkuri.folktale import verify_sheet  # noqa: E402

ROOT = Path(r"C:\wk\YukkuriGenerator")
COMFY_PY = r"C:\wk\ComfyUI\venv\Scripts\python.exe"
COMFY = "http://127.0.0.1:8188"
LOG = ROOT / "output" / "_studio" / "logs" / "watchdog.log"


def log(msg: str):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}\n")
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def comfy_alive() -> bool:
    try:
        urllib.request.urlopen(COMFY + "/system_stats", timeout=5)
        return True
    except Exception:
        return False


def ensure_comfy():
    if comfy_alive():
        return
    log("ComfyUI down -> restarting")
    subprocess.Popen([COMFY_PY, "main.py", "--port", "8188"], cwd=r"C:\wk\ComfyUI",
                     stdout=open(ROOT / "output/_studio/logs/comfy_restart.log", "ab"),
                     stderr=subprocess.STDOUT)
    for _ in range(90):
        time.sleep(10)
        if comfy_alive():
            log("ComfyUI back up")
            return
    raise RuntimeError("ComfyUI failed to start")


def procs_with(marker: str):
    # pythonプロセス限定(クエリ用powershell自身への自己マッチ防止)
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Get-CimInstance Win32_Process | Where-Object {{$_.Name -like 'python*' -and $_.CommandLine -like '*{marker}*'}} | Select-Object -ExpandProperty ProcessId"],
                        capture_output=True, text=True)
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip().isdigit()]


def kill_procs(marker: str):
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"Get-CimInstance Win32_Process | Where-Object {{$_.CommandLine -like '*{marker}*'}} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"],
                    capture_output=True)


def scene_count(out: str) -> int:
    return len(glob.glob(str(Path(out) / "scene_*.mp4")))


def run_logged(cmd: list[str], logname: str, timeout_min: int = 120) -> int:
    cmd = [str(c) for c in cmd]
    with open(ROOT / f"output/_studio/logs/{logname}.log", "ab") as f:
        f.write(f"\n$ {' '.join(str(c) for c in cmd)}\n".encode())
        try:
            r = subprocess.run(cmd, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT,
                               timeout=timeout_min * 60)
            return r.returncode
        except subprocess.TimeoutExpired:
            return 124


def wait_scenes(out: str, expected: int, timeout_min: int):
    """既存ランの完走を待つ(停滞時は諦めて先へ)。"""
    t0 = time.time(); last = scene_count(out); last_change = time.time()
    while scene_count(out) < expected:
        if time.time() - t0 > timeout_min * 60:
            log(f"wait_scenes: timeout at {scene_count(out)}/{expected}, proceeding")
            return
        n = scene_count(out)
        if n != last:
            last, last_change = n, time.time()
        elif time.time() - last_change > 900 and not procs_with("comfy_story") and not procs_with("comfyui"):
            log(f"wait_scenes: stalled at {n}/{expected}, proceeding")
            return
        ensure_comfy()
        time.sleep(60)


def _queue_len() -> int:
    try:
        q = json.loads(urllib.request.urlopen(COMFY + "/queue", timeout=5).read())
        return len(q["queue_running"]) + len(q["queue_pending"])
    except Exception:
        return -1


def do_scenes(out: str, refs: list[str], ref_names: list[str], expected: int,
              script: str, deadline_min: int = 240):
    """シーン生成を expected に達するまで自動再開。"""
    start = time.time(); last = scene_count(out); last_change = time.time()
    while scene_count(out) < expected:
        if time.time() - start > deadline_min * 60:
            raise RuntimeError(f"scenes deadline exceeded at {scene_count(out)}/{expected}")
        ensure_comfy()
        if not procs_with("comfy_story.py"):
            log(f"launch comfy_story ({scene_count(out)}/{expected})")
            cmd = [COMFY_PY, "-u", str(ROOT / "comfy_story.py"), str(script), "--out", str(out),
                   "--refs", ",".join(refs)]
            if ref_names:
                cmd += ["--ref-names", ",".join(ref_names)]
            subprocess.Popen(cmd, cwd=str(ROOT),
                             stdout=open(ROOT / "output/_studio/logs/watchdog_scenes.log", "ab"),
                             stderr=subprocess.STDOUT)
        n = scene_count(out)
        if n > last:
            log(f"progress {n}/{expected}")
            last, last_change = n, time.time()
        elif time.time() - last_change > 2700:  # 45分: 詳細版+モデルロードでも完了する時間
            qlen = _queue_len()
            log(f"stall 45min at {n}/{expected} (comfy queue={qlen}) -> kill & relaunch")
            kill_procs("comfy_story.py")
            last_change = time.time()
        time.sleep(30)
    log(f"scenes complete {expected}/{expected}")


def do_script(args: list[str], mark: str, env_extra: dict | None = None):
    """台本生成(失敗時は最大5回)。mark ファイルの更新で成功判定。"""
    import os
    mark_p = Path(mark)
    mtime0 = mark_p.stat().st_mtime if mark_p.exists() else 0
    env = os.environ.copy()
    env.update(env_extra or {})
    for attempt in range(1, 6):
        log(f"make_script attempt {attempt}")
        r = subprocess.run([sys.executable, str(ROOT / "make_script.py")] + args,
                           cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=90 * 60)
        ok = r.returncode == 0 and mark_p.exists() and mark_p.stat().st_mtime > mtime0
        log(f"make_script rc={r.returncode} ok={ok} {r.stdout[-200:] if r.stdout else r.stderr[-200:]}")
        if ok:
            return
        time.sleep(30)
    raise RuntimeError("make_script failed 5 times")


def main():
    plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    log(f"watchdog start: {len(plan['phases'])} phases")
    for i, ph in enumerate(plan["phases"], 1):
        kind = ph["kind"]
        log(f"phase {i}: {kind} {ph.get('out') or ph.get('title') or ''}")
        if kind == "wait_scenes":
            wait_scenes(ph["out"], ph["expected"], ph.get("timeout_min", 60))
        elif kind == "archive_scenes":
            dest = Path(ph["out"]) / ph.get("to", "scenes_old")
            dest.mkdir(parents=True, exist_ok=True)
            n = 0
            for f in glob.glob(str(Path(ph["out"]) / "scene_*.mp4")):
                Path(f).rename(dest / Path(f).name)
                n += 1
            log(f"archived {n} scenes -> {dest}")
        elif kind == "script":
            do_script(ph["args"], ph["mark"], ph.get("env"))
        elif kind == "write_refs":
            # charsheetsから検証合格シートをピックし refs.json を書き出す
            sheets_dir = Path(ph["sheets_dir"])
            key2name = {}
            try:
                spec = json.loads((ROOT / "data" / "char_specs.json").read_text(encoding="utf-8"))
                entry = spec[ph["chars_key"]]["characters"]
                key2name = {k: c["name"] for k, c in entry.items()}
            except Exception:
                pass
            refs, names = [], []
            for pre in sorted({f.name.rsplit("_sheet_", 1)[0] for f in sheets_dir.glob("*.png")}):
                cands = sorted(sheets_dir.glob(f"{pre}_sheet_*.png"))
                pick = next((c for c in cands if verify_sheet(c)[0]), cands[0])
                ok = verify_sheet(pick)[0]
                refs.append(str(pick))
                names.append(f"{pre}={key2name.get(pre, pre)}")
                if not ok:
                    log(f"write_refs: {pre} は合格シートが無いため候補を使用({cands[0].name})")
            json.dump({"refs": refs, "ref_names": names},
                      open(ph["out_file"], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            log(f"write_refs: {len(refs)} sheets -> {ph['out_file']}")
        elif kind == "scenes":
            refs, ref_names = ph.get("refs"), ph.get("ref_names")
            if ph.get("refs_file"):
                rd = json.loads(Path(ph["refs_file"]).read_text(encoding="utf-8"))
                refs, ref_names = rd["refs"], rd["ref_names"]
            do_scenes(ph["out"], refs or [], ref_names or [], ph["expected"], ph["script"],
                      ph.get("deadline_min", 240))
        elif kind == "concat":
            cmd = [COMFY_PY, str(ROOT / "concat_story.py"), ph["out_dir"],
                   "--out", ph["final"], "--title", ph["title"], "--no-endcard"]
            if run_logged(cmd, f"concat_{ph.get('name', i)}", timeout_min=30) != 0:
                raise RuntimeError("concat failed")
            log(f"concat done: {ph['final']}")
        elif kind == "sheets":
            ensure_comfy()
            for args in ph["cmds"]:
                name = args[args.index("--name") + 1]
                for attempt in range(2):
                    if run_logged([COMFY_PY, str(ROOT / "char_sheet.py")] + args,
                                  f"sheets_{name}", timeout_min=20) == 0:
                        break
                    log(f"sheets retry {name}")
            log("sheets done")
        elif kind == "sleep":
            time.sleep(ph["sec"])
    log("watchdog: ALL PHASES COMPLETE")


if __name__ == "__main__":
    main()
