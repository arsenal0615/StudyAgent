# 局域网 Linux 部署套件（10.0.3.117）

与 [232 部署记录](../../../doc/product/232-lan-linux-deployment.md) 配套。这里的文件与服务器实际运行的配置一致，密钥和生成的密码都不在仓库中。

| 文件 | 服务器位置 | 作用 |
| --- | --- | --- |
| `application.env.example` | `/etc/studyagent/application.env` | 运行配置（`JWT_SECRET` 安装时生成） |
| `start-api.sh` | `/data/studyagent/bin/start-api.sh` | 把 tripo-cli 的 AIHUB 密钥（systemd 凭据）交给 API 进程 |
| `studyagent-api.service`、`studyagent-web.service` | `/etc/systemd/system/` | API（含内置 worker）与 Next 网页 |
| `edge-nginx.conf`、`edge-compose.yaml` | `/data/studyagent/edge/` | `:8300` HTTPS 入口：IP 用内部 CA 证书，`tripo.gz4399.com` 用公司通配证书 |
| `codex-config.toml`、`build-model-catalog.py` | `/data/studyagent/codex-home/` | 小九/课程生成经 Codex CLI 调 AIHUB `gpt-5.1`、`gpt-5.2` |
| `imagegen-skill/scripts/` | `/data/studyagent/imagegen-skill/scripts/` | 课堂配图：未修改的 Codex imagegen CLI（`vendor/openai_image_gen.py`，Apache-2.0）经 AIHUB `gpt-image-2` |
| `tts/` | `/data/studyagent/tts/` | 本地中文语音 Kokoro v1.1-zh（OpenAI 兼容，`127.0.0.1:14500`） |
| `fetch-textbooks.py` | `/data/studyagent/bin/` | 按清单下载 307 册公开教材 PDF 并逐个核对 SHA-256 |

教材提取文本由 [`scripts/operations/restore-textbook-extracts.py`](../../../scripts/operations/restore-textbook-extracts.py) 用 PyMuPDF 1.26.6 重建，逐文件与清单 `extractedSha256` 一致才写入。

## 从零安装要点

全部放在 `/data/studyagent`（系统盘已 97%）。以 root 执行：

1. `useradd --system --home-dir /data/studyagent --shell /usr/sbin/nologin studyagent`；Node 24.22.2 解压到 `tools/node`，`npm install -g --prefix tools/codex @openai/codex@0.164.0`。
2. 源码按提交 `git archive` 解压到 `releases/<commit>`，`current` 指向它；以 `set -a; . /etc/studyagent/application.env` 后 `npm ci && npm run build`，再 `chown -R root:root`、只给 `apps/web/.next/cache` 写权限。
3. Python 3.12 由 uv 安装到 `tools/python`：`venv`（`scripts/processing/requirements.txt`）、`imagegen-venv`（`requirements-imagegen.txt`）、`extract-1.26.6`（`PyMuPDF==1.26.6`）。
4. `fetch-textbooks.py <源码> /data/studyagent/ChinaTextbook`，再用 `extract-1.26.6/bin/python -X utf8 scripts/operations/restore-textbook-extracts.py --textbook-root /data/studyagent/ChinaTextbook --asset-root /data/studyagent/assets`。
5. 按 `tts/setup.sh` 安装语音服务；安装 systemd 单元并 `enable --now studyagent-tts studyagent-api studyagent-web`；`docker compose -f edge/compose.yaml up -d`。
6. 管理员：以 `studyagent` 身份带环境运行 `STUDY_ADMIN_PASSWORD=… node apps/api/dist/apps/api/src/cli.js create-admin <账号>`。
