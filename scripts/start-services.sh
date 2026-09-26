#!/usr/bin/env bash
# 启动链路依赖的两个后端服务：Duix.Avatar(8383) 与 GPT-SoVITS(9880)。
#
# 两个坑写在这里，不写下来重建容器时会重踩：
#
#   1. GPT-SoVITS 镜像里 16 个 numba JIT 编译缓存（.nbc）全是 0 字节。
#      librosa 导入时 @guvectorize 会触发 numba 读缓存，pickle 读到空流抛
#      EOFError: Ran out of input。因为发生在**导入阶段**，跟请求参数毫无关系，
#      服务端又只回一句 "tts failed"，所以换参考音频/分句方式/文本全都没用。
#      缓存可再生，启动前删掉即可。
#
#   2. GPT-SoVITS 镜像的默认 Cmd 会先 rm -rf 工作目录里的四个模型目录，
#      再软链到 /workspace/models/。用自定义命令启动时必须自己复刻这一步，
#      否则 G2PWModel 等路径是空的。同理**不要挂载宿主机仓库目录**，
#      否则那个 rm -rf 会删掉宿主机上的对应目录。
#
# 用法：bash scripts/start-services.sh [--only duix|tts]
set -uo pipefail

DUIX_DIR="${DUIX_DIR:-$HOME/Duix-Avatar/deploy}"
COMPOSE_FILE="$DUIX_DIR/docker-compose-lite.yml"
DATA_DIR="${DATA_DIR:-$HOME/duix_avatar_data}"
PROJECT_DIR="${PROJECT_DIR:-$HOME/speech_video_gen}"

TTS_IMAGE=xxxxrt666/gpt-sovits:latest-cu126-lite
TTS_NAME=gpt-sovits
TTS_PORT=9880

ONLY=""
[ "${1:-}" = "--only" ] && ONLY="${2:-}"

wait_for() {
  local name="$1" url="$2" probe="$3"
  echo "   等待 $name 就绪…"
  for i in $(seq 1 40); do
    if [ "$probe" = "docs" ]; then
      code=$(curl -s -o /dev/null -m 5 -w "%{http_code}" "$url" 2>/dev/null || echo 000)
      [ "$code" = "200" ] && { echo "   ✓ $name 就绪（第 $i 次）"; return 0; }
    else
      body=$(curl -s -m 5 "$url" 2>/dev/null || echo "")
      echo "$body" | grep -q '"code"' && { echo "   ✓ $name 就绪（第 $i 次）"; return 0; }
    fi
    sleep 10
  done
  echo "   ✗ $name 超时未就绪，看日志：docker logs $name"
  return 1
}

start_duix() {
  echo "── Duix.Avatar (:8383) ──"
  if [ ! -f "$COMPOSE_FILE" ]; then
    echo "   找不到 $COMPOSE_FILE，跳过（可用 DUIX_DIR 指定）"
    return 1
  fi
  # 挂载点必须与 config.yaml 的 heygem_host_dir 一致。compose 原文写的是
  # d:/duix_avatar_data/face2face —— 那是 Windows 版 Docker Desktop 的路径写法，
  # 换成 WSL 原生 Docker 后需先改成绝对路径，否则会被当成 Linux 相对路径解析错。
  docker compose --project-directory "$DUIX_DIR" -f "$COMPOSE_FILE" up -d 2>&1 | tail -3 | sed 's/^/   /'
  wait_for duix-avatar-gen-video "http://127.0.0.1:8383/easy/query?code=__ping__" api
}

start_tts() {
  echo "── GPT-SoVITS (:$TTS_PORT) ──"
  docker rm -f "$TTS_NAME" >/dev/null 2>&1

  # -v 以「相同绝对路径」挂载：调用方传的是宿主机绝对路径（ref_audio_path），
  # 而它必须是容器内可读路径。同路径挂载后代码侧的假设才成立，不必做路径映射。
  docker run -d --name "$TTS_NAME" \
    --gpus all \
    -p "$TTS_PORT:$TTS_PORT" \
    --shm-size 16g \
    -e is_half=true \
    --restart unless-stopped \
    -v "$DATA_DIR:$DATA_DIR" \
    -v "$PROJECT_DIR:$PROJECT_DIR" \
    "$TTS_IMAGE" \
    /bin/bash -c '
      find /root/conda -name "*.nbc" -delete 2>/dev/null
      find /root/conda -name "*.nbi" -delete 2>/dev/null
      rm -rf /workspace/GPT-SoVITS/GPT_SoVITS/pretrained_models
      rm -rf /workspace/GPT-SoVITS/GPT_SoVITS/text/G2PWModel
      rm -rf /workspace/GPT-SoVITS/tools/asr/models
      rm -rf /workspace/GPT-SoVITS/tools/uvr5/uvr5_weights
      ln -s /workspace/models/pretrained_models /workspace/GPT-SoVITS/GPT_SoVITS/pretrained_models
      ln -s /workspace/models/G2PWModel /workspace/GPT-SoVITS/GPT_SoVITS/text/G2PWModel
      ln -s /workspace/models/asr_models /workspace/GPT-SoVITS/tools/asr/models
      ln -s /workspace/models/uvr5_weights /workspace/GPT-SoVITS/tools/uvr5/uvr5_weights
      exec python api_v2.py -a 0.0.0.0 -p '"$TTS_PORT"'
    ' >/dev/null 2>&1

  # 首次调用要重新编译 numba，比后续慢
  wait_for "$TTS_NAME" "http://127.0.0.1:$TTS_PORT/docs" docs
}

rc=0
case "$ONLY" in
  duix) start_duix || rc=1 ;;
  tts)  start_tts || rc=1 ;;
  *)    start_duix; start_tts || rc=1 ;;
esac

echo
echo "── 状态 ──"
docker ps --format "   {{.Names}}  {{.Status}}" | head -5
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader | sed 's/^/   GPU: /'
exit $rc
