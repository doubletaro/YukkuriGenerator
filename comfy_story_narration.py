"""ナレーション分離型の物語動画生成。

方式:
  1. H3 で「音声なし(効果音・BGMのみ)」のシーン動画を生成
     → prompt の【音声】欄でセリフ・ナレーション禁止を明示
  2. 台本のナレーションを VOICEVOX で合成(四国めたんなど感情スタイル付き)
  3. シーン動画にナレーションをミックス(ダッキング付き: ナレ中はBGMを下げる)

使い方:
  python comfy_story_narration.py output/alice_script.json --out output/alice_narr \
      --voice shikoku_metan --style 76
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

FFMPEG = "ffmpeg"
COMFY = "http://127.0.0.1:8188"
UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VAE_V = "minimax_h3_video_vae_fp16.safetensors"
VAE_A = "minimax_h3_audio_vae_fp32.safetensors"
LORA = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
STEPS = 6
WIDTH, HEIGHT = 640, 480
LENGTH = 362  # ~15s @24fps


# ── VOICEVOX ──────────────────────────────────────────────
def vv_health(port=50021) -> bool:
    try:
        return urllib.request.urlopen(f"http://127.0.0.1:{port}/version", timeout=3).status == 200
    except Exception:
        return False


def vv_synth(text: str, style_id: int, out: Path, port=50021):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/audio_query?text={urllib.parse.quote(text)}&speaker={style_id}",
        method="POST")
    q = json.loads(urllib.request.urlopen(req, timeout=30).read())
    q["speedScale"] = 0.95  # 物語語りはややゆっくり
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/synthesis?speaker={style_id}",
        data=json.dumps(q).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        out.write_bytes(r.read())
    with __import__("wave").open(str(out)) as w:
        return w.getnframes() / w.getframerate()


def extract_narration(prompt: str) -> str | None:
    """prompt の【音声】欄からナレーション本文を抽出。"""
    m = re.search(r'ナレーション[「『]([^」』]+)[」』]', prompt)
    return m.group(1) if m else None


# ── H3: 効果音・BGMのみの動画生成 ─────────────────────────
def api_prompt(prompt_text: str, seed: int, prefix: str) -> dict:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_V}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_A}},
        "5": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "lora_name": LORA, "strength_model": 1.0, "model": ["1", 0]}},
        "6": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0], "prompt": prompt_text,
            "width": WIDTH, "height": HEIGHT, "length": LENGTH}},
        "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed, "control_after_generate": "fixed"}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "9": {"class_type": "BasicScheduler", "inputs": {
            "model": ["5", 0], "scheduler": "simple", "steps": STEPS, "denoise": 1.0}},
        "10": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["6", 0]}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["7", 0], "guider": ["10", 0], "sampler": ["8", 0],
            "sigmas": ["9", 0], "latent_image": ["6", 1]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["11", 0], "vae": ["4", 0]}},
        "14": {"class_type": "CreateVideo", "inputs": {
            "images": ["12", 0], "audio": ["13", 0], "fps": 24, "bit_depth": 8}},
        "15": {"class_type": "SaveVideo", "inputs": {
            "video": ["14", 0], "filename_prefix": f"narr/{prefix}", "format": "mp4"}},
    }


def post(path, payload, timeout=60):
    req = urllib.request.Request(COMFY + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_json(path, timeout=60):
    with urllib.request.urlopen(COMFY + path, timeout=timeout) as r:
        return json.loads(r.read())


def wait_output(prompt_id, timeout_s=7200):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        h = get_json(f"/history/{prompt_id}")
        if prompt_id in h:
            e = h[prompt_id]
            if e.get("status", {}).get("completed") or e.get("outputs"):
                return e
            if e.get("status", {}).get("status_str") == "error":
                raise RuntimeError(json.dumps(e["status"], ensure_ascii=False)[:1200])
        time.sleep(10)
    raise TimeoutError(prompt_id)


def fetch_files(entry) -> list[str]:
    files = []
    for node_out in entry.get("outputs", {}).values():
        for key in ("gifs", "videos", "images"):
            for item in node_out.get(key, []) or []:
                sub, fn = item.get("subfolder", ""), item["filename"]
                files.append(f"{sub}/{fn}" if sub else fn)
    return files


def run_scene(prompt_text: str, seed: int, prefix: str, out_dir: Path) -> Path:
    payload = {"prompt": api_prompt(prompt_text, seed, prefix), "client_id": "yukkuri-narr"}
    pid = post("/prompt", payload)["prompt_id"]
    print(f"[{prefix}] submitted {pid}", flush=True)
    entry = wait_output(pid)
    files = fetch_files(entry)
    if not files:
        raise RuntimeError("no output")
    dest = out_dir / f"{prefix}.mp4"
    sub = files[0].rsplit("/", 1)[0] if "/" in files[0] else ""
    url = f"/view?filename={files[0].split('/')[-1]}&subfolder={sub}&type=output"
    with urllib.request.urlopen(COMFY + url, timeout=600) as r, open(dest, "wb") as f:
        f.write(r.read())
    print(f"[{prefix}] saved {dest}", flush=True)
    return dest


# ── ナレーション合成 + ミックス ──────────────────────────
def strip_speech(prompt: str) -> str:
    """prompt からセリフ・ナレーション文言を除去(映像だけの指示に書き換え)。"""
    p = re.sub(r'セリフ[=は][^。]*。', '', prompt)
    p = re.sub(r'ナレーション[「『][^」』]+[」』]', 'ナレーションは別途収録済みのため音声には含めない。', p)
    return p


def mix_scene(scene_mp4: Path, narration_wav: Path, out_mp4: Path, gap: float = 1.0):
    """シーン動画の先頭1秒後にナレーションを重ねる(効果音/BGMはそのまま)。"""
    work = Path(out_mp4).parent
    work.mkdir(parents=True, exist_ok=True)
    run([FFMPEG, "-y", "-v", "error", "-i", str(narration_wav),
         "-af", f"adelay={int(gap*1000)}|{int(gap*1000)}", "narr_delayed.wav"], work)
    run([FFMPEG, "-y", "-v", "error", "-i", str(scene_mp4), "-i", "narr_delayed.wav",
         "-filter_complex",
         "[1:a]volume=1.0[nar];[0:a]volume=1.0[bg];[bg][nar]amix=inputs=2:duration=first:dropout_transition=0[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
         str(out_mp4)], work)


def run(args: list[str], cwd: Path | None = None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(" ".join(map(str, args[:6])) + "\n" + r.stderr[-1200:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--out", default="output/alice_narr")
    ap.add_argument("--voice", default="shikoku_metan")
    ap.add_argument("--style", type=int, default=2)
    ap.add_argument("--styles", default="", help="シーン別スタイル: 1=76,5=2 形式のカンマ区切り")
    ap.add_argument("--scenes", default="")
    ap.add_argument("--seed", type=int, default=3000)
    args = ap.parse_args()

    data = json.loads(Path(args.script).read_text(encoding="utf-8"))
    scenes = data["scenes"]
    if args.scenes:
        want = {int(x) for x in args.scenes.split(",")}
        scenes = [s for s in scenes if s["id"] in want]

    # シーン別スタイル map
    style_map = {}
    for item in (args.styles or "").split(","):
        if "=" in item:
            k, v = item.split("=")
            style_map[int(k)] = int(v)

    out_dir = Path(args.out).resolve()
    audio_dir = out_dir / "narr"
    audio_dir.mkdir(parents=True, exist_ok=True)
    if not vv_health():
        raise SystemExit("VOICEVOX が起動していません (127.0.0.1:50021)")

    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    fails = []
    for s in scenes:
        prefix = f"scene_{s['id']:02d}"
        dest = out_dir / f"{prefix}.mp4"
        final = out_dir / f"{prefix}_narr.mp4"
        if final.exists() and final.stat().st_size > 500_000:
            print(f"[{prefix}] 既存スキップ", flush=True)
            continue
        try:
            # 1) 効果音のみのシーン生成
            scene_mp4 = run_scene(strip_speech(s["prompt"]), args.seed + s["id"] * 17,
                                  prefix, out_dir)
            # 2) ナレーション合成
            narr = extract_narration(s["prompt"])
            if narr:
                style = style_map.get(s["id"], args.style)
                wav = audio_dir / f"{prefix}.wav"
                dur = vv_synth(narr, style, wav)
                print(f"[{prefix}] narration {dur:.1f}s (style {style}): {narr[:30]}...", flush=True)
            else:
                wav = None
            # 3) ミックス
            if wav:
                mix_scene(scene_mp4.resolve(), wav.resolve(), final.resolve())
            else:
                dest.replace(final)
            print(f"[{prefix}] done -> {final.name}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[{prefix}] FAILED: {e}", flush=True)
            fails.append(s["id"])
    if fails:
        print("FAILED:", fails, flush=True)
        sys.exit(2)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
