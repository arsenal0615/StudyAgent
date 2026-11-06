#!/usr/bin/env bash
# studyagent-tts: install log / reproducible setup (run as root on the TTS host).
# Local OpenAI-compatible Chinese TTS: Kokoro v1.1-zh via kokoro-onnx (CPU) + misaki[zh] G2P.
# Everything lives under /data/studyagent/tts (root disk is nearly full). No GPU is used.
set -euo pipefail

TTS=/data/studyagent/tts
UV=/data/studyagent/tools/uv-venv/bin/uv
export UV_PYTHON_INSTALL_DIR=/data/studyagent/tools/python   # CPython 3.12.16 already installed there
export UV_PYTHON_DOWNLOADS=never
export UV_CACHE_DIR=$TTS/build-cache/uv
export PIP_CACHE_DIR=$TTS/build-cache/pip
export TMPDIR=$TTS/build-cache/tmp

mkdir -p "$TTS"/{models,deploy-copy,build-cache/{uv,pip,tmp}} "$TTS"/cache/{tmp,hf,xdg}

# 1. Model files (sha256 recorded on 2026-11-06)
B=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1
cd "$TTS/models"
[ -f kokoro-v1.1-zh.onnx ] || curl -fL --retry 3 -o kokoro-v1.1-zh.onnx "$B/kokoro-v1.1-zh.onnx"
[ -f voices-v1.1-zh.bin ]  || curl -fL --retry 3 -o voices-v1.1-zh.bin  "$B/voices-v1.1-zh.bin"
[ -f config.json ]         || curl -fL --retry 3 -o config.json https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh/resolve/main/config.json
sha256sum -c - <<'EOF'
859f9ded9f53be16c24857cdab3254a45da53c3afd5ba6ef134c7de3f822e326  kokoro-v1.1-zh.onnx
14cb6186c99e4f6016871405f62046c5df863ae27465cbdc4ee08be7dd703acd  voices-v1.1-zh.bin
bc333efa5ce4ceff433c8c8e5d027a1eca0166001e4e4a62bea2d26ff7a46890  config.json
EOF

# 2. Python 3.12 venv + pinned packages (CPU onnxruntime only)
cd "$TTS"
[ -x venv/bin/python ] || "$UV" venv -p 3.12 venv
"$UV" pip install --python venv/bin/python --compile-bytecode \
  kokoro-onnx==0.6.1 onnxruntime==1.31.1 'misaki-fork[zh]==0.9.6' cn2an==0.5.24 soundfile==0.14.0 \
  numpy==2.5.4 espeakng-loader==0.2.4 phonemizer==3.6.0 jieba==0.42.1 pypinyin==0.55.0 \
  pypinyin-dict==0.9.0 ordered-set==4.1.0 addict==2.4.0 regex==2026.9.29 proces==0.1.7 \
  flatbuffers==25.12.19 protobuf==7.36.2 packaging==26.3 attrs==26.1.0 cffi==2.1.1 pycparser==3.12 \
  cloudpickle==3.1.2 dlinfo==2.0.0 joblib==1.6.0 typing-extensions==4.16.0 pip==26.3
# uv must not leave new links in /root/.local/bin (pre-existing: env env.fish python3.11 uv uvx)
ls -la /root/.local/bin

# 3. Server + permissions: code/models/venv root-owned (world-readable), runtime cache owned by studyagent
install -m 0644 -o root -g root "$TTS/deploy-copy/server.py" "$TTS/server.py"
chown -R root:root "$TTS/models" "$TTS/venv" "$TTS/deploy-copy"
chmod -R a+rX "$TTS/models" "$TTS/venv"
chown -R studyagent:studyagent "$TTS/cache"
chmod 0750 "$TTS/cache"

# 4. systemd unit
install -m 0644 -o root -g root "$TTS/deploy-copy/studyagent-tts.service" /etc/systemd/system/studyagent-tts.service
systemctl daemon-reload
systemctl enable --now studyagent-tts.service

# 5. Smoke test
for i in $(seq 1 60); do curl -fsS http://127.0.0.1:14500/health && break; sleep 1; done
curl -s -X POST http://127.0.0.1:14500/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"model":"tts-1","voice":"alloy","input":"我们先把问题看清楚，一步一步来，再用新的例子验证。","response_format":"mp3"}' \
  -o /tmp/x.mp3
ffprobe -v error -show_entries format=format_name,duration:stream=codec_name,channels,sample_rate -of compact /tmp/x.mp3
