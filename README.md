# wechat-fetcher

> 微信公众号文章抓取器 — 通过 Playwright 无头浏览器模拟真实用户访问，提取标题、作者、正文并转为结构化 Markdown。支持代理轮换、图片下载、自动摘要、批量模式。

## 特性

- ✅ **真实浏览器模拟** — 基于 Playwright Chromium，绕过微信反爬检测
- ✅ **结构化输出** — Markdown / JSON 双格式，含元信息、正文、图片列表
- ✅ **代理轮换** — 支持多代理自动切换，防 IP 被封
- ✅ **图片下载** — 自动下载正文图片到本地，替换为相对路径
- ✅ **AI 自动摘要** — 调用 LLM（Kimi / DeepSeek / SCNet）生成文章摘要和关键词
- ✅ **批量模式** — 文件内每行一个 URL，一键抓取多篇文章
- ✅ **存档闭环** — 自动评估信息价值，写入 memory 或仅存档，无需人工确认
- ✅ **跨环境兼容** — 支持 `OPENCLAW_WORKSPACE` 环境变量覆盖路径

## 安装

### 方式一：通过 OpenClaw 安装（推荐）

```bash
skills add https://github.com/htten/wechat-fetcher
```

安装后执行依赖初始化：

```bash
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/setup.sh
```

### 方式二：手动克隆

```bash
git clone https://github.com/htten/wechat-fetcher ~/.openclaw/workspace/skills/wechat-fetcher
cd ~/.openclaw/workspace/skills/wechat-fetcher
./scripts/setup.sh
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENCLAW_WORKSPACE` | 覆盖 workspace 路径 | `~/.openclaw/workspace` |

## 前置依赖

- Python 3.10+
- Playwright + Chromium
- requests

`setup.sh` 会自动检查并安装所有依赖。

## 快速开始

### 抓取单篇文章

```bash
# 打印到终端
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py \
  "https://mp.weixin.qq.com/s/xxx"

# 保存到文件
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py \
  "https://mp.weixin.qq.com/s/xxx" -o article.md
```

### 存档模式（推荐）

```bash
# 自动保存原文到 articles/ + 生成闭环报告
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py \
  "https://mp.weixin.qq.com/s/xxx" --archive

# 存档 + AI 摘要
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py \
  "https://mp.weixin.qq.com/s/xxx" --archive --summarize
```

### 批量抓取

```bash
# urls.txt 每行一个 URL
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py \
  --batch urls.txt -o ./articles/ \
  --proxy auto --download-images --summarize
```

### 代理配置

编辑 `config/proxies.txt`，每行一个代理：

```
http://127.0.0.1:7890
socks5://user:pass@proxy.example.com:1080
```

使用时添加 `--proxy auto` 参数：

```bash
python3 scripts/fetch.py "URL" --proxy auto
```

## 命令行参数

```
 positional arguments:
  url                   单篇文章 URL

  optional arguments:
  -h, --help            显示帮助信息
  -o OUTPUT, --output OUTPUT
                        输出文件路径
  --timeout TIMEOUT     页面加载超时（秒，默认 20）
  --json                输出 JSON 格式
  --no-images           不提取图片
  --proxy PROXY         代理模式：auto 或指定代理地址
  --download-images     下载图片到本地
  --summarize           生成 AI 摘要（需配置 API key）
  --archive             存档模式：保存到 articles/ + 生成闭环报告
  --batch BATCH         批量模式：文件内每行一个 URL
  -v, --verbose         详细日志
```

## 输出格式

### Markdown（默认）

```markdown
# 文章标题

> **摘要**: ...
> **关键词**: `关键词1` `关键词2` ...

---

**公众号**: xxx
**发布时间**: 2026-05-20

---

正文内容...
```

### JSON（`--json`）

```json
{
  "title": "文章标题",
  "author": "公众号名称",
  "publish_time": "2026-05-20",
  "summary": "文章摘要...",
  "keywords": ["关键词1", "关键词2"],
  "content": "正文 Markdown",
  "images": ["https://mmbiz.qpic.cn/..."],
  "url": "https://mp.weixin.qq.com/s/..."
}
```

## AI 摘要配置

摘要功能调用 LLM API，支持多 provider（SCNet / DeepSeek / Moonshot / Kimi）。

### 方式 1：专用本地配置（推荐）

创建 `config/local.json`：

```json
{
  "scnet": {
    "apiKey": "your-api-key",
    "baseUrl": "https://api.scnet.cn/api/llm/v1",
    "model": "Qwen3-235B-A22B"
  }
}
```

### 方式 2：环境变量

```bash
export KIMI_API_KEY="sk-..."
export DEEPSEEK_API_KEY="sk-..."
export SCNET_API_KEY="..."
```

### 方式 3：OpenClaw 全局配置

在 `~/.openclaw/openclaw.json` 中配置：

```json
{
  "skills": {
    "wechat-fetcher": {
      "kimiApiKey": "sk-..."
    }
  }
}
```

## 目录结构

```
wechat-fetcher/
├── SKILL.md              # OpenClaw 技能定义
├── README.md             # 本文件
├── scripts/
│   ├── fetch.py          # 主程序
│   └── setup.sh          # 依赖安装脚本
├── config/
│   ├── proxies.txt       # 代理列表（可选）
│   └── local.json        # 本地 API 配置（可选）
└── .gitignore
```

## 注意事项

- 微信公众号内容是 JS 动态渲染，必须使用浏览器环境（Playwright）
- 服务器 IP 可能被微信标记，建议本地执行或使用 `--proxy`
- 遇到验证码页面会返回错误提示
- 图片下载单张超时 30 秒，批量模式下按文章标题前缀命名避免冲突
- 摘要生成依赖外部 LLM API，服务不稳定时会跳过并记录警告

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.3.0 | 2026-05-31 | 路径动态化（支持 `OPENCLAW_WORKSPACE`）、添加 `setup.sh` |
| 1.2.0 | 2026-05-20 | 新增代理轮换、图片下载、LLM 自动摘要（多 provider） |
| 1.1.0 | 2026-04-15 | 新增批量模式、JSON 输出、日志系统 |
| 1.0.0 | 2026-04-07 | 初始版本，基础抓取 + Markdown 转换 |

## 贡献

欢迎 Issue 和 PR。如果你使用此技能遇到问题，或希望支持新的 LLM provider，请随时反馈。

## 许可

MIT License © 2026 HTTEN / 小码

---

> 如果此技能对你有帮助，欢迎 Star ⭐ 支持！
