# 232 局域网 Linux 部署（10.0.3.117）

2026-11-06 用户要求：把 StudyAgent 部署到 tripo-cli 所在的 Linux 机器，局域网所有电脑以 IP 加端口访问；AI 模型使用 tripo-cli 的密钥；补齐教材原卷、语音和课堂配图，并记录部署。原域名 https://study-agent.iepose.cn 的 Windows 正式实例保持不变，本部署是另一套独立实例和数据库。

## 访问入口

| 地址 | 证书 | 适用 |
| --- | --- | --- |
| https://tripo.gz4399.com:8300 | 公司 `*.gz4399.com` 公共证书 | 推荐。办公网电脑与连公司 Wi‑Fi 的手机，公司内网 DNS 解析到 10.0.3.117，无证书提示 |
| https://10.0.3.117:8300 | tripo 工作台内部 CA（SAN IP） | 直接 IP 访问；未安装 https://10.0.3.117:8300/ca.crt 时浏览器提示不安全 |

同一端口上的 http:// 请求被重定向到 https://。生产模式登录 Cookie 带 Secure，页面也使用仅在安全上下文可用的浏览器能力，因此不提供纯 HTTP 入口。

手机打不开的排查（2026-11-06）：入口 nginx 日志里没有任何来自手机的请求，说明请求没有到达服务器，而不是应用拒绝。服务器防火墙未开启；`tripo.gz4399.com` 只在公司内网 DNS 有记录，公网 DNS 没有。手机需连接能访问 10.0.3.x 的公司 Wi‑Fi（不能用移动数据或访客网络）。连上后使用域名入口，避免 IP 入口的证书拦截。

## 组成

- API（含内置 worker）`studyagent-api`、网页 `studyagent-web`：systemd，专用用户 `studyagent`，仅监听 127.0.0.1:14300/13300。
- 入口：`studyagent-edge` nginx 容器（host 网络）监听 :8300，两个 TLS 身份，证书文件只读复用 `/etc/tripo/tls`。
- 存储：SQLite `/data/studyagent/var`；全部程序、依赖与资料在 `/data/studyagent`（系统盘 97% 已用）。
- 文字模型：tripo-cli `/etc/tripo/aihub.key` 经 systemd 凭据交给 API；Codex CLI 0.164.0 走 AIHUB Responses，模型目录只列该密钥实际可用的 `gpt-5.1`（默认）与 `gpt-5.2`。
- 课堂配图：tripo-cli `/etc/tripo/image.key`，未修改的 Codex imagegen CLI 经 AIHUB `gpt-image-2`。
- 语音：AIHUB 的两把密钥都没有语音通道（`/v1/audio/speech` 404，MiniMax 透传“没有支持该模型的channel”），因此部署本地 Kokoro v1.1-zh（CPU，`studyagent-tts`，127.0.0.1:14500，OpenAI 兼容）。应用新增可选 `OPENAI_TTS_BASE_URL`/`OPENAI_TTS_API_KEY`，语音可单独指向该服务，文字模型仍用 AIHUB。
- 教材：307 册 ChinaTextbook 原卷从公开仓库下载，逐册大小和 SHA-256 与清单一致；提取文本用 PyMuPDF 1.26.6 重建，307/307 与清单 `extractedSha256` 一致（1.28.2 不一致，未采用）。

配置与脚本见 [lan-linux 部署套件](../../resources/deployment/lan-linux/README.md)。

## 验证

- 构建：提交 `af7f7c0b` 在服务器上 `npm ci` 与 `npm run build` 通过，Web buildId `1OGJ1JZg7r3qZfkEIav5U`；语音改动的配置包构建、API 类型检查与 `speech.service.test.ts` 9 项通过。切换前一致备份 SQLite 至 `/data/studyagent/backups/`，旧版本 `56caddec` 保留在 `releases/` 供回退。
- HTTPS：本机按公共信任链访问域名入口 200；IP 入口以内部 CA 校验 200；http 跳转 301；tripo 8443 与其他容器不受影响。
- 账户与模型：合成家长账号注册、Secure Cookie、模型目录、经 `gpt-5.1` 的真实小九回复均通过，随后经本人删除接口删除账号；管理员登录通过。
- 教材：管理员读取语文三上原卷（Range 206、`%PDF`）与第一课受校验正文 200。
- 配图：经 AIHUB `gpt-image-2` 真实生成 1536×1024 PNG；就绪探针 `ready`。
- 语音：本地服务 31/296/770 字分别约 0.9/6.7/14.2 秒，五种音色均可用，重启后约 8 秒恢复。经正式入口，合成成人账号打开社会学第一课并请求首句旁白，返回 `audio/mpeg`；课堂能力接口显示语音 `available`、配图 `ready`；账号随后删除。

## 未完成与边界

- 业务数据与 35 GB 题卷库没有迁移。它们只在承载 study-agent.iepose.cn 的 Windows 电脑上（本机与 Linux 都没有副本，经网页无法批量导出），需要在那台电脑上用 `scripts/operations/backup.cjs create` 制作备份并拷到服务器后，用 `sqlite-restore` 恢复。题卷库缺失时应用正常启动，题卷检索、组卷与导出不可用。
- 两套实例数据互不同步。课堂配图与语音未在正式页面里由真人逐项试用。
- 语音单次超过约 1000 字会超出应用 20 秒超时；应用按句或单元调用，正常讲解不受影响。
