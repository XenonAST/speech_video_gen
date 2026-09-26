# MVP 方案 — 最小可用口播视频生成

> 版本：v1.0
> 日期：2026-09-26
> 关联文档：[技术设计方案.md](技术设计方案.md)
> 目标：**打通链路，端到端可用**。不做任何非必要功能。

---

## 0. 一句话方案

```
[正面演讲视频] + [音频素材] + [讲稿]
        │
        ├─ 音频素材 → GPT-SoVITS 零样本克隆音色 ─┐
        │                                        │
        └─ 讲稿 ────────────────────────────────┼→ 合成音频
                                                 │
        [视频] ──────────────────────────────────┼→ Duix.Avatar (lite) 唇形驱动
                                                 │
                                                 ▼
                                            口播视频.mp4
        Gradio Web UI 串起全部
```

**技术栈**：Duix.Avatar lite（Docker）+ GPT-SoVITS（Python）+ Gradio（Web UI）
**代码量**：约 500–800 行 Python
**部署形态**：Windows 10 + WSL2 + Docker，全程离线

---

## 1. 可行性结论

### 1.1 结论：✅ 可行

组件选型合理，链路清晰，无技术死结。但有一个**必须先验证的不确定性**（见 [1.3](#13-关键不确定点)）。

### 1.2 关键发现（改变了链路设计）

调研 Duix.Avatar API 时发现一个与直觉不符的事实：

> **`18180` 端口不是"形象训练"接口，而是 TTS 接口**（基于 fish-speech）。
> `/v1/preprocess_and_tran` 的参数是 `format` / `reference_audio` / `lang`，返回 `asr_format_audio_url` 和 `reference_audio_text` —— 这是**声音克隆的预处理**，与视频形象无关。

**推论：**

1. **视频合成不需要独立的"训练/定制"步骤** —— `8383/easy/submit` 直接接收 `video_url` + `audio_url` 两个路径就出片。这符合 HeyGem 的"检索拼接式"原理：合成时才在内部对视频做音素切分。
2. **`18180`（TTS）和 `10095`（ASR）都可以不要** —— 声音克隆用 GPT-SoVITS，参考文本用 ASR 自动转写。

**于是有了一个更省事的部署方式：**

| 部署文件 | 包含服务 | 镜像体积 | 内存要求 | 本方案 |
|---|---|---|---|---|
| `docker-compose.yml` | TTS + ASR + GenVideo | ~70GB | ≥32GB | ❌ 不需要 |
| **`docker-compose-lite.yml`** | **仅 GenVideo** | **小得多** | **低** | ✅ **采用** |
| `docker-compose-linux.yml` | 完整版 Linux | ~70GB | ≥32GB | ❌ |

**采用 lite 版的好处：**
- 镜像体积从 ~70GB 降到十几 GB
- 避开"16GB 内存时 ASR 服务起不来"这个高频坑
- 只暴露 `8383` 一个端口，路径映射关系简单

### 1.3 关键不确定点

> ⚠️ **必须在写代码之前验证**：`8383/easy/submit` 能否**纯 API** 完成合成，不依赖官方 GUI 客户端的任何预处理。

依据：社区文档中 `video_url` 直接传 mp4 路径，暗示无需预训练。但官方客户端存在"定制/克隆"步骤，无法确定那一步是客户端本地做的转码（无所谓）还是必须的模型生成（有所谓）。

**验证方法见 [第 3 章 P0 手测](#3-p0-手测先验证再开发)。跑通则方案成立，跑不通再讨论绕路。**

### 1.4 MVP 明确不做

| 不做的 | 理由 |
|---|---|
| ❌ GPT-SoVITS 微调 | 零样本够用，省掉一整套训练流程 |
| ❌ 字幕 | 讲稿已知，v2 加更容易 |
| ❌ BGM / 片头片尾 / 水印 | 非核心链路 |
| ❌ 人脸修复 / 超分 / 抠像 | 后处理，v2 加 |
| ❌ 素材入库 / 音素索引 / 极速档 | 高级特性，v2 加 |
| ❌ 长视频分段与一致性保障 | 先用短视频验证（≤3 分钟） |
| ❌ 任务队列 / 断点续跑 | 单用户串行，Gradio 直接阻塞等待即可 |
| ❌ 多用户 / 账号 | 个人使用 |
| ❌ 前端工程化 | Gradio 足够 |

> **MVP 的唯一目标：证明"视频 + 音频 + 讲稿 → 口播视频"这条路能走通。**

---

## 2. 架构设计

### 2.1 组件图

```
┌─────────────────────── Windows 10 ───────────────────────┐
│                                                          │
│   ┌──────────────────────────────────────────────────┐   │
│   │  WSL2 (Ubuntu 22.04)                             │   │
│   │                                                  │   │
│   │  ┌────────────────────────────────────────────┐  │   │
│   │  │  Gradio Web UI   :7860                     │  │   │
│   │  │  ┌──────────────────────────────────────┐  │  │   │
│   │  │  │ 上传视频 / 上传音频 / 讲稿 / 生成按钮 │  │  │   │
│   │  │  └──────────────────────────────────────┘  │  │   │
│   │  └────────────────┬───────────────────────────┘  │   │
│   │                   │                              │   │
│   │  ┌────────────────▼───────────────────────────┐  │   │
│   │  │  Pipeline (Python 编排)                     │  │   │
│   │  │  media.py → asr.py → tts/ → heygem.py      │  │   │
│   │  └────┬───────────────────────────┬───────────┘  │   │
│   │       │                           │              │   │
│   │  ┌────▼─────────────┐   ┌─────────▼───────────┐  │   │
│   │  │ GPT-SoVITS       │   │ Docker              │  │   │
│   │  │ api_v2.py :9880  │   │ ┌─────────────────┐ │  │   │
│   │  │ (纯 Python 服务) │   │ │ duix-avatar-    │ │  │   │
│   │  └──────────────────┘   │ │ gen-video :8383 │ │  │   │
│   │                         │ └─────────────────┘ │  │   │
│   │                         └─────────┬───────────┘  │   │
│   │                                   │              │   │
│   │  ┌────────────────────────────────▼───────────┐  │   │
│   │  │ 共享挂载目录  ~/heygem_data/face2face       │  │   │
│   │  │ 宿主路径  ⇄  容器内 /code/data              │  │   │
│   │  └────────────────────────────────────────────┘  │   │
│   └──────────────────────────────────────────────────┘   │
│                                                          │
│   浏览器访问 http://localhost:7860                        │
└──────────────────────────────────────────────────────────┘
```

### 2.2 ★核心概念：路径映射★

**这是整个 MVP 最容易踩的坑，必须彻底理解。**

`8383/easy/submit` 的参数 `audio_url` / `video_url` **不是文件上传，而是容器内路径**。所以：

```
Gradio 拿到上传文件
   → 写入宿主机目录  ~/heygem_data/face2face/xxx.wav
   → 该目录被 docker-compose 挂载为容器内 /code/data
   → API 传 "/code/data/xxx.wav"
   → 容器内能读到 ✓
```

**反向同理**：合成结果写在容器内 `/code/data/temp/{code}-r.mp4`，对应宿主机 `~/heygem_data/face2face/temp/{code}-r.mp4`，Gradio 从宿主路径读取并返回给用户。

```
宿主机(宿主)                          容器内
~/heygem_data/face2face/     ⇄      /code/data/
├── input_video.mp4          ⇄      /code/data/input_video.mp4
├── tts_output.wav           ⇄      /code/data/tts_output.wav
└── temp/{code}-r.mp4        ⇄      /code/data/temp/{code}-r.mp4
```

> ⚠️ **待 P0 确认**：`docker-compose-lite.yml` 中的实际挂载点。不同版本可能不同（有资料提到 `/home/ubuntu/heygem_data/face2face`）。**必须以你本地 compose 文件里的 `volumes:` 定义为准**，在 [3.2](#32-确认挂载点) 中确认。

### 2.3 数据流

```
① 用户上传视频 ──→ ffmpeg 标准化 ──→ 写入共享目录
                   H.264 / yuv420p / 25fps

② 用户上传音频 ──→ ffmpeg 转 WAV ──→ ASR 转写参考文本
                                      ↓
                              GPT-SoVITS 零样本试听
                                      ↓
                                  确认音色 OK

③ 讲稿 ──→ 分句 ──→ 逐句 GPT-SoVITS 合成 ──→ 拼接
                                              ↓
                                    统一采样率 WAV
                                              ↓
                                      写入共享目录

④ POST /easy/submit {audio_url, video_url, code}
                                              ↓
                     轮询 /easy/query?code=xxx （进度回调给 Gradio）
                                              ↓
                        读取 temp/{code}-r.mp4
                                              ↓
                                      输出给用户
```

---

## 3. P0 手测（先验证，再开发）

> **不要跳过这一章去写代码。** 这半天的验证能避免几天的返工。

### 3.1 环境前提检查

```bash
# ── Windows 侧（PowerShell，管理员）──
wsl --list --verbose                    # 确认 VERSION = 2
wsl --update
nvidia-smi                              # 确认能看到 4090

# ── WSL2 内 ──
nvidia-smi                              # 应同样能看到 4090
free -h                                 # ★ 记录可用内存
df -h                                   # ★ 记录可用磁盘

# GPU 透传验证（关键）
docker run --rm --gpus all nvidia/cuda:11.6.2-base-ubuntu20.04 nvidia-smi
```

**前提要求：**

| 项 | 要求 | 不满足时 |
|---|---|---|
| 磁盘可用 | **≥ 100GB**（镜像 + 模型 + 素材 + 中间产物） | 清理或在 `.wslconfig` 迁移镜像目录 |
| 内存可用 | **≥ 32GB**（WSL 默认用物理内存 50%） | 在 `.wslconfig` 调 `memory` / `swap` |
| GPU 透传 | `docker run --gpus all` 成功 | 见 [6.2](#62-gpu-透传失败) |

**`.wslconfig` 参考**（`C:\Users\<用户名>\.wslconfig`）：
```ini
[wsl2]
memory=32GB
swap=32GB
processors=12
# 若磁盘紧张，把镜像与 WSL 虚拟磁盘迁到 E 盘
```

### 3.2 确认挂载点

```bash
# 找到 lite 版 compose 文件
cd ~/Duix-Avatar/deploy       # 或你的实际克隆路径
ls docker-compose*.yml

# ★ 关键：查看挂载定义
grep -A 5 "volumes:" docker-compose-lite.yml
```

**记录下 `宿主路径:容器路径` 的映射关系**，后续所有路径处理都依赖它。本方案假设为：
```
<宿主>/heygem_data/face2face  ⇄  /code/data
```
**若实际不同，请替换本文档中所有 `/code/data`。**

### 3.3 启动 lite 版

```bash
cd ~/Duix-Avatar/deploy
docker-compose -f docker-compose-lite.yml up -d

# 等待容器就绪（首次拉镜像可能较久）
docker ps                       # 应看到 duix-avatar-gen-video 为 Up

# 健康检查
curl http://localhost:8383/health
```

### 3.4 ★核心验证：纯 API 能否出片★

```bash
# 准备测试素材：一段 10 秒以上正面讲话视频 + 一段 5 秒以上音频
# 放入共享目录（注意用你 3.2 确认的宿主路径）
cp /path/to/test_video.mp4 ~/heygem_data/face2face/
cp /path/to/test_audio.wav ~/heygem_data/face2face/

# 提交任务
curl -X POST http://127.0.0.1:8383/easy/submit \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "/code/data/test_audio.wav",
    "video_url": "/code/data/test_video.mp4",
    "code": "p0test001",
    "chaofen": 0,
    "watermark_switch": 0,
    "pn": 1
  }'

# 期望返回：{"status":"accepted","code":"p0test001",...} 或 {"success":true,...}

# 轮询进度
curl "http://127.0.0.1:8383/easy/query?code=p0test001"

# 成功后检查产物（宿主路径）
ls -la ~/heygem_data/face2face/temp/p0test001-r.mp4
```

**判定标准：**

| 结果 | 含义 | 下一步 |
|---|---|---|
| ✅ 返回 accepted 且能查到进度、最终生成 mp4 | **方案成立** | 进入第 4 章开发 |
| ⚠️ 返回参数错误 | 参数名/格式不对 | 用浏览器打开 `http://127.0.0.1:8383/docs` 看实际 OpenAPI 定义 |
| ⚠️ 提示文件不存在 | 挂载点判断错误 | 回到 3.2 重新确认映射 |
| ❌ 提示需要先训练模型 / 需要 model_id | **1.2 的推论错误** | 需增加训练步骤，回到本文档重新设计 |

> 💡 **提示**：`8383` 服务通常自带 Swagger 文档。直接访问 `http://127.0.0.1:8383/docs` 可以看到**最准确的参数定义**，比任何社区文档都可靠。**优先看这个。**

### 3.5 验证输出质量

用播放器打开 `p0test001-r.mp4`，评估：
- 口型是否与音频同步？
- 画面中人物是否就是输入视频里的人？
- 有无明显破绽？

**同时记录关键参数**（后续要用）：

| 参数 | 值 | 说明 |
|---|---|---|
| 测试音频采样率 | ______ | HeyGem 接受的格式 |
| 测试音频时长 | ______ 秒 | |
| 处理耗时 | ______ 秒 | 用于估算 |
| 峰值显存 | ______ GB | 用 `nvidia-smi` 观察 |

### 3.6 GPT-SoVITS 独立验证

```bash
# 启动 api_v2
cd ~/GPT-SoVITS
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml

# 测一次零样本合成
curl "http://127.0.0.1:9880/tts?text=你好，这是一段测试语音。&text_lang=zh&ref_audio_path=/path/to/ref.wav&prompt_lang=zh&prompt_text=参考音频的文字内容&text_split_method=cut5&media_type=wav" \
  --output test_tts.wav
```

**记录：**

| 参数 | 值 |
|---|---|
| 输出采样率 | ______ Hz（GPT-SoVITS 默认 32k/48k） |
| 单句合成耗时 | ______ 秒 |
| 显存占用 | ______ GB |
| 音色相似度主观评分 | ______ /5 |

> ⚠️ **显存冲突检查**：GPT-SoVITS 和 HeyGem 容器同时跑时，24GB 是否够？若不够，改为串行（先合成音频，再关掉 GPT-SoVITS 跑视频）。GPT-SoVITS 可在 `tts_infer.yaml` 里设 `is_half: true` 省显存。

---

## 4. 项目结构

```
~/speech_video_gen/
├── app.py                      # Gradio 入口
├── cli.py                      # 命令行入口（不经过界面跑一遍，便于调试）
├── config.yaml                 # 配置（路径、端口、参数）
├── requirements.txt
│
├── core/
│   ├── config.py               # 配置加载
│   ├── logs.py                 # 日志配置
│   ├── media.py                # ffmpeg 封装 + PathMapper ★核心★
│   ├── asr.py                  # 参考音频转写（可选）
│   ├── heygem.py               # HeyGem 客户端 + Mock ★核心★
│   ├── pipeline.py             # 流程编排 ★核心★
│   └── tts/
│       ├── base.py             # TTSEngine 抽象接口
│       ├── gpt_sovits.py       # GPT-SoVITS 实现
│       └── mock.py             # 正弦波假引擎
│
├── scripts/preflight.py        # P0 手测自动化
│
├── workspace/                  # 本地工作目录
│   ├── temp/                   # 中间产物（按 run_id 分子目录）
│   ├── mock_shared/            # mock 模式下的"共享目录"
│   └── outputs/                # 成片
│
└── logs/
```

**`requirements.txt`**
```
gradio>=4.4
requests
PyYAML
# 可选：funasr（参考音频转写，装不上则退化为 ref_free 模式）
```

**相对原方案的三处调整：**

| 调整 | 原因 |
|---|---|
| 不引入 `pysbd` / `pydub` | 分句用内置正则（中文本来就是按标点切）；拼接用 ffmpeg 的 `apad` + `concat` 滤镜，各段采样率不一致时也不会像 concat demuxer 那样直接报错 |
| 去掉 `heygem_tts.py` | `18180`（TTS）不在 lite 部署里，要用得拉完整版 70GB 镜像；而它的接口语义尚未验证。留着是纯投机代码，等真需要再加 |
| 新增 `MockHeyGemClient` | 没有 Docker 也能验证编排、路径映射、进度回调。它只覆盖 `health`/`submit`/`query` 三个 HTTP 方法，轮询逻辑复用真实实现，接口语义一致 |

---

## 5. 核心模块设计

### 5.1 配置（config.yaml）

```yaml
paths:
  # ★ 必须与 docker-compose-lite.yml 的 volumes 定义一致 ★
  heygem_host_dir: "/home/<user>/heygem_data/face2face"   # 宿主路径
  heygem_container_dir: "/code/data"                       # 容器内路径
  workspace: "/home/<user>/speech_video_gen/workspace"

services:
  heygem_base: "http://127.0.0.1:8383"
  gpt_sovits_base: "http://127.0.0.1:9880"

tts:
  engine: "gpt_sovits"        # gpt_sovits | heygem_tts
  text_lang: "zh"
  prompt_lang: "zh"
  text_split_method: "cut5"
  speed_factor: 1.0
  # 句间停顿（毫秒）
  pause_comma: 200
  pause_period: 400
  pause_paragraph: 800

audio:
  # HeyGem 输入音频的统一格式（P0 后按实测调整）
  target_sample_rate: 16000
  target_channels: 1
  target_format: "wav"

video:
  # HeyGem 输入视频的统一格式
  target_fps: 25
  target_codec: "libx264"
  target_pix_fmt: "yuv420p"

timeouts:
  heygem_submit: 30           # 提交请求超时
  heygem_task: 3600           # 任务总超时（秒）
  poll_interval: 3            # 轮询间隔（秒）
```

### 5.2 路径映射工具（最容易出错的地方，单独封装）

```python
# core/media.py 片段
class PathMapper:
    """
    宿主路径 ⇄ 容器路径 双向转换。
    所有涉及 HeyGem API 的路径都必须经过这里，禁止裸传路径。
    """
    def __init__(self, host_dir: str, container_dir: str):
        self.host_dir = Path(host_dir).resolve()
        self.container_dir = container_dir.rstrip("/")

    def to_container(self, host_path: Path) -> str:
        """宿主路径 → 容器内路径"""
        host_path = Path(host_path).resolve()
        try:
            rel = host_path.relative_to(self.host_dir)
        except ValueError:
            raise ValueError(
                f"路径 {host_path} 不在共享目录 {self.host_dir} 下。"
                f"所有要传给 HeyGem 的文件必须先复制到共享目录。"
            )
        return f"{self.container_dir}/{rel.as_posix()}"

    def to_host(self, container_path: str) -> Path:
        """容器内路径 → 宿主路径"""
        rel = container_path.replace(self.container_dir, "").lstrip("/")
        return self.host_dir / rel
```

### 5.3 HeyGem 客户端

```python
# core/heygem.py
import uuid, time, requests
from pathlib import Path
from dataclasses import dataclass

@dataclass
class HeyGemResult:
    code: str
    output_host_path: Path
    elapsed: float

class HeyGemClient:
    """
    Duix.Avatar (lite) 视频合成客户端。
    文档：http://127.0.0.1:8383/docs  ← P0 时以此为准
    """

    def __init__(self, base_url: str, mapper, timeout_task=3600, poll_interval=3):
        self.base = base_url.rstrip("/")
        self.mapper = mapper
        self.timeout_task = timeout_task
        self.poll_interval = poll_interval

    def health(self) -> bool:
        try:
            return requests.get(f"{self.base}/health", timeout=5).status_code == 200
        except requests.RequestException:
            return False

    def submit(self, audio_host: Path, video_host: Path, code: str = None) -> str:
        """提交合成任务。audio/video 必须是宿主路径，内部自动转容器路径。"""
        code = code or uuid.uuid4().hex[:12]
        payload = {
            "audio_url": self.mapper.to_container(audio_host),
            "video_url": self.mapper.to_container(video_host),
            "code": code,
            "chaofen": 0,
            "watermark_switch": 0,
            "pn": 1,
        }
        r = requests.post(f"{self.base}/easy/submit", json=payload, timeout=30)
        r.raise_for_status()
        resp = r.json()
        # 兼容两种返回格式
        if not (resp.get("success") or resp.get("status") == "accepted"):
            raise RuntimeError(f"提交失败: {resp}")
        return resp.get("code", code)

    def query(self, code: str) -> dict:
        r = requests.get(f"{self.base}/easy/query", params={"code": code}, timeout=30)
        r.raise_for_status()
        return r.json()

    def poll(self, code: str):
        """轮询直到完成。逐次 yield TaskStatus，结束时 return 成片的宿主路径。

        写成生成器而不是 wait(on_progress=callback)，是因为上层（pipeline）
        本身就是生成器 —— 用 yield from 才能把进度直接吐给 Gradio。
        """
        start = time.monotonic()
        while True:
            if time.monotonic() - start > self.timeout_task:
                raise HeyGemError(f"任务 {code} 超过 {self.timeout_task}s 未完成")
            info = _unwrap(self.query(code))
            status, done, failed = _read_status(info)
            if done:
                out_container = info.get("result") or self.mapper.output_container_path(code)
                return self.mapper.to_host(str(out_container))
            if failed:
                raise HeyGemError(f"合成失败: {info}")
            yield TaskStatus(percent=_extract_percent(info), raw=info)
            time.sleep(self.poll_interval)
```

> ⚠️ **`query` 的返回结构未经验证**。P0 阶段务必用真实响应替换 `status` / `progress` / `result` 的解析逻辑。**不要照抄，要对齐实际返回。**

### 5.4 TTS 抽象接口

```python
# core/tts/base.py
from abc import ABC, abstractmethod
from pathlib import Path

class TTSEngine(ABC):
    """
    抽象出接口，便于在 GPT-SoVITS 与 HeyGem 自带 TTS 之间切换。
    先用哪个跑通都行，另一个作为兜底。
    """

    @abstractmethod
    def prepare_voice(self, ref_audio: Path, ref_text: str = None) -> dict:
        """准备音色。ref_text 为 None 时自动 ASR 转写。"""

    @abstractmethod
    def synthesize(self, text: str, voice: dict, out_path: Path) -> Path:
        """合成单句音频。"""
```

```python
# core/tts/gpt_sovits.py
import requests
from pathlib import Path
from .base import TTSEngine

class GPTSoVITSEngine(TTSEngine):
    def __init__(self, base_url: str, text_lang="zh", prompt_lang="zh",
                 split_method="cut5", speed=1.0):
        self.base = base_url.rstrip("/")
        self.text_lang = text_lang
        self.prompt_lang = prompt_lang
        self.split_method = split_method
        self.speed = speed

    def prepare_voice(self, ref_audio: Path, ref_text: str = None) -> dict:
        if not ref_text:
            # 用 ASR 自动转写参考音频
            ref_text = transcribe(ref_audio)
        return {"ref_audio_path": str(ref_audio.resolve()), "ref_text": ref_text}

    def synthesize(self, text: str, voice: dict, out_path: Path) -> Path:
        payload = {
            "text": text,
            "text_lang": self.text_lang,
            "ref_audio_path": voice["ref_audio_path"],
            "prompt_text": voice["ref_text"],
            "prompt_lang": self.prompt_lang,
            "text_split_method": self.split_method,
            "speed_factor": self.speed,
            "media_type": "wav",
            "streaming_mode": False,
        }
        r = requests.post(f"{self.base}/tts", json=payload, timeout=120)
        r.raise_for_status()
        out_path.write_bytes(r.content)
        return out_path
```

### 5.5 文本分句与音频拼接（核心流程）

```python
# core/pipeline.py 片段
import pysbd

def split_script(text: str, max_chars: int = 50) -> list[str]:
    """
    分句。两个约束：
      1. 语义完整（用标点切）
      2. 单句不超过 max_chars（TTS 长句韵律会漂移）
    """
    seg = pysbd.Segmenter(language="zh", clean=False)
    sentences = seg.segment(text)
    out = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(s) <= max_chars:
            out.append(s)
        else:
            # 超长句按逗号二次切分
            parts, cur = s.split("，"), ""
            for p in parts:
                if len(cur) + len(p) + 1 <= max_chars:
                    cur = f"{cur}，{p}" if cur else p
                else:
                    if cur: out.append(cur)
                    cur = p
            if cur: out.append(cur)
    return out


def synthesize_script(engine, script: str, voice: dict, work_dir: Path) -> Path:
    """
    讲稿 → 完整音频。
    关键：逐句合成再拼接，而不是整段一次性合成（避免韵律漂移）。
    """
    sentences = split_script(script)
    if not sentences:
        raise ValueError("讲稿为空")

    clips, total = [], len(sentences)
    for i, s in enumerate(sentences):
        p = work_dir / f"seg_{i:04d}.wav"
        engine.synthesize(s, voice, p)
        clips.append((p, pause_after(s)))     # 逗号 200ms / 句号 400ms
        yield_progress("tts", (i + 1) / total)

    return concat_with_pauses(clips, work_dir / "tts_full.wav")


def pause_after(sentence: str) -> int:
    """根据句尾标点决定停顿长度（毫秒）"""
    if sentence.rstrip().endswith(("。", "！", "？", ".", "!", "?")):
        return 400
    if sentence.rstrip().endswith(("，", ",", "；", ";")):
        return 200
    return 200
```

> **拼接后必须统一转码**为 HeyGem 要求的采样率（见 `config.yaml` 的 `audio.target_sample_rate`），因为 GPT-SoVITS 输出通常是 32k/48k。

### 5.6 完整流程编排

```python
# core/pipeline.py
def run_pipeline(video_path, audio_ref_path, script, cfg, on_progress) -> Path:
    """端到端流程。每个阶段失败都要给出明确错误。"""

    # ── 0. 前置检查 ──
    on_progress("检查服务状态", 0.02)
    if not heygem.health():
        raise RuntimeError("HeyGem 服务未就绪。请确认容器已启动："
                           "docker-compose -f docker-compose-lite.yml up -d")

    # ── 1. 视频预处理 ──
    on_progress("处理视频素材", 0.05)
    video_std = media.standardize_video(video_path, cfg.video)
    video_shared = media.copy_to_shared(video_std)      # ★ 复制到共享目录
    if media.duration(video_shared) < 8:
        raise ValueError("视频素材过短，建议至少 10 秒")

    # ── 2. 音色准备 ──
    on_progress("准备音色", 0.10)
    audio_std = media.standardize_audio(audio_ref_path, cfg.audio)
    ref_text = asr.transcribe(audio_std)                # 自动转写参考文本
    voice = tts.prepare_voice(audio_std, ref_text)

    # ── 3. 合成语音 ──
    on_progress("合成语音", 0.15)
    tts_audio = synthesize_script(tts, script, voice, work_dir)
    tts_audio = media.conform_audio(tts_audio, cfg.audio)   # ★ 统一采样率
    tts_shared = media.copy_to_shared(tts_audio)            # ★ 复制到共享目录

    # ── 4. 视频合成 ──
    on_progress("提交合成任务", 0.50)
    code = heygem.submit(tts_shared, video_shared)
    poll = heygem.poll(code)
    while True:
        try:
            status = next(poll)
        except StopIteration as stop:
            out = stop.value          # 成片的宿主路径
            break
        on_progress(f"合成中 {status.percent:.0f}%", 0.5 + status.percent * 0.0047)

    # ── 5. 拷贝到输出目录 ──
    on_progress("整理产物", 0.98)
    final = cfg.workspace / "outputs" / f"{code}.mp4"
    shutil.copy(out, final)
    on_progress("完成", 1.0)
    return final
```

### 5.7 Gradio UI（约 100 行）

```python
# app.py
import gradio as gr

with gr.Blocks(title="数字人口播视频生成") as demo:
    gr.Markdown("# 数字人口播视频生成\n上传素材 → 输入讲稿 → 生成口播视频")

    with gr.Row():
        with gr.Column():
            video_in = gr.Video(label="① 正面演讲视频（≥10秒）", sources=["upload"])
            audio_in = gr.Audio(label="② 音频素材（提取音色，≥5秒）", type="filepath")
            script_in = gr.Textbox(label="③ 讲稿", lines=10,
                                   placeholder="在此粘贴讲稿…")
            run_btn = gr.Button("生成口播视频", variant="primary")

        with gr.Column():
            video_out = gr.Video(label="成片")
            log_out = gr.Textbox(label="进度", lines=6, interactive=False)

    # 参考文本（可选手填，留空则自动 ASR）
    ref_text_in = gr.Textbox(label="参考音频文本（留空自动识别）")

    run_btn.click(
        fn=run_pipeline_gradio,                    # 内部用 yield 推进度
        inputs=[video_in, audio_in, script_in, ref_text_in],
        outputs=[video_out, log_out],
    )

demo.queue().launch(server_name="0.0.0.0", server_port=7860)
```

**Gradio 用生成器函数推进度：**
```python
def run_pipeline_gradio(video, audio, script, ref_text):
    logs = []
    def on_progress(msg, pct):
        logs.append(f"[{pct*100:5.1f}%] {msg}")
    try:
        for msg, pct in ...:          # pipeline 内改为 yield
            logs.append(f"[{pct*100:5.1f}%] {msg}")
            yield None, "\n".join(logs[-8:])
        yield str(final_path), "✅ 完成"
    except Exception as e:
        yield None, f"❌ {type(e).__name__}: {e}"
```

---

## 6. 已知风险与规避

### 6.1 风险清单

| 风险 | 概率 | 影响 | 规避 |
|---|---|---|---|
| **纯 API 无法完成合成**（需 GUI 训练） | 中 | **致命** | P0 3.4 先验证，跑不通则改用官方客户端或换 ComfyUI_HeyGem |
| **挂载点判断错误** | **高** | 高 | P0 3.2 用 `grep volumes` 实际确认，不照抄文档 |
| **音频采样率不匹配** | **高** | 中 | 统一转码为 `config.yaml` 中配置的采样率；P0 3.5 记录实测值 |
| **显存冲突**（GPT-SoVITS + HeyGem 同时跑） | 中 | 中 | 串行执行；GPT-SoVITS 开 `is_half`；必要时视频合成前卸载 TTS |
| **内存不足导致容器崩溃** | 中 | 高 | WSL 内存 ≥32GB；配 32GB swap；监控 `docker stats` |
| **镜像拉取缓慢/失败** | 中 | 低 | 配镜像加速；耐心等待，勿中断 |
| **GPT-SoVITS 参考文本缺失** | 低 | 中 | 自动 ASR 兜底 + UI 提供手填入口 |
| **长讲稿导致单次任务超长** | 中 | 中 | MVP 限制讲稿 ≤500 字；超长拆多次 |

### 6.2 GPU 透传失败

```bash
docker info | grep -i nvidia          # 应含 nvidia runtime
```
若失败，在 **WSL2 内**（不是 Windows）重装 toolkit：
```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

> ⚠️ **NVIDIA 驱动只在 Windows 侧装**，WSL 内装驱动会冲突。

### 6.3 常见报错速查

| 报错 | 原因 | 解法 |
|---|---|---|
| `could not select device driver "nvidia"` | toolkit 未装/未配 runtime | 见 6.2 |
| `file not exists` | 路径映射错误 | 检查传入的是否为**容器内路径** |
| 容器反复重启 / Exit 139 | 内存不足 | 加 swap，调 `.wslconfig` |
| `CUDA out of memory` | 显存冲突 | 串行执行 |
| Gradio 打不开 | 端口未转发 | 用 `0.0.0.0` 绑定；WSL2 通常自动转发 localhost |

---

## 7. 实施计划

| 阶段 | 工作量 | 内容 | 出口标准 |
|---|---|---|---|
| **P0 手测** | **0.5 天** | 第 3 章全部步骤 | `p0test001-r.mp4` 生成成功 |
| **P1 骨架** | 1 天 | 项目结构、配置、`media.py`、`heygem.py`；用命令行跑通 API 调用 | 脚本能出片 |
| **P2 TTS 集成** | 1 天 | `tts/` 模块、分句、拼接、ASR 参考文本 | 命令行能出带本人音色的片 |
| **P3 Gradio** | 1 天 | UI + 进度反馈 + 错误处理 | 浏览器可完成全流程 |
| **P4 打磨** | 1 天 | 参数调优、边界处理、日志、使用说明 | 连续 3 次成功生成 |

**总计约 4–5 个工作日。**

### 7.1 验收标准

```
□ 浏览器打开 localhost:7860 能看到界面
□ 上传 30 秒正面视频 + 10 秒音频 + 200 字讲稿
□ 点击生成后能看到实时进度
□ 5 分钟内输出成片
□ 成片口型与音频同步，无明显错位
□ 音色与参考音频相似（主观 ≥3/5）
□ 断网状态下全程可运行
```

---

## 8. 后续演进（明确不在 MVP 内）

按优先级排序，每项都可独立追加：

| 优先级 | 功能 | 依赖 | 预估 |
|---|---|---|---|
| P1 | **字幕**（讲稿已知，用强制对齐做时间戳） | Qwen3-ForcedAligner | 2 天 |
| P1 | **BGM / 片头片尾 / 水印** | FFmpeg | 1 天 |
| P2 | **长视频支持**（分段 + 拼接 + 一致性） | 见技术方案 7.1 | 1 周 |
| P2 | **素材库与极速档**（检索拼接） | 技术方案 4.5.4 | 2 周 |
| P2 | **PPT 讲课模式** | python-pptx + LibreOffice | 2 周 |
| P3 | **人脸修复 / 超分** | GPEN / Real-ESRGAN | 3 天 |
| P3 | **GPT-SoVITS 微调**（音色保真提升） | 训练流程 | 3 天 |
| P3 | **任务队列与断点续跑** | SQLite | 1 周 |

---

## 9. 待确认事项（P0 已全部实测确认）

| # | 事项 | 实测结果 |
|---|---|---|
| 1 | 磁盘可用空间是否 ≥100GB | ✅ 充足（WSL 虚拟磁盘 1TB，两张镜像解包后约 70GB） |
| 2 | 物理内存大小 | **61.6 GB**，`.wslconfig` 分配 48GB（WSL 默认只给 50%，需显式配置） |
| 3 | `docker-compose-lite.yml` 的实际挂载点 ★ | **`d:/duix_avatar_data/face2face` ⇄ `/code/data`**。文档原假设的 `~/heygem_data/face2face` 是**错的**；且 `d:/...` 是 Windows 版 Docker Desktop 的路径写法，WSL 原生 Docker 下须改写为 `/home/xenon/duix_avatar_data/face2face` |
| 4 | `8383` 的 API 参数名与返回结构 ★ | **不是 FastAPI，所以 `/docs` 和 `/health` 都不存在**；只有 `/easy/submit`(POST) 与 `/easy/query`(GET)。详见 [9.1](#91-heygem-api-契约实测) |
| 5 | HeyGem 接受的音频采样率 | ✅ **16000 Hz 单声道**——成片音轨实测即此格式，`config.yaml` 的 `target_sample_rate: 16000` 正确 |
| 6 | GPT-SoVITS 输出采样率 | ✅ **32000 Hz 单声道**。与 #5 不一致，所以「合成后必须重采样到 16kHz」这一步是必需的，不是可选优化 |
| 7 | 两者同时运行的显存是否够 | ✅ 够。HeyGem 稳态约 10GB，GPT-SoVITS 加载后合计约 7GB 起，24GB 有余量 |

**结论：1.3 节标记为"致命"的风险（纯 API 能否完成合成）已证伪——可以，且不需要官方 GUI 的任何预处理。**

### 9.1 HeyGem API 契约（实测）

`8383` 是 **Flask** 应用。响应统一为 `{code, success, msg, data}`，`code` 是数字码：

| code | 含义 | success |
|---|---|---|
| 10000 | 成功 | true |
| 10001 | 忙碌中 | **true** ⚠️ |
| 10002 | 参数异常 | false |
| 10004 | 任务不存在 | **true** ⚠️ |
| 9999 | 系统异常 | false |

> ⚠️ 10001 / 10004 的 `success` 同样是 `true`，**判定必须看 `code` 而非 `success`**。

`/easy/query` 的业务数据嵌在 `data` 里，`status` 枚举：`run=1` / `success=2` / `error=3`。

三条容易踩的行为：

1. **终态即回收**——任务进入 success/error 后服务端**立即从字典删除**，再查返回 10004。轮询必须在首次拿到终态时收手。
2. **`data.result` 不可信**——成功时它返回的是中间产物（如 `temp/{code}/result.avi`）或一个不存在的根路径，**不是成片**。成片在约定的 `temp/{code}-r.mp4`。
3. `/code/data/temp/{code}/` 是服务端的内部工作目录。

### 9.2 环境级坑（不在代码里，但会反复踩）

| 现象 | 根因 | 解法 |
|---|---|---|
| 容器内**任何** exec 都报 `input/output error` | Docker 的 `containerd-snapshotter` 解包这张镜像时，把 `/usr/lib/x86_64-linux-gnu` 下 **1596 个文件全解成 0 字节**（blob 的 digest 校验是通过的，损坏发生在解包环节） | `/etc/docker/daemon.json` 设 `"features": {"containerd-snapshotter": false}`，切回经典 overlay2 |
| GPT-SoVITS `/tts` 永远返回 `{"message":"tts failed","Exception":"Ran out of input"}`，换参考音频/分句方式/文本都一样 | 镜像里 **16 个 numba JIT 缓存（`.nbc`）全是 0 字节**，librosa 导入时 `@guvectorize` 触发 numba 读缓存，`pickle.loads` 读到空流抛 EOFError。**错误发生在导入阶段，与请求参数无关** | 启动前 `find /root/conda -name "*.nbc" -delete`（缓存可再生） |
| 拼接音频产出无限增长的巨型文件 | ffmpeg `apad` 的 `pad_dur` **默认值就是 0，传 0 等于没限长**，会无限补静音，`concat` 串成无限流 | 停顿为 0 的片段改用 `anull` 透传，不挂 `apad` |
| 镜像默认 Cmd 会 `rm -rf` 工作目录里的模型目录 | 该 Cmd 是给 compose 挂载宿主机仓库设计的，会把宿主机的 `pretrained_models` 等四个目录删掉再软链 | 不要挂载仓库目录；若用自定义命令启动，需自己复刻软链步骤 |

---

*文档结束。P0 已通过：完整链路（讲稿 → 克隆音色 → 口型驱动 → 出片）实测跑通。*
