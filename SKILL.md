---
name: wechat-fetcher
description: 抓取微信公众号文章，通过 Playwright 无头浏览器模拟真实用户访问，提取标题、作者、正文并转为 Markdown。支持代理轮换、图片下载、自动摘要。
metadata:
  author: 小码
  version: "1.3.0"
---

# wechat-fetcher

## 安装方式

### 方式一：通过 OpenClaw 安装（推荐）

```bash
skills add https://github.com/htten/wechat-fetcher
```

安装后执行依赖初始化：

```bash
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/setup.sh
```

### 方式二：手动复制

将本目录复制到目标 agent 的 workspace：

```bash
cp -r wechat-fetcher ~/.openclaw/workspace/skills/
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/setup.sh
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENCLAW_WORKSPACE` | 覆盖 workspace 路径 | `~/.openclaw/workspace` |

## 前置依赖

- Python 3.10+
- Playwright (`pip install playwright && playwright install chromium`)
- requests (`pip install requests`)

**一键安装：** `scripts/setup.sh` 会自动检查并安装所有依赖。

## 使用方式

```bash
# 基本用法：抓取并打印到终端
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py "https://mp.weixin.qq.com/s/xxx"

# 保存到文件
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py "https://mp.weixin.qq.com/s/xxx" -o article.md

# 存档模式：保存原文到 articles/ + 输出结构化摘要（推荐）
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py "https://mp.weixin.qq.com/s/xxx" --archive

# 存档 + AI 摘要（需配置 API key）
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py "https://mp.weixin.qq.com/s/xxx" --archive --summarize

# 指定超时（秒）
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py "https://mp.weixin.qq.com/s/xxx" --timeout 30

# 输出 JSON（结构化数据，适合二次处理）
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py "https://mp.weixin.qq.com/s/xxx" --json -o article.json

# 不提取图片
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py "https://mp.weixin.qq.com/s/xxx" --no-images

# 批量模式：文件内每行一个 URL
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py --batch urls.txt -o ./articles/

# 代理轮换（防 IP 被封）
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py "https://mp.weixin.qq.com/s/xxx" --proxy auto

# 下载图片到本地
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py "https://mp.weixin.qq.com/s/xxx" --download-images -o article.md

# 生成摘要和关键词（需配置 API key）
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py "https://mp.weixin.qq.com/s/xxx" --summarize -o article.md

# 组合使用（批量 + 代理 + 下载图片 + 摘要）
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py \
  --batch urls.txt -o ./articles/ \
  --proxy auto --download-images --summarize

# 详细日志
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py "https://mp.weixin.qq.com/s/xxx" -v
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

## 代理配置

在 `~/.openclaw/workspace/skills/wechat-fetcher/config/proxies.txt` 中每行添加一个代理：

```
# 格式: scheme://host:port 或 scheme://user:pass@host:port
http://127.0.0.1:7890
socks5://user:pass@proxy.example.com:1080
```

使用 `--proxy auto` 时会从该文件随机选取代理，每次请求轮换。

## 存档模式（--archive）

一键保存原文到 articles/，同时输出**闭环报告**，无需用户确认。

```bash
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py "URL" --archive
```

**执行流程**：
1. 抓取文章内容
2. 保存完整 Markdown 原文到 `~/.openclaw/workspace/articles/YYYY-MM-DD-标题.md`
3. **自动评估信息价值**（高/中/低）
4. **自动执行**：
   - 高价值 → 写入 MEMORY.md 相关章节 + 原文存档
   - 中价值 → 仅写入 `memory/YYYY-MM-DD.md` + 原文存档
   - 低价值 → 仅存档，不入 memory
5. 输出**闭环报告**

**闭环报告格式**：
```
✅ 已闭环
━━━━━━━━━━━━
文章：《xxx》
价值：高/中/低（理由）
动作：已写入 memory（章节）+ 存档 / 仅存档
耗时：Xs
待处理：无（如查重冲突或敏感信息则列出）
```

**例外暂停**：
- 与已有记忆内容冲突 → 暂停，列出差异常，由用户决定覆盖/补充/跳过
- 涉及敏感信息（密钥、内部数据）→ 暂停，等用户确认

**配合 --summarize**：
```bash
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py "URL" --archive --summarize
```
- 开启 AI 摘要后，评估更精准
- 未配置 API key 时，自动用正文前 500 字符作为 fallback

## 图片下载

`--download-images` 会将正文中的图片下载到输出目录下的 `images/` 子目录，
并自动替换 Markdown 中的远程 URL 为本地相对路径（如 `images/article_001.jpg`）。

批量模式下，每张图片按文章标题前缀命名，避免冲突。

## 自动摘要配置

摘要功能调用 LLM API（支持 SCNet / DeepSeek / Moonshot / Kimi），支持以下配置方式（按优先级）：

### 方式 1：专用本地配置文件（推荐）

创建 `~/.openclaw/workspace/skills/wechat-fetcher/config/local.json`：

```json
{
  "scnet": {
    "apiKey": "sk-...",
    "baseUrl": "https://api.scnet.cn/api/llm/v1",
    "model": "Qwen3-235B-A22B"
  }
}
```

local.json 的 `model` 字段优先级高于 openclaw.json 的默认配置。

### 方式 2：环境变量

```bash
export KIMI_API_KEY="sk-..."
export DEEPSEEK_API_KEY="sk-..."
export SCNET_API_KEY="sk-..."
```

### 方式 3：openclaw.json

```json
{
  "skills": {
    "wechat-fetcher": {
      "kimiApiKey": "sk-..."
    }
  },
  "models": {
    "providers": {
      "scnet": {
        "apiKey": "sk-...",
        "baseUrl": "https://api.scnet.cn/api/llm/v1",
        "models": [{"id": "Qwen3-235B-A22B"}]
      }
    }
  }
}
```

未配置 API key 时会跳过摘要生成，不影响其他功能。

## 批量模式

创建 `urls.txt`：
```
https://mp.weixin.qq.com/s/abc123
https://mp.weixin.qq.com/s/def456
https://mp.weixin.qq.com/s/ghi789
```

执行：
```bash
python3 ~/.openclaw/workspace/skills/wechat-fetcher/scripts/fetch.py \
  --batch urls.txt -o ./articles/ \
  --download-images --summarize
```

输出目录自动生成按标题命名的 `.md` 或 `.json` 文件。

## 注意事项

- 微信公众号内容是 JS 动态渲染，必须用浏览器环境
- 服务器 IP 可能被微信标记，建议本地执行或使用 `--proxy`
- 遇到验证码页面会返回错误提示
- 代码块检测基于 `<pre>` 标签和简单启发式，复杂排版可能不完美
- 滚动次数上限 20 次，确保懒加载内容被触发
- 图片下载使用 requests，单张超时 30 秒
- 摘要生成依赖外部 LLM API，服务不稳定时会跳过并记录警告

## 版本历史

- **1.2.0**：新增代理轮换、图片下载、LLM 自动摘要（支持多 provider：SCNet / DeepSeek / Moonshot / Kimi）
- **1.1.0**：新增批量模式、JSON 输出、日志系统、简化代码块检测
- **1.0.0**：初始版本，基础抓取 + Markdown 转换
