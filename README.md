# YukkuriGenerator

テーマを与えると**人間の介在なしに**動画を生成するパイプライン。2つのモードを持つ。

1. **ゆっくり対話解説動画**(ずんだもん×四国めたん) — VOICEVOX音声+立ち絵+口パク
2. **昔話アニメ**(物語動画) — ComfyUI / MiniMax H3 による15秒シーン連結型のセルアニメ

## パイプライン

### ゆっくり対話解説動画

```
テーマ → [script] LLM対話台本生成(ずんだもん×四国めたんの掛け合い)
       → [tts]    VOICEVOX 音声合成 + 音素タイムライン
       → [render] 口パクフレーム + ASS字幕 + BGM → ffmpeg (NVENC/ libx264)
       → [publish]ローカル保存
```

### 昔話アニメ(物語動画)

```
テーマ(作品名) → [script] 12シーン台本生成(各15秒 / continueフラグ / ナレーション+効果音指定)
     → [sheet]  キャラクターシート生成(3面図: 正面・側面・背面 — キャラ一貫性の担保)
     → [scene]  ComfyUI (MiniMax H3 fl2va) で15秒×N本のシーン動画を生成
                └ continue:true のシーンは「前シーンの最終1秒」を入力にし、シームレスに継続
     → [concat] 全シーン連結 + 和風タイトルカード / エンドカード
     → [publish]YouTubeアップロード
```

- 3分動画 = 12シーン構成。シーンは独立したmp4だが、継続フラグで見た目上つながりのある映像になる
- 台本→シート→生成→連結→投稿まで自動。失敗シーンは既存成果物をスキップして再開(resume)可能

## 使い方

```bash
# ゆっくり対話解説動画
.venv/Scripts/python.exe -m yukkuri make --theme "DGX Sparkとは何か?" --minutes 1.5
# 資料つき
.venv/Scripts/python.exe -m yukkuri make --theme "..." --source research.md

# 昔話アニメ(例: ジャックと豆の木)
# 1) 台本JSONを用意(output/<作品>_script.json) 2) 各ステージを順に実行
.venv/Scripts/python.exe char_sheet.py --name <キャラ> --prompt "..." --out output/sheets_chars
.venv/Scripts/python.exe comfy_story.py output/<作品>_script.json --out output/<作品>
.venv/Scripts/python.exe concat_story.py output/<作品> --out output/<作品>/final.mp4 --title <作品名> --title-style folktale

# テスト
.venv/Scripts/python.exe -m pytest
```

成果物: ゆっくり=`output/final/video.mp4`(+ meta.json) / 昔話=`output/<作品>/final.mp4`。中間物は各作品ディレクトリに保存。

## 設計

- **Stage パターン**: `yukkuri/stages/` — 機能追加は Stage 追加のみ
- **エンジン差し替え可**: `yukkuri/engines/base.py` の ABC(TtsEngine / ScriptWriter / AvatarRenderer / Publisher)。新しい動画スタイル(ゆっくり以外)も Publisher/Renderer 追加で拡張可能
- **同期変数は全部 YAML**: `config/render.yaml`(尺/口パク最小フレーム/gap/字幕/BGM音量)と `config/characters.yaml`(話者・立ち絵・口調)
- PSD 立ち絵は StockSeeker から移植(`zundamon.2.3.psd` / `metan.2.1.psd`)。PSD無し環境では SimpleAvatarRenderer にフォールバック

## 外部依存

| 依存 | 場所 | 備考 |
|---|---|---|
| ComfyUI | `http://127.0.0.1:8188` | MiniMax H3 ワークフロー(昔話モード) |
| VOICEVOX engine | `%LOCALAPPDATA%/Programs/VOICEVOX/vv-engine/run.exe` | 未起動なら自動起動 |
| ffmpeg | PATH or winget | subtitles/ass/amix 必須 |
| LLM | OpenAI互換 API(`config/render.yaml` の llm) | ローカルLLM |

## ライセンス

Apache License 2.0。**改変した場合はフォークして公開する必要があります**(改変版を非公開のまま配布しないでください)。

使用するキャラクター・モデルのライセンスに注意:

- ずんだもん / 四国めたん(VOICEVOX キャラクター): 各公式の利用規約に従うこと
  - ずんだもん: https://zundamon.net/
  - 四国めたん: https://odashutter.jp/
- 映像生成モデル(MiniMax H3)はモデル側のライセンスに従う
- 生成動画を公開する場合は、上記規約のクレジット表記要件を確認すること
