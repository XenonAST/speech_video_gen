"""Gradio Web UI。

    python app.py            # 浏览器打开 http://localhost:7860
"""

from __future__ import annotations

import argparse
import os

# 必须在 import gradio 之前设置：Gradio 默认会向 huggingface.co / api.gradio.app
# 发遥测和版本检查，离线环境下要等到超时才继续。
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import gradio as gr  # noqa: E402

from core import heygem as heygem_mod
from core.config import ensure_dirs, load_config
from core.logs import setup_logging
from core.pipeline import run_pipeline
from core.tts import build_tts

LOG_LINES = 12

cfg = load_config()
ensure_dirs(cfg)
setup_logging(cfg.paths.logs)


def check_services(mock: bool) -> str:
    if mock:
        return "mock 模式：跳过真实服务检查"
    heygem_ok = heygem_mod.build_heygem(cfg).health()
    tts_ok = build_tts(cfg).health()
    lines = [
        f"{'✓' if heygem_ok else '✗'} HeyGem  {cfg.services.heygem_base}",
        f"{'✓' if tts_ok else '✗'} TTS     {cfg.services.gpt_sovits_base}",
    ]
    if not (heygem_ok and tts_ok):
        lines.append("服务未就绪，先按 docs/MVP方案.md 第 3 章启动容器与 TTS 服务")
    return "\n".join(lines)


def generate(video, audio, script, ref_text, mock):
    logs: list[str] = []

    def emit(message: str) -> str:
        logs.append(message)
        return "\n".join(logs[-LOG_LINES:])

    if not video or not audio or not (script or "").strip():
        yield None, emit("请先上传视频、上传音频并填写讲稿")
        return

    try:
        pipeline = run_pipeline(
            video=video,
            audio_ref=audio,
            script=script,
            cfg=cfg,
            tts_engine=build_tts(cfg, mock=mock),
            heygem=heygem_mod.build_heygem(cfg, mock=mock),
            ref_text=ref_text or None,
        )
        for progress in pipeline:
            yield None, emit(f"[{progress.percent * 100:5.1f}%] {progress.message}")
        output = progress.output
    except Exception as e:
        yield None, emit(f"❌ {type(e).__name__}: {e}")
        return

    yield str(output), emit(f"✅ 成片：{output}")


with gr.Blocks(title="数字人口播视频生成") as demo:
    gr.Markdown("# 数字人口播视频生成\n上传素材 → 输入讲稿 → 生成口播视频")

    with gr.Row():
        with gr.Column():
            video_in = gr.Video(label="① 正面讲话视频（≥10 秒）", sources=["upload"])
            audio_in = gr.Audio(label="② 音频素材（提取音色，≥5 秒）", type="filepath")
            script_in = gr.Textbox(label="③ 讲稿", lines=8, placeholder="在此粘贴讲稿…")
            ref_text_in = gr.Textbox(label="参考音频文本（留空自动识别）")
            mock_in = gr.Checkbox(label="mock 模式（不连真服务，仅验证界面）", value=False)
            run_btn = gr.Button("生成口播视频", variant="primary")

        with gr.Column():
            video_out = gr.Video(label="成片")
            log_out = gr.Textbox(label="进度", lines=LOG_LINES, interactive=False)
            check_btn = gr.Button("检查服务状态")

    run_btn.click(
        fn=generate,
        inputs=[video_in, audio_in, script_in, ref_text_in, mock_in],
        outputs=[video_out, log_out],
    )
    check_btn.click(fn=check_services, inputs=[mock_in], outputs=[log_out])


def main() -> None:
    parser = argparse.ArgumentParser(description="数字人口播视频生成（Web UI）")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    demo.queue().launch(server_name=args.host, server_port=args.port)


if __name__ == "__main__":
    main()
