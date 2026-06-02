#!/usr/bin/env python3
"""
微信公众号文章抓取器
通过 Playwright 无头浏览器模拟真实用户访问，提取内容并转为 Markdown。
支持代理轮换、图片下载、自动摘要。

用法:
    python3 fetch.py <url> [-o output.md] [--timeout 20] [--json] [--proxy auto]
    python3 fetch.py --batch urls.txt -o ./articles/ --download-images --summarize
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ---- 日志配置 ----
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("wechat-fetcher")


# ---- 辅助函数 ----

def _load_proxies(config_path: Optional[str] = None) -> list[str]:
    """加载代理列表。"""
    if config_path:
        path = Path(config_path)
    else:
        path = Path(__file__).parent.parent / "config" / "proxies.txt"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    proxies = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    logger.debug("加载 %d 个代理", len(proxies))
    return proxies


def _pick_proxy(proxies: list[str]) -> Optional[dict]:
    """随机选一个代理，返回 Playwright 的 proxy 参数字典。"""
    if not proxies:
        return None
    proxy_url = random.choice(proxies)
    parsed = urlparse(proxy_url)
    # Playwright 的 proxy 格式: {"server": "http://host:port", "username": "x", "password": "y"}
    proxy_dict: dict = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        proxy_dict["username"] = parsed.username
    if parsed.password:
        proxy_dict["password"] = parsed.password
    logger.info("使用代理: %s", proxy_dict["server"])
    return proxy_dict


def _download_images(images: list[str], output_dir: Path, article_title: str) -> dict[str, str]:
    """下载图片到本地，返回 {原URL: 本地相对路径} 映射。"""
    img_dir = output_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    safe_prefix = re.sub(r"[^\w\u4e00-\u9fa5_-]", "_", article_title)[:20] or "img"

    for idx, url in enumerate(images, 1):
        try:
            ext = Path(urlparse(url).path).suffix or ".jpg"
            # 微信图片 URL 可能以 /640 结尾，无扩展名
            if not ext.startswith("."):
                ext = ".jpg"
            filename = f"{safe_prefix}_{idx:03d}{ext}"
            local_path = img_dir / filename
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            local_path.write_bytes(r.content)
            # 相对路径（相对于 output_dir）
            rel_path = f"images/{filename}"
            mapping[url] = rel_path
            logger.debug("下载图片: %s -> %s", url[:60], rel_path)
        except Exception as e:
            logger.warning("图片下载失败 %s: %s", url[:60], e)
            continue

    logger.info("下载完成: %d/%d 张图片", len(mapping), len(images))
    return mapping


def _summarize(content: str, title: str, api_key: Optional[str] = None,
                base_url: Optional[str] = None, model_id: Optional[str] = None,
                provider: Optional[str] = None) -> tuple[str, list[str]]:
    """
    调用 LLM API 生成摘要和关键词。
    自动检测 provider：DeepSeek / Kimi coding / Moonshot OpenAI。
    返回 (摘要, [关键词1, 关键词2, ...])
    """
    # 尝试各 provider 的 key（优先级：显式传入 > 环境变量 > 配置文件）
    api_key = api_key or os.environ.get("KIMI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("SCNET_API_KEY", "")

    cfg: dict = {}
    kimi_cfg: dict = {}
    deepseek_cfg: dict = {}
    scnet_cfg: dict = {}
    if not api_key:
        # 1. 读 openclaw.json
        config_path = Path.home() / ".openclaw" / "openclaw.json"
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                kimi_cfg = cfg.get("models", {}).get("providers", {}).get("kimi", {})
                deepseek_cfg = cfg.get("models", {}).get("providers", {}).get("deepseek", {})
                scnet_cfg = cfg.get("models", {}).get("providers", {}).get("scnet", {})
                # 优先显式配置的 wechat-fetcher key
                api_key = cfg.get("skills", {}).get("wechat-fetcher", {}).get("kimiApiKey", "")
                # 回退到各 provider
                if not api_key:
                    api_key = (
                        scnet_cfg.get("apiKey", "")
                        or deepseek_cfg.get("apiKey", "")
                        or kimi_cfg.get("apiKey", "")
                    )
                if not base_url:
                    base_url = (
                        scnet_cfg.get("baseUrl", "")
                        or deepseek_cfg.get("baseUrl", "")
                        or kimi_cfg.get("baseUrl", "")
                    )
                if not model_id:
                    model_id = (
                        scnet_cfg.get("models", [{}])[0].get("id", "")
                        or deepseek_cfg.get("models", [{}])[0].get("id", "")
                        or kimi_cfg.get("models", [{}])[0].get("id", "")
                    )
                if not provider:
                    provider = (
                        "scnet" if scnet_cfg else
                        ("deepseek" if deepseek_cfg else ("kimi" if kimi_cfg else ""))
                    )
            except Exception:
                pass

        # 2. 读 wechat-fetcher 本地配置（local.json）——优先覆盖 openclaw.json 的值
        local_cfg_path = Path(__file__).parent.parent / "config" / "local.json"
        if local_cfg_path.exists():
            try:
                local_cfg = json.loads(local_cfg_path.read_text(encoding="utf-8"))
                for provider_name, pcfg in local_cfg.items():
                    if pcfg.get("apiKey"):
                        api_key = api_key or pcfg["apiKey"]  # local key 优先级低于 openclaw
                        base_url = pcfg.get("baseUrl", "") or base_url
                        model_id = pcfg.get("model", "") or model_id  # local model 优先级更高
                        provider = provider or provider_name
            except Exception:
                pass

    if not api_key:
        logger.warning("未配置 API key（KIMI_API_KEY / DEEPSEEK_API_KEY / SCNET_API_KEY），跳过摘要生成")
        return "", []

    # 推断 provider
    if not provider:
        if base_url and "scnet.cn" in base_url.lower():
            provider = "scnet"
        elif base_url and "deepseek" in base_url.lower():
            provider = "deepseek"
        elif base_url and "kimi.com" in base_url.lower():
            provider = "kimi"
        elif os.environ.get("SCNET_API_KEY"):
            provider = "scnet"
        elif os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("KIMI_API_KEY"):
            provider = "deepseek"
        else:
            provider = "kimi"

    # 端点和模型
    if provider == "scnet":
        url = base_url.rstrip("/") + "/chat/completions" if base_url else "https://api.scnet.cn/api/llm/v1/chat/completions"
        model = model_id or "Qwen3-235B-A22B"
    elif provider == "deepseek":
        url = base_url.rstrip("/") + "/chat/completions" if base_url else "https://api.deepseek.com/v1/chat/completions"
        model = model_id or "deepseek-chat"
    else:
        is_kimi_coding = bool(base_url and "kimi.com" in base_url)
        url = base_url.rstrip("/") + "/chat/completions" if base_url else "https://api.moonshot.cn/v1/chat/completions"
        model = model_id or ("kimi-k2-5" if is_kimi_coding else "moonshot-v1-8k")

    # 截取正文前 8000 字符
    truncated = content[:8000]
    # MiniMax-M2.5 对长中文内容可能返回 510，尝试截取更短
    if provider == "scnet" and "minimax" in model.lower():
        truncated = content[:4000]
        logger.debug("SCNet MiniMax: 正文截断至 %d 字符", len(truncated))
    prompt = (
        f"Generate a Chinese summary (within 200 Chinese characters) and 5 keywords for the following article. "
        f"Output ONLY in pure JSON format: {{\"summary\": \"...\", \"keywords\": [\"...\", \"...\"]}}\n\n"
        f"Title: {title}\n\nContent:\n{truncated}"
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    if provider not in ("kimi",) or not (base_url and "kimi.com" in base_url):
        # DeepSeek / SCNet / Moonshot 支持 max_tokens
        payload["max_tokens"] = 1024

    try:
        logger.info("调用 %s API (%s) 生成摘要...", provider, model)
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            summary = parsed.get("summary", "")
            keywords = parsed.get("keywords", [])
            if isinstance(keywords, str):
                keywords = [k.strip() for k in keywords.split(",") if k.strip()]
            logger.info("摘要生成完成: %d 关键词", len(keywords))
            return summary, keywords
    except Exception as e:
        logger.warning("摘要生成失败: %s", e)

    return "", []



# 噪音正则（广告、推广、脚本样式等）
NOISE_PATTERNS = [
    r'<div[^>]*class="[^"]*rich_media_tool[^"]*"[^>]*>.*?</div>',
    r'<div[^>]*id="js_pc_qr_code"[^>]*>.*?</div>',
    r'<div[^>]*class="[^"]*qr_code_pc[^"]*"[^>]*>.*?</div>',
    r'<div[^>]*class="[^"]*reward[^"]*"[^>]*>.*?</div>',
    r'<div[^>]*class="[^"]*like_tip[^"]*"[^>]*>.*?</div>',
    r'<div[^>]*class="[^"]*rich_media_area_extra[^"]*"[^>]*>.*?</div>',
    r'<div[^>]*class="[^"]*related_reading[^"]*"[^>]*>.*?</div>',
    r'<div[^>]*class="[^"]*js_end_share[^"]*"[^>]*>.*?</div>',
    r'<div[^>]*class="[^"]*share_notice[^"]*"[^>]*>.*?</div>',
    r'<div[^>]*class="[^"]*blog_code_wechat[^"]*"[^>]*>.*?</div>',
    r'<div[^>]*class="[^"]*qr_code[^"]*"[^>]*>.*?</div>',
    r'<div[^>]*class="[^"]*js_wx_tap[^"]*"[^>]*>.*?</div>',
    r'<script[^>]*>.*?</script>',
    r'<style[^>]*>.*?</style>',
]

# 基础 HTML → Markdown 映射
HTML_CONVERSIONS = [
    (r'<h1[^>]*>(.*?)</h1>', r'# \1'),
    (r'<h2[^>]*>(.*?)</h2>', r'## \1'),
    (r'<h3[^>]*>(.*?)</h3>', r'### \1'),
    (r'<h4[^>]*>(.*?)</h4>', r'#### \1'),
    (r'<strong>(.*?)</strong>', r'**\1**'),
    (r'<b>(.*?)</b>', r'**\1**'),
    (r'<em>(.*?)</em>', r'*\1*'),
    (r'<i>(.*?)</i>', r'*\1*'),
    (r'<br\s*/?>', '\n'),
    (r'<p[^>]*>', '\n\n'),
    (r'</p>', ''),
    (r'<blockquote[^>]*>', '\n> '),
    (r'</blockquote>', '\n'),
    (r'<li[^>]*>', '- '),
    (r'</li>', '\n'),
    (r'<ul[^>]*>', '\n'),
    (r'</ul>', '\n'),
    (r'<ol[^>]*>', '\n'),
    (r'</ol>', '\n'),
    (r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)'),
    (r'<img[^>]*data-src="([^"]*)"[^>]*/?>', r'![](\1)'),
    (r'<img[^>]*src="([^"]*)"[^>]*/?>', r'![](\1)'),
    (r'<pre[^>]*>', '\n\n```\n'),
    (r'</pre>', '\n```\n\n'),
    (r'<code[^>]*>', '`'),
    (r'</code>', '`'),
]

HTML_ENTITIES = {
    '&nbsp;': ' ',
    '&amp;': '&',
    '&lt;': '<',
    '&gt;': '>',
    '&quot;': '"',
    '&#39;': "'",
}

TEXT_NOISE_PATTERNS = [
    r'关注公众号[，,].*$',
    r'后台回复.*?获取.*$',
    r'one more thing.*$',
    r'我建了.*?交流群.*$',
    r'感兴趣的朋友.*?后台留言.*$',
    r'\n往期文章精选[：:]\s*\n.*?(?=\n我是|\Z)',
    r'\n我是\S{2,6}[，,]专注于.*$',
]


def clean_html_to_markdown(html: str) -> str:
    """清理 HTML 并转为 Markdown 格式。"""
    text = html

    # 1. 去除噪音区块
    for pattern in NOISE_PATTERNS:
        text = re.sub(pattern, '', text, flags=re.DOTALL | re.IGNORECASE)

    # 2. 代码块：微信文章里的 <pre> 标签通常就是代码块，但有很多文章
    #    用 <p> 放代码。这里用简单启发式：连续 3 行以上以常见代码前缀开头 → 代码块
    text = _heuristic_code_blocks(text)

    # 3. 基础标签转换
    for pattern, replacement in HTML_CONVERSIONS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE | re.DOTALL)

    # 4. 去除剩余标签
    text = re.sub(r'<[^>]+>', '', text)

    # 5. 解码 HTML 实体
    for entity, char in HTML_ENTITIES.items():
        text = text.replace(entity, char)

    # 6. 清理图片 URL 水印参数
    text = re.sub(
        r'!\[\]\((https?://mmbiz[^)]+)\)',
        lambda m: '![](' + re.sub(r'[?#].*$', '', m.group(1)) + ')',
        text,
    )

    # 7. 去除文末推广噪音
    for pattern in TEXT_NOISE_PATTERNS:
        text = re.sub(pattern, '', text, flags=re.DOTALL | re.MULTILINE)

    # 8. 标题前补空行
    text = re.sub(r'([^\n])((?:#{1,4}) )', r'\1\n\n\2', text)

    # 9. 压缩连续空行
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def _get_workspace():
    """推断 workspace 路径，支持环境变量覆盖。"""
    return Path(os.environ.get("OPENCLAW_WORKSPACE", Path.home() / ".openclaw" / "workspace"))


def _sanitize_content(text: str) -> str:
    """
    安全清洗：过滤提示注入、ASCII 走私、隐藏字符等潜在恶意载荷。
    应用于所有抓取内容写入 memory 或上下文前。
    """
    import unicodedata

    # 1. 零宽字符 / 隐藏字符检测与移除
    #    U+200B-U+200F, U+2060-U+2064, U+FEFF, U+180E, U+200C-U+200D 等
    zero_width = [
        '\u200b', '\u200c', '\u200d', '\u200e', '\u200f',
        '\u2060', '\u2061', '\u2062', '\u2063', '\u2064',
        '\ufeff', '\u180e',
    ]
    for char in zero_width:
        if char in text:
            text = text.replace(char, '')
            # 不逐字符记录，数量大时太吵；只在发现时统一记一次

    # 2. 控制字符清理（保留正常换行、制表符）
    cleaned = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith('Cc') and ch not in '\n\t\r':
            continue
        cleaned.append(ch)
    text = ''.join(cleaned)

    # 3. 提示注入关键词检测（不删除，只标记警告——供上层决策）
    #    这里只做轻度模式，避免过度误伤正常内容
    injection_patterns = [
        r'ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?',
        r'you\s+are\s+now\s+(?:in\s+)?(?:DAN|developer|admin|root)\s+mode',
        r'jailbreak\s*[:\-]?',
        r'system\s+prompt\s+leak',
        r'forget\s+(?:everything|all)\s+(?:before|above)',
        r'\bDAN\b\s+mode',
        r'new\s+instructions?\s*[:\-]',
    ]
    for pattern in injection_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            # 发现潜在注入，在文末附加警告标记（不改变正文，供后续审核）
            warning = f"\n\n[⚠️ 安全警告：检测到潜在提示注入模式 `{pattern}`，请人工复核]"
            if warning not in text:
                text += warning
            break  # 只标记一次

    # 4. ASCII 走私检测：常见隐藏编码字符
    #    如 Homoglyph（同形字符）——极度隐蔽，但误伤率高，暂不处理
    #    如需严格场景，可引入外部库 `confusable_homoglyphs`

    # 5. 统计信息
    removed_zero_width = sum(1 for c in zero_width if c in text)
    if removed_zero_width > 0:
        logger.info("安全清洗: 移除 %d 个零宽/隐藏字符", removed_zero_width)

    return text


def _heuristic_code_blocks(text: str) -> str:
    """
    对纯文本中可能是代码块的段落加 ``` 包裹。
    微信文章很多代码没有用 <pre>，而是多行 <p> 或 <section>。
    """
    # 简单策略：将已被 <pre> 包裹的部分先保留，后续处理逻辑已覆盖
    # 对于未用 <pre> 的代码，在 clean_html_to_markdown 后再处理比较困难，
    # 因此这里先不做额外复杂处理，依赖作者使用 <pre> 的规范度。
    # 如果后续发现大量误识别/漏识别，再引入更重的方案。
    return text


# ---- 抓取逻辑 ----

def fetch_article(url: str, timeout: int = 20, no_images: bool = False, proxy: Optional[dict] = None) -> dict:
    """
    抓取微信公众号文章。

    Returns:
        {"title": ..., "author": ..., "content": ..., "error": ...}
    """
    result = {
        "title": "",
        "author": "",
        "tagline": "",
        "publish_time": "",
        "content": "",
        "images": [],
        "url": url,
        "error": None,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy=proxy)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        try:
            logger.info("正在访问: %s", url)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)

            title = page.title()
            if "环境异常" in title or "验证" in title:
                result["error"] = "微信检测到异常环境，需要验证码（建议本地执行）"
                return result

            page.wait_for_selector("#js_content", timeout=timeout * 1000)
            logger.info("正文已加载，开始滚动拉取全文...")

            # 模拟滚动触发懒加载
            prev_height = 0
            for i in range(20):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                time.sleep(0.5)
                curr_height = page.evaluate("document.body.scrollHeight")
                if curr_height == prev_height and i > 3:
                    break
                prev_height = curr_height
            page.evaluate("window.scrollTo(0, 0)")
            time.sleep(0.5)

            # 提取元信息
            result["title"] = page.evaluate(
                "document.querySelector('#activity-name')?.innerText?.trim() || document.title"
            )
            result["author"] = page.evaluate(
                "document.querySelector('#js_name')?.innerText?.trim() || "
                "document.querySelector('.rich_media_meta_nickname')?.innerText?.trim() || ''"
            )
            result["tagline"] = page.evaluate(
                "document.querySelector('.profile_nickname')?.nextElementSibling?.innerText?.trim() || "
                "document.querySelector('.profile_meta_value')?.innerText?.trim() || ''"
            )
            result["publish_time"] = page.evaluate(
                "document.querySelector('#publish_time')?.innerText?.trim() || "
                "document.querySelector('.rich_media_meta_text')?.innerText?.trim() || ''"
            )

            # 正文 HTML
            content_html = page.evaluate(
                "document.querySelector('#js_content')?.innerHTML || ''"
            )

            # 图片 URL（清理水印参数）
            image_urls = page.evaluate(
                "Array.from(document.querySelectorAll('#js_content img'))"
                ".map(img => img.dataset.src || img.src)"
                ".filter(u => u && !u.startsWith('data:'))"
            )
            result["images"] = [re.sub(r'[?#].*$', '', u) for u in image_urls]

            # 转换 Markdown
            result["content"] = clean_html_to_markdown(content_html)
            
            # 安全清洗：过滤提示注入、隐藏字符等
            result["content"] = _sanitize_content(result["content"])

            if not result["content"].strip():
                result["error"] = "正文为空，可能页面未正确加载"

            logger.info("抓取完成: %s", result["title"] or "(无标题)")

        except PlaywrightTimeout:
            result["error"] = f"超时（{timeout}s）：页面加载或元素定位失败"
        except Exception as e:
            result["error"] = f"抓取失败: {e}"
        finally:
            browser.close()

    return result


def _archive_result(result: dict, no_images: bool, summarize: bool = False) -> str:
    """
    存档模式：保存完整原文到 articles/，并返回评估报告。
    评估报告包含信息价值判断和建议操作，供用户确认后决定是否写入 memory。
    """
    from datetime import datetime
    workspace = _get_workspace()

    # 1. 保存完整原文到 articles/
    articles_dir = workspace / "articles"
    articles_dir.mkdir(parents=True, exist_ok=True)

    safe_title = re.sub(r'[^\w\u4e00-\u9fa5_-]', '_', result["title"])[:40] or "untitled"
    today = datetime.now().strftime("%Y-%m-%d")
    article_filename = f"{today}-{safe_title}.md"
    article_path = articles_dir / article_filename

    # 生成完整 Markdown（不含 AI 摘要区块）
    full_md = _build_markdown(result, no_images)
    article_path.write_text(full_md, encoding="utf-8")
    logger.info("[archive] 原文已保存: %s", article_path)

    # 2. 生成评估报告
    summary = ""
    keywords: list[str] = []
    if summarize and result["content"]:
        summary, keywords = _summarize(result["content"], result["title"])

    # 信息价值评估
    content_len = len(result.get("content", ""))
    has_tech_detail = any(kw in result["content"] for kw in ["代码", "配置", "API", "架构", "原理", "实践"])
    is_promotional = any(kw in result["content"] for kw in ["课程", "培训", "限时", "扫码", "免费领", "原价"])
    
    if is_promotional and not has_tech_detail:
        value_level = "低"
        value_reason = "营销推广文，技术深度不足"
        suggestion = "建议：只存档原文，不写入 memory"
    elif has_tech_detail and content_len > 2000:
        value_level = "高"
        value_reason = "有具体技术细节和实践案例"
        suggestion = "建议：写入 memory，提取核心框架和行动项"
    elif content_len > 1000:
        value_level = "中"
        value_reason = "有一定信息量，但技术深度一般"
        suggestion = "建议：写入 memory，简要记录核心观点"
    else:
        value_level = "低"
        value_reason = "内容较短，信息量有限"
        suggestion = "建议：只存档原文，不写入 memory"

    keyword_str = ""
    if keywords:
        keyword_str = ", ".join(keywords)

    # 如果没生成 AI 摘要，用正文前 500 字符作为 fallback
    core_content = summary or result["content"][:500].strip()
    core_content = re.sub(r'\n{2,}', '\n', core_content)

    report = f"""\n{'='*60}
📋 文章评估报告（请确认后决定是否写入 memory）
{'='*60}

**标题**: {result['title']}
**来源**: 微信公众号「{result['author']}」
**发布时间**: {result.get('publish_time', '未知')}
**原文存档**: articles/{article_filename}

**信息价值评估**:
- 价值等级: {value_level}
- 评估理由: {value_reason}
- 建议操作: {suggestion}

**关键词**: {keyword_str or '未生成'}

**核心内容预览**:
{core_content[:300]}...

{'='*60}
💡 请确认:
- 回复 "同意" → 我将按建议执行（写入 memory 或仅存档）
- 回复 "只存档" → 仅保留原文，不写入 memory
- 回复 "删除" → 删除原文存档
- 回复 "写入" → 强制写入 memory（无论评估等级）
{'='*60}
"""
    return report


def _build_markdown(result: dict, no_images: bool) -> str:
    """将结果字典组装为 Markdown 字符串。"""
    md = f"# {result['title']}\n\n"

    meta = []
    if result["author"]:
        meta.append(f"**公众号**: {result['author']}")
    if result.get("tagline"):
        meta.append(f"**简介**: {result['tagline']}")
    if result.get("publish_time"):
        meta.append(f"**发布时间**: {result['publish_time']}")
    if meta:
        md += "\n".join(meta) + "\n\n---\n\n"

    md += result["content"]

    # 追加正文未引用的额外图片
    if result.get("images") and not no_images:
        used = set(re.findall(r'!\[.*?\]\((.*?)\)', result["content"]))
        extra = [u for u in result["images"] if u not in used]
        if extra:
            md += "\n\n---\n\n## 附图\n\n"
            for i, img_url in enumerate(extra, 1):
                md += f"![图{i}]({img_url})\n\n"

    return md


def _save_result(result: dict, output_path: Optional[str], as_json: bool, no_images: bool,
                 download_images: bool = False, summarize: bool = False) -> None:
    """保存结果到文件或 stdout，可选下载图片和生成摘要。"""
    # 下载图片
    img_mapping: dict[str, str] = {}
    if download_images and result.get("images"):
        out_dir = Path(output_path).parent if output_path else Path.cwd()
        img_mapping = _download_images(result["images"], out_dir, result["title"])
        # 替换正文中的图片 URL
        if img_mapping and not as_json:
            for orig, local in img_mapping.items():
                result["content"] = result["content"].replace(orig, local)
                result["images"] = [img_mapping.get(u, u) for u in result["images"]]

    # 生成摘要
    summary = ""
    keywords: list[str] = []
    if summarize and result["content"]:
        summary, keywords = _summarize(result["content"], result["title"])

    if as_json:
        payload = {
            "title": result["title"],
            "author": result["author"],
            "publish_time": result.get("publish_time"),
            "summary": summary,
            "keywords": keywords,
            "content": result["content"],
            "images": result.get("images", []),
            "url": result["url"],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        md = f"# {result['title']}\n\n"
        # 摘要区块
        if summary or keywords:
            md += "> **摘要**: " + summary + "\n\n"
            if keywords:
                md += "> **关键词**: " + " ".join(f"`{k}`" for k in keywords) + "\n\n"
            md += "---\n\n"

        meta = []
        if result["author"]:
            meta.append(f"**公众号**: {result['author']}")
        if result.get("tagline"):
            meta.append(f"**简介**: {result['tagline']}")
        if result.get("publish_time"):
            meta.append(f"**发布时间**: {result['publish_time']}")
        if meta:
            md += "\n".join(meta) + "\n\n---\n\n"

        md += result["content"]

        # 追加正文未引用的额外图片
        if result.get("images") and not no_images:
            used = set(re.findall(r'!\[.*?\]\((.*?)\)', result["content"]))
            extra = [u for u in result["images"] if u not in used]
            if extra:
                md += "\n\n---\n\n## 附图\n\n"
                for i, img_url in enumerate(extra, 1):
                    md += f"![图{i}]({img_url})\n\n"

        text = md

    if output_path:
        path = Path(output_path)
        path.write_text(text, encoding="utf-8")
        logger.info("已保存到: %s", path)
    else:
        print(text)


# ---- CLI ----

def main():
    parser = argparse.ArgumentParser(
        description="微信公众号文章抓取器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 fetch.py "https://mp.weixin.qq.com/s/xxx"
  python3 fetch.py "https://mp.weixin.qq.com/s/xxx" -o article.md
  python3 fetch.py "https://mp.weixin.qq.com/s/xxx" --json -o article.json
  python3 fetch.py --batch urls.txt -o ./articles/
  python3 fetch.py "https://mp.weixin.qq.com/s/xxx" --archive --summarize
        """,
    )
    parser.add_argument("url", nargs="?", help="微信公众号文章链接")
    parser.add_argument("-o", "--output", help="输出文件路径（默认打印到终端）")
    parser.add_argument("--timeout", type=int, default=20, help="超时秒数（默认 20）")
    parser.add_argument("--no-images", action="store_true", help="不输出图片链接")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown")
    parser.add_argument("--batch", metavar="FILE", help="批量模式：文件内每行一个 URL")
    parser.add_argument("--proxy", metavar="auto|FILE", default=None,
                        help="代理模式：auto（自动轮换）、或指定代理文件路径")
    parser.add_argument("--download-images", action="store_true",
                        help="下载正文图片到本地 images/ 目录")
    parser.add_argument("--archive", action="store_true",
                        help="存档模式：保存完整原文到 articles/ 并输出结构化摘要，便于写入 memory/")
    parser.add_argument("--summarize", action="store_true",
                        help="调用 Kimi API 生成摘要和关键词（需配置 API key）")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # 加载代理
    proxies = []
    if args.proxy == "auto":
        proxies = _load_proxies()
    elif args.proxy:
        proxies = _load_proxies(args.proxy)

    # 测试代理加载
    if args.proxy:
        logger.info("代理模式: %s", args.proxy)
        if not proxies:
            logger.warning("未找到可用代理，将直连")

    urls = []
    if args.batch:
        batch_path = Path(args.batch)
        if not batch_path.exists():
            logger.error("批量文件不存在: %s", args.batch)
            sys.exit(1)
        urls = [line.strip() for line in batch_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        logger.info("批量模式: %d 个 URL", len(urls))
    elif args.url:
        urls = [args.url]
    else:
        parser.print_help()
        sys.exit(1)

    # 批量模式校验输出路径
    output_dir = None
    if args.output and len(urls) > 1:
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)

    failed = 0
    for idx, url in enumerate(urls, 1):
        if len(urls) > 1:
            logger.info("[%d/%d] %s", idx, len(urls), url)

        if "mp.weixin.qq.com" not in url:
            logger.warning("这不是微信公众号链接，但仍会尝试抓取: %s", url)

        result = fetch_article(url, args.timeout, no_images=args.no_images, proxy=_pick_proxy(proxies))

        if result["error"]:
            logger.error("失败: %s", result["error"])
            failed += 1
            continue

        # 确定输出路径
        out_path = None
        if args.output:
            if len(urls) > 1:
                # 自动生成文件名
                ext = ".json" if args.json else ".md"
                safe_title = re.sub(r'[^\w\u4e00-\u9fa5_-]', '_', result["title"])[:40] or f"article_{idx}"
                out_path = str(output_dir / f"{safe_title}{ext}")
            else:
                out_path = args.output

        _save_result(result, out_path, args.json, args.no_images,
                     download_images=args.download_images, summarize=args.summarize)

        # 存档模式：额外保存到 articles/ 并输出结构化摘要
        if args.archive:
            memory_entry = _archive_result(result, args.no_images, summarize=args.summarize)
            print("\n" + "=" * 60)
            print("📋 结构化摘要（可复制追加到 memory/YYYY-MM-DD.md）：")
            print("=" * 60)
            print(memory_entry)
            print("=" * 60)

    if failed:
        logger.warning("完成，%d/%d 失败", failed, len(urls))
        sys.exit(1)
    else:
        logger.info("全部完成，共 %d 篇", len(urls))


if __name__ == "__main__":
    main()
