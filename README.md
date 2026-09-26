# speech_video_gen

AI 数字人口播 / 讲课视频生成系统。本地离线部署，单卡 RTX 4090，仅本人使用。

上传一段正面讲话视频 + 一段音频素材（提取音色）+ 一份讲稿 → 输出口播视频。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/MVP方案.md](docs/MVP方案.md) | **先看这个**。选型、架构、P0 手测清单、风险与实施计划 |
| [docs/技术设计方案.md](docs/技术设计方案.md) | 完整技术方案：模型选型对比、许可证评估、长视频一致性、演进路线 |

## 快速开始

```bash
# 1. 环境：WSL2 + Docker + NVIDIA Container Toolkit
wsl --install -d Ubuntu-22.04          # Windows 侧（管理员 PowerShell）
nvidia-smi                              # WSL 内验证 GPU 透传
docker run --rm --gpus all nvidia/cuda:11.6.2-base-ubuntu20.04 nvidia-smi

# 2. 依赖
pip install -r requirements.txt         # 另外需要 ffmpeg / ffprobe 在 PATH 里

# 3. 启动后端服务（另开两个终端）
cd ~/Duix-Avatar/deploy && docker-compose -f docker-compose-lite.yml up -d
cd ~/GPT-SoVITS && python api_v2.py -a 127.0.0.1 -p 9880

# 4. 先跑 P0 自检，确认挂载点和服务都对
python scripts/preflight.py --video test.mp4 --audio test.wav

# 5. 生成
python cli.py --video talk.mp4 --audio voice.wav --script-file script.txt
python app.py                           # 或浏览器打开 localhost:7860
```

## 命令行

```bash
python cli.py --video talk.mp4 --audio voice.wav --script-file script.txt
python cli.py --video talk.mp4 --audio voice.wav --script "今天讲三个话题。" --mock
```

| 参数 | 说明 |
|---|---|
| `--video` / `--audio` | 正面讲话视频 / 音色参考音频 |
| `--script` / `--script-file` | 讲稿正文或文件（UTF-8），二选一 |
| `--ref-text` | 参考音频对应的文字，留空自动 ASR |
| `--mock` | 不连真服务，用本地假后端跑通链路 |
| `-v` | 打印 DEBUG 日志 |

## mock 模式

没有 Docker 也能验证编排逻辑、路径映射和进度回调：

```bash
python cli.py --video talk.mp4 --audio voice.wav --script-file script.txt --mock
```

`MockHeyGemClient` 只替换了 `health` / `submit` / `query` 三个 HTTP 方法，轮询、路径映射复用真实实现，
所以 mock 下能跑通的东西，换真服务只需要去掉 `--mock`。
**成片只是把输入视频和音频合流，口型不会同步** —— 它验证的是链路，不是效果。

## 项目结构

```
├── app.py                    # Gradio Web UI
├── cli.py                    # 命令行入口
├── config.yaml               # 配置
├── core/
│   ├── config.py             # 配置加载
│   ├── logs.py               # 日志配置
│   ├── media.py              # ffmpeg 封装 + PathMapper ★
│   ├── asr.py                # 参考音频转写（可选）
│   ├── heygem.py             # HeyGem 客户端 + Mock ★
│   ├── pipeline.py           # 流程编排 ★
│   └── tts/                  # TTS 引擎（GPT-SoVITS / Mock）
├── scripts/preflight.py      # P0 手测自动化
├── workspace/                # 运行时产物（git 忽略）
└── logs/                     # 日志与自检报告（git 忽略）
```

## 技术栈

- **唇形驱动**：Duix.Avatar (HeyGem) lite — 检索拼接式，无需训练
- **声音克隆**：GPT-SoVITS — 零样本推理
- **Web UI**：Gradio
- **运行环境**：Windows 10 + WSL2 (Ubuntu 22.04) + Docker + NVIDIA Container Toolkit

## 状态

**P0 已通过**：完整链路（讲稿 → GPT-SoVITS 克隆音色 → HeyGem 口型驱动 → 出片）实测跑通。
参考数据：约 110 字讲稿、6 秒参考音频，端到端 **113.6 秒**，成片 1080×1920 / 57.7 秒。

- [x] 配置、媒体处理、路径映射
- [x] HeyGem 客户端（真机 + Mock）
- [x] TTS 模块 + ASR 兜底
- [x] 流程编排 + 命令行
- [x] Gradio Web UI
- [x] P0 自检脚本
- [x] **P0 手测**（10/11 项通过，未通过项为可选依赖）
- [x] 接通真实 HeyGem / GPT-SoVITS
- [ ] 人脸保真度：重生人脸有"美颜化"倾向（源视频人脸占画面过大时更明显），
      与 MVP 验收标准未覆盖，待评估

### 已知环境坑

跑之前请先看 [docs/MVP方案.md 9.2 节](docs/MVP方案.md#92-环境级坑不在代码里但会反复踩)：
Docker 的 `containerd-snapshotter` 解包、GPT-SoVITS 镜像的 numba 空缓存、
以及 `apad` 无限补静音，这三个都不是代码问题、但都会让链路跑不起来。
