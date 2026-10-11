"""昔ばなし台本を GLM(DGX01 tensorfold)で一括生成し、自動検証する。

使い方:
  python make_script.py --title つるの恩返し --chars tsuru --outline-file outline.txt \
      --out output/019_tsuru --scenes 12

- data/char_specs.json のエントリから【キャラ】固定文を組み立てる
- thinking モデル対応: まず思考OFFで生成し、空応答なら思考ON+大tokenで再試行
- 検証(validate_script)合格まで最大 --retries 回再生成する
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from yukkuri.folktale import (  # noqa: E402
    STYLE_SENTENCE,
    build_system_prompt,
    build_user_prompt,
    extract_json,
    validate_script,
)

SPEC_PATH = Path("data/char_specs.json")
DEFAULT_ENDPOINT = "http://192.168.1.21:8888/v1"
DEFAULT_MODEL = "GLM-5.3-Flash-EXL3"
# 思考OFFでも空になりうるため余裕を持つ。thinking時は思考分を含めて大きく取る
MAX_TOKENS_NOTHINK = 16000
MAX_TOKENS_THINK = 120000


def char_fixed_sentences(key: str) -> dict[str, str]:
    """char_specs.json エントリ → {名前: 固定描写文}。"""
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))[key]
    return {c["name"]: c["desc"] for c in spec["characters"].values()}


def load_outline(path: str) -> list[str]:
    """outline ファイル: 「1: あらすじ」形式でも単純な行リストでも可。"""
    lines = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        ln = ln.split(":", 1)[1].strip() if ln[:1].isdigit() and ":" in ln else ln
        lines.append(ln)
    return lines


def call_llm(endpoint: str, model: str, system: str, user: str,
             thinking: bool, timeout: int = 3600) -> tuple[str, str]:
    """(content, finish_reason) を返す。thinking モデル対応。"""
    body = {"model": model, "temperature": 0.8, "max_tokens": MAX_TOKENS_THINK if thinking
            else MAX_TOKENS_NOTHINK, "messages": [{"role": "system", "content": system},
                                                  {"role": "user", "content": user}]}
    if not thinking:
        body["chat_template_kwargs"] = {"thinking": False}
    r = requests.post(f"{endpoint.rstrip('/')}/chat/completions", json=body, timeout=timeout)
    r.raise_for_status()
    out = r.json()
    choice = out["choices"][0]
    return (choice["message"].get("content") or "", choice.get("finish_reason") or "")


def generate(endpoint: str, model: str, system: str, user: str,
             n_scenes: int, min_chars: int, max_chars: int, retries: int) -> tuple[dict, list[str]]:
    """検証合格まで再生成。戻り値は (script, 最終エラー一覧)。"""
    last_errs = ["未生成"]
    for attempt in range(1, retries + 1):
        thinking = attempt > retries - 1  # 最終試行のみ思考ONにフォールバック
        label = "思考ON" if thinking else "思考OFF"
        print(f"[gen {attempt}/{retries}] {label} で生成中...", flush=True)
        content, finish = call_llm(endpoint, model, system, user, thinking)
        if not content:
            last_errs = [f"空応答(finish={finish})"]
            print(f"  -> 空応答(finish={finish})", flush=True)
            continue
        try:
            data = extract_json(content)
        except json.JSONDecodeError as e:
            last_errs = [f"JSONパース失敗: {e}"]
            print(f"  -> {last_errs[0]}", flush=True)
            continue
        errs = validate_script(data, n_scenes=n_scenes, min_chars=min_chars, max_chars=max_chars)
        if errs:
            last_errs = errs
            print(f"  -> 検証NG: {errs[:5]}", flush=True)
            continue
        return data, []
    return {}, last_errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True, help="作品名(記録用)")
    ap.add_argument("--chars", required=True, help="char_specs.json のエントリキー")
    ap.add_argument("--outline-file", required=True, help="シーンあらすじ(行=シーン)")
    ap.add_argument("--out", required=True, help="出力ディレクトリ")
    ap.add_argument("--scenes", type=int, default=0, help="シーン数(未指定はoutline行数)")
    ap.add_argument("--min-chars", type=int, default=550)
    ap.add_argument("--max-chars", type=int, default=700)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--retries", type=int, default=3)
    args = ap.parse_args()

    outline = load_outline(args.outline_file)
    n_scenes = args.scenes or len(outline)
    system = build_system_prompt(char_fixed_sentences(args.chars), outline,
                                 args.min_chars, args.max_chars)
    user = build_user_prompt(n_scenes, args.min_chars, args.max_chars)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    req = {"model": args.model, "temperature": 0.8, "max_tokens": MAX_TOKENS_NOTHINK,
           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    (out / "req.json").write_text(json.dumps(req, ensure_ascii=False, indent=1), encoding="utf-8")

    script, errs = generate(args.endpoint, args.model, system, user,
                            n_scenes, args.min_chars, args.max_chars, args.retries)
    if errs:
        raise SystemExit("台本生成に失敗: " + "; ".join(errs[:8]))

    (out / "script.json").write_text(json.dumps({"title": args.title, "scenes": script["scenes"]},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    lens = [len(s["prompt"]) for s in script["scenes"]]
    cont = [s["continue"] for s in script["scenes"]]
    print(f"[OK] {out / 'script.json'}: {len(script['scenes'])}シーン, "
          f"prompt {min(lens)}〜{max(lens)}字, continue={cont}")
    print(f"スタイル固定文: {STYLE_SENTENCE[:40]}...")


if __name__ == "__main__":
    main()
