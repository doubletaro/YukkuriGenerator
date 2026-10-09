"""ComfyUI API で MiniMax H3 t2va を実行し、シーン動画を生成するクライアント。

使い方:
  python comfy_story.py script.json --out output/momotaro
workflow: user/default/workflows/video_minimax_h3_t2v.json (サブグラフ展開済みの構成を内蔵)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

COMFY = "http://127.0.0.1:8188"
UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VAE_V = "minimax_h3_video_vae_fp16.safetensors"
VAE_A = "minimax_h3_audio_vae_fp32.safetensors"
LORA = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
STEPS = 6  # turbo 8step lora + 6 steps (workflow のスイッチと同じ構成)

WIDTH, HEIGHT = 640, 480
LENGTH = 362  # 17k+5 @24fps ≒ 15.08秒 (H3の学習上限)


def api_prompt(prompt_text: str, seed: int, prefix: str, first_frame: str | None = None,
               ref_images: list[str] | None = None) -> dict:
    """first_frame: output 内の画像パス(相対) — 前シーン最終フレームからの続き生成に使用。
    ref_images: ComfyUI/input の画像リスト — キャラシート等の参照画像(R2V)。"""
    use_r2v = bool(ref_images) and not first_frame
    node_class = "MiniMaxH3ReferenceToVideo" if use_r2v else "MiniMaxH3ImageToVideo"
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_V}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_A}},
        "5": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "lora_name": LORA, "strength_model": 1.0, "model": ["1", 0]}},
        "6": {"class_type": node_class, "inputs": {
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
            "video": ["14", 0], "filename_prefix": f"momotaro/{prefix}", "format": "mp4"}},
    }
    nid = 16
    if ref_images and not first_frame:
        # R2V ノードは first_frame 非対応。シームレス継続が優先のため、
        # first_frame がある場合は参照画像を捨てて ImageToVideo で継続する。
        wf["6"]["inputs"]["audio_vae"] = ["4", 0]
        pairs = {}
        for i, img in enumerate(ref_images[:3]):
            wf[str(nid)] = {"class_type": "LoadImage", "inputs": {"image": img}}
            pairs[f"ref_images.ref_image_{i}"] = [str(nid), 0]
            nid += 1
        wf["6"]["inputs"].update(pairs)
        wf["6"]["inputs"]["ref_image_size"] = "match"
    if first_frame:
        # LoadImage の入力は ComfyUI/input/ からの相対パス
        wf[str(nid)] = {"class_type": "LoadImage", "inputs": {"image": first_frame}}
        wf["6"]["inputs"]["first_frame"] = [str(nid), 0]
    return wf


def post(path: str, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(COMFY + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_json(path: str, timeout: int = 60):
    with urllib.request.urlopen(COMFY + path, timeout=timeout) as r:
        return json.loads(r.read())


def download(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=600) as r, open(dest, "wb") as f:
        f.write(r.read())


def wait_output(prompt_id: str, timeout_s: int = 7200) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        h = get_json(f"/history/{prompt_id}")
        if prompt_id in h:
            entry = h[prompt_id]
            if entry.get("status", {}).get("completed") or entry.get("outputs"):
                return entry
            if entry.get("status", {}).get("status_str") == "error":
                raise RuntimeError(json.dumps(entry.get("status", {}), ensure_ascii=False)[:2000])
        time.sleep(10)
    raise TimeoutError(f"prompt_id {prompt_id} が {timeout_s}s 内に完了しませんでした")


def outputs_of(entry: dict) -> list[str]:
    """history エントリから SaveVideo の出力ファイル(gifs/videos)を収集。"""
    files = []
    for node_out in entry.get("outputs", {}).values():
        for key in ("gifs", "videos", "images", "audio"):
            for item in node_out.get(key, []) or []:
                sub = item.get("subfolder", "")
                fn = item.get("filename")
                if fn:
                    files.append(f"{sub}/{fn}" if sub else fn)
    return files


def run_scene(prompt_text: str, seed: int, prefix: str, out_dir: Path,
              first_frame: str | None = None, ref_images: list[str] | None = None) -> Path:
    payload = {"prompt": api_prompt(prompt_text, seed, prefix, first_frame=first_frame,
                                    ref_images=ref_images),
               "client_id": "yukkuri-story"}
    resp = post("/prompt", payload)
    pid = resp["prompt_id"]
    print(f"[{prefix}] submitted prompt_id={pid}", flush=True)
    entry = wait_output(pid)
    files = outputs_of(entry)
    if not files:
        raise RuntimeError(f"[{prefix}] 出力ファイルなし: {json.dumps(entry.get('outputs', {}))[:500]}")
    dest = out_dir / f"{prefix}.mp4"
    url = f"/view?filename={files[0].split('/')[-1]}&subfolder={files[0].rsplit('/', 1)[0] if '/' in files[0] else ''}&type=output"
    download(COMFY + url, dest)
    print(f"[{prefix}] saved {dest} ({dest.stat().st_size/1e6:.1f} MB)", flush=True)
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script", help="momotaro_script.json")
    ap.add_argument("--out", default="output/momotaro")
    ap.add_argument("--scenes", default="", help="カンマ区切りで限定(例: 1,2)")
    ap.add_argument("--start-seed", type=int, default=1000)
    ap.add_argument("--refs", default="", help="カンマ区切りの参照画像(ComfyUI/input 相対)。キャラシートを R2V で渡す")
    args = ap.parse_args()

    data = json.loads(Path(args.script).read_text(encoding="utf-8"))
    scenes = data["scenes"]
    if args.scenes:
        want = {int(x) for x in args.scenes.split(",")}
        scenes = [s for s in scenes if s["id"] in want]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    fails = []

    # R2V 参照画像: ComfyUI/input にコピーしてファイル名リストへ
    ref_images = None
    if args.refs:
        input_dir_ref = Path(r"C:\wk\samples\ComfyUI\input")
        input_dir_ref.mkdir(parents=True, exist_ok=True)
        ref_images = []
        for rp in args.refs.split(","):
            rp = Path(rp.strip())
            if not rp.exists():
                print(f"[refs] 参照画像が見つからずスキップ: {rp}", flush=True)
                continue
            dest = input_dir_ref / rp.name
            shutil.copyfile(rp, dest)
            ref_images.append(rp.name)
        if ref_images:
            print(f"[refs] R2V 参照画像: {ref_images}", flush=True)
        else:
            ref_images = None

    # シームレス連結: 台本の各シーンに "continue": true がある場合、
    # 前シーンの最終フレームを first_frame として渡す
    all_scenes = data["scenes"]
    id2scene = {s["id"]: s for s in all_scenes}
    input_dir = Path(r"C:\wk\samples\ComfyUI\input")

    def last_frame_of(prev_scene_id: int) -> str | None:
        """前シーンmp4の最終フレームを ComfyUI/input へ抽出し、ファイル名を返す。"""
        import subprocess
        prev = out_dir / f"scene_{prev_scene_id:02d}.mp4"
        if not prev.exists():
            return None
        ff = shutil.which("ffmpeg") or "ffmpeg"
        fname = f"lastframe_{prev_scene_id:02d}.png"
        dest = input_dir / fname
        r = subprocess.run([ff, "-y", "-v", "error", "-sseof", "-0.2", "-i", str(prev),
                            "-frames:v", "1", str(dest)], capture_output=True, timeout=60)
        if r.returncode != 0 or not dest.exists():
            return None
        return fname

    for s in scenes:
        prefix = f"scene_{s['id']:02d}"
        dest = out_dir / f"{prefix}.mp4"
        if dest.exists() and dest.stat().st_size > 500_000:
            print(f"[{prefix}] 既存スキップ", flush=True)
            continue
        try:
            first_frame = None
            if s.get("continue"):
                # 直前のシーン(id順で自分より前の最後)を探す
                prev_ids = [x["id"] for x in all_scenes if x["id"] < s["id"]]
                if prev_ids:
                    ff = last_frame_of(max(prev_ids))
                    if ff:
                        first_frame = ff
                        print(f"[{prefix}] seamless: first_frame={ff}", flush=True)
                    else:
                        print(f"[{prefix}] 前シーンのフレーム抽出失敗、t2vで続行", flush=True)
            run_scene(s["prompt"], args.start_seed + s["id"] * 17, prefix, out_dir,
                      first_frame=first_frame, ref_images=ref_images)
        except Exception as e:  # noqa: BLE001 — 1シーン失敗でも続行、最後に報告
            print(f"[{prefix}] FAILED: {e}", flush=True)
            fails.append((s["id"], str(e)))
    if fails:
        print("FAILED SCENES:", fails, flush=True)
        sys.exit(2)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
