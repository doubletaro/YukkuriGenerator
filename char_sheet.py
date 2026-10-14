"""Qwen-Image-2.1 viggle-turbo でキャラクターシート(立ち絵)を生成する。

使い方:
  python char_sheet.py --out output/sheets --name kintaro --prompt "..." [--count 3]
生成後、vision またはユーザー確認で採用シートを決める。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from yukkuri.folktale import DEFAULT_STYLE_BASE, verify_sheet  # noqa: E402

COMFY = "http://127.0.0.1:8188"
UNET = "qwen_image_2.1_int8_convrot.safetensors"
CLIP = "qwen3vl_8b_int8_convrot.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"
SIGMAS = "1.0, 0.9375, 0.875, 0.75, 0.5, 0.25"  # viggle turbo 6step
STYLE = (f"{DEFAULT_STYLE_BASE}"
         "キャラクターデザインシート、1枚に同一キャラの三面図(正面・横向き・背面)を横一列に並べる。"
         "3体ともA-poseで自然に腕を下ろし直立、互いに重ならない、白背景、単独。")
STYLE_SUFFIX = STYLE[len(DEFAULT_STYLE_BASE):]  # ベースより後ろの指示部分


def api_prompt(prompt_text: str, seed: int, prefix: str, width=1024, height=1024) -> dict:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "lora_name": "Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r256.safetensors",
            "strength_model": 1.0, "model": ["1", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {
            "text": prompt_text, "clip": ["2", 0]}},
        "6": {"class_type": "EmptyLatentImage", "inputs": {
            "width": width, "height": height, "batch_size": 1}},
        "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed, "control_after_generate": "fixed"}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "9": {"class_type": "ViggleTurboSigmas", "inputs": {
            "nodes": SIGMAS, "latent": ["6", 0]}},
        "10": {"class_type": "BasicGuider", "inputs": {"model": ["4", 0], "conditioning": ["5", 0]}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["7", 0], "guider": ["10", 0], "sampler": ["8", 0],
            "sigmas": ["9", 0], "latent_image": ["6", 0]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {
            "images": ["12", 0], "filename_prefix": f"charsheet/{prefix}"}},
    }


def post(path: str, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(COMFY + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_json(path: str, timeout: int = 60):
    with urllib.request.urlopen(COMFY + path, timeout=timeout) as r:
        return json.loads(r.read())


def wait_output(prompt_id: str, timeout_s: int = 1800) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        h = get_json(f"/history/{prompt_id}")
        if prompt_id in h:
            e = h[prompt_id]
            if e.get("status", {}).get("completed") or e.get("outputs"):
                return e
            if e.get("status", {}).get("status_str") == "error":
                raise RuntimeError(json.dumps(e["status"], ensure_ascii=False)[:1500])
        time.sleep(5)
    raise TimeoutError(prompt_id)


def fetch(entry: dict, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for node_out in entry.get("outputs", {}).values():
        for item in node_out.get("images", []) or []:
            sub = item.get("subfolder", "")
            fn = item["filename"]
            url = f"/view?filename={fn}&subfolder={sub}&type=output"
            p = dest / fn
            with urllib.request.urlopen(COMFY + url, timeout=300) as r, open(p, "wb") as f:
                f.write(r.read())
            out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="output/sheets")
    ap.add_argument("--name", default="kintaro")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7000)
    ap.add_argument("--no-verify", action="store_true",
                    help="生成後の三面図ピクセル検証をスキップ")
    ap.add_argument("--style-note", default="",
                    help="作品固有の作風指示を STYLE に追記(90年代セル画ベースは不変)")
    ap.add_argument("--style-base", default=None,
                    help="スタイル基盤文(未指定は DEFAULT_STYLE_BASE)")
    ap.add_argument("--sheet-suffix", default=None,
                    help="シート形式の指示文(未指定はキャラクターデザインシート三面図。実写は写真参照文を指定)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    base = args.style_base or DEFAULT_STYLE_BASE
    if base and not base.endswith("。"):
        base += "。"
    suffix = args.sheet_suffix if args.sheet_suffix is not None else STYLE_SUFFIX
    full = f"{base}{suffix} {args.style_note} {args.prompt}".strip()
    ok_counts = []
    for i in range(args.count):
        # 検証不合格はseedを変えて最大3回リトライ(キャラごとに合格1枚あれば十分)
        ok = 0
        for attempt in range(3):
            payload = {"prompt": api_prompt(full, args.seed + (i * 3 + attempt) * 13,
                                            f"{args.name}_sheet_{i+1:02d}"),
                       "client_id": "yukkuri-charsheet"}
            resp = post("/prompt", payload)
            pid = resp["prompt_id"]
            print(f"[sheet {i+1} attempt {attempt+1}] prompt_id={pid}", flush=True)
            entry = wait_output(pid)
            files = fetch(entry, out_dir)
            print(f"[sheet {i+1} attempt {attempt+1}] saved: {files}", flush=True)
            if args.no_verify:
                ok = 1
                break
            all_ok = True
            for f in files:
                passed, detail = verify_sheet(f)
                print(f"[verify] {f.name}: {'OK' if passed else 'FAIL - ' + detail}", flush=True)
                all_ok = all_ok and passed
            if all_ok:
                ok = 1
                break
        ok_counts.append(ok)
    if not any(ok_counts):
        raise SystemExit(f"{args.name}: 検証合格シートが1枚も無し。seed/prompt を変えて再生成してください。")
    if not all(ok_counts):
        print(f"[warn] {args.name}: 一部シートが不合格ですが合格分があるため続行", flush=True)


if __name__ == "__main__":
    main()
