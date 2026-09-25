# speech_video_gen

AI 数字人口播 / 讲课视频生成系统。本地离线部署，单卡 RTX 4090，仅本人使用。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/MVP方案.md](docs/MVP方案.md) | **先看这个**。最小可用版本：Duix.Avatar(lite) + GPT-SoVITS + Gradio，约 4–5 个工作日 |
| [docs/技术设计方案.md](docs/技术设计方案.md) | 完整技术方案：模型选型对比、许可证评估、长视频一致性、后续演进路线 |

## 快速开始

```bash
# 1. 先做 P0 手测（不要跳过）
#    见 docs/MVP方案.md 第 3 章

# 2. 环境
wsl --install -d Ubuntu-22.04        # Windows 侧
nvidia-smi                            # WSL 内验证 GPU 透传
docker run --rm --gpus all nvidia/cuda:11.6.2-base-ubuntu20.04 nvidia-smi

# 3. 启动服务
cd ~/Duix-Avatar/deploy && docker-compose -f docker-compose-lite.yml up -d
cd ~/GPT-SoVITS && python api_v2.py -a 127.0.0.1 -p 9880

# 4. 启动 UI
python app.py                         # 浏览器打开 localhost:7860
```

## 技术栈

- **唇形驱动**：Duix.Avatar (HeyGem) lite — 检索拼接式，无需训练
- **声音克隆**：GPT-SoVITS — 零样本推理
- **Web UI**：Gradio
- **运行环境**：Windows 10 + WSL2 (Ubuntu 22.04) + Docker + NVIDIA Container Toolkit

## 状态

方案设计阶段。代码未开始，请先完成 MVP 方案的 P0 手测。
