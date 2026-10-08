# YukkuriGenerator

A pipeline that generates videos **without any human intervention** from a single theme. Two modes:

1. **Yukkuri dialogue/commentary videos** (Zundamon × Shikoku Metan) — VOICEVOX speech + character sprites + lip-sync
2. **Folktale animations** — 15-second-scene cinematic anime via ComfyUI / MiniMax H3

## Pipeline

### Yukkuri dialogue video

```
theme → [script]  LLM dialogue script (Zundamon × Shikoku Metan banter)
      → [tts]     VOICEVOX synthesis + phoneme timeline
      → [render]  lip-sync frames + ASS subtitles + BGM → ffmpeg (NVENC / libx264)
      → [publish] save locally
```

### Folktale anime (story video)

```
theme (title) → [script]  12-scene script (15 s each / continue flags / narration + SFX directions)
     → [sheet]   character sheets (turnaround: front / side / back — keeps characters consistent)
     → [scene]   scene clips via ComfyUI (MiniMax H3 fl2va), 15 s × N
                 └ scenes with continue:true start from the last second of the previous scene (seamless)
     → [concat]  concat all scenes + Japanese-style title / end cards
     → [publish] upload to YouTube
```

- A 3-minute story = 12 scenes. Each scene is a standalone mp4; continue flags make consecutive scenes visually continuous
- Fully automated script → sheets → render → concat → publish. Failed scenes are skipped and resumed on retry

## Usage

```bash
# Yukkuri dialogue video
.venv/Scripts/python.exe -m yukkuri make --theme "What is a DGX Spark?" --minutes 1.5
# with reference material
.venv/Scripts/python.exe -m yukkuri make --theme "..." --source research.md

# Folktale anime (e.g. Jack and the Beanstalk)
# 1) prepare a script JSON (output/<title>_script.json) 2) run the stages in order
.venv/Scripts/python.exe char_sheet.py --name <char> --prompt "..." --out output/sheets_chars
.venv/Scripts/python.exe comfy_story.py output/<title>_script.json --out output/<title>
.venv/Scripts/python.exe concat_story.py output/<title> --out output/<title>/final.mp4 --title <Title> --title-style folktale

# tests
.venv/Scripts/python.exe -m pytest
```

Outputs: yukkuri=`output/final/video.mp4` (+ meta.json) / folktale=`output/<title>/final.mp4`. Intermediates are kept per-title under `output/<title>/`.

## Design

- **Stage pattern**: `yukkuri/stages/` — new features = new stages only
- **Swappable engines**: ABCs in `yukkuri/engines/base.py` (TtsEngine / ScriptWriter / AvatarRenderer / Publisher). New video styles plug in as extra Renderer/Publisher
- **All tuning in YAML**: `config/render.yaml` (duration / min lip-sync frame / gaps / subtitles / BGM gain) and `config/characters.yaml` (speakers, sprites, speech style)
- PSD sprites ported from StockSeeker (`zundamon.2.3.psd` / `metan.2.1.psd`). Falls back to SimpleAvatarRenderer without the PSDs

## External dependencies

| Dependency | Location | Notes |
|---|---|---|
| ComfyUI | `http://127.0.0.1:8188` | MiniMax H3 workflow (folktale mode) |
| VOICEVOX engine | `%LOCALAPPDATA%/Programs/VOICEVOX/vv-engine/run.exe` | auto-started if not running |
| ffmpeg | PATH or winget | required for subtitles/ass/amix |
| LLM | OpenAI-compatible API (`llm` in `config/render.yaml`) | local LLM |

## Environment variables (secrets are never hardcoded)

| Variable | Purpose |
|---|---|
| `YUKKURI_CLIENT_SECRETS` | path to the Google OAuth client secrets JSON |
| `YUKKURI_TOKEN_FILE` | OAuth token cache (generated on first run; `assets/token_aifolktale.json.example` is a template) |
| `YUKKURI_STOCKSEEKER` | path to a local StockSeeker checkout providing the uploader |
| `YUKKURI_TG_TOKEN` / `YUKKURI_TG_CHAT_ID` | optional Telegram notification |

## License

Apache License 2.0. **Modified versions must be published as forks** — do not distribute private modified copies.

Note the licenses of the characters/models used:

- Zundamon / Shikoku Metan (VOICEVOX characters): follow each official term of use
  - Zundamon: https://zundamon.net/
  - Shikoku Metan: https://odashutter.jp/
- Video model (MiniMax H3): subject to the model's own license
- When publishing generated videos, check the credit/attribution requirements of the above terms
