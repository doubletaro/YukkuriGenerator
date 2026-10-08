"""Qwen-Image-2.1 viggle-turbo でキャラクターシート(立ち絵)を生成する。

使い方:
  python char_sheet.py --out output/sheets --name kintaro --prompt "..." [--count 3]
生成後、vision またはユーザー確認で採用シートを決める。
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

COMFY = "http://127.0.0.1:8188"
UNET = "qwen_image_2.1_int8_convrot.safetensors"
CLIP = "qwen3vl_8b_int8_convrot.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"
SIGMAS = "1.0, 0.9375, 0.875, 0.75, 0.5, 0.25"  # viggle turbo 6step
STYLE = ("1990年代の日本の劇場アニメ風セルアニメ、温かみのある色調。"
         "キャラクターデザインシート、全身立ち絵、正面、白背景、単独。")


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
    args = ap.parse_args()

    out_dir = Path(args.out)
    full = f"{STYLE} {args.prompt}"
    for i in range(args.count):
        payload = {"prompt": api_prompt(full, args.seed + i * 13, f"{args.name}_sheet_{i+1:02d}"),
                   "client_id": "yukkuri-charsheet"}
        resp = post("/prompt", payload)
        pid = resp["prompt_id"]
        print(f"[sheet {i+1}] prompt_id={pid}", flush=True)
        entry = wait_output(pid)
        files = fetch(entry, out_dir)
        print(f"[sheet {i+1}] saved: {files}", flush=True)


if __name__ == "__main__":
    main()
