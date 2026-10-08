# YukkuriGenerator

テーマを与えると**人間の介在なしに**ゆっくり対話解説動画(ずんだもん×四国めたん)を生成するパイプライン。

```
テーマ → [script] LLM台本生成(GLM/DGX01)
       → [tts]    VOICEVOX 音声合成 + 音素タイムライン
       → [render] 口パクフレーム + ASS字幕 + BGM → ffmpeg (NVENC/ libx264)
       → [publish]ローカル保存 / Telegram送信
```

## 使い方

```bash
.venv/Scripts/python.exe -m yukkuri make --theme "DGX Sparkとは何か?" --minutes 1.5
# 資料つき
.venv/Scripts/python.exe -m yukkuri make --theme "..." --source research.md
# テスト
.venv/Scripts/python.exe -m pytest
```

成果物: `output/final/video.mp4`(+ meta.json)。中間物は `output/work/`。

## 設計

- **Stage パターン**: `yukkuri/stages/` — 機能追加は Stage 追加のみ
- **エンジン差し替え可**: `yukkuri/engines/base.py` の ABC(TtsEngine / ScriptWriter / AvatarRenderer / Publisher)
- **同期変数は全部 YAML**: `config/render.yaml`(尺/口パク最小フレーム/gap/字幕/BGM音量)と `config/characters.yaml`(話者・立ち絵・口調)
- PSD 立ち絵は StockSeeker から移植(`zundamon.2.3.psd` / `metan.2.1.psd`)。PSD無し環境では SimpleAvatarRenderer にフォールバック

## 外部依存

| 依存 | 場所 | 備考 |
|---|---|---|
| VOICEVOX engine | `%LOCALAPPDATA%/Programs/VOICEVOX/vv-engine/run.exe` | 未起動なら自動起動 |
| ffmpeg | PATH or winget | subtitles/ass/amix 必須 |
| LLM | OpenAI互換 API(`config/render.yaml` の llm) | DGX01 tensorfold GLM |

## Telegram 自動報告

環境変数 `YUKKURI_TG_TOKEN` `YUKKURI_TG_CHAT_ID` があるときのみ sendVideo する。
無設定では Local のみ。cron から呼ぶときに設定する。
