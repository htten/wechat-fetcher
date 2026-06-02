#!/usr/bin/env bash
set -euo pipefail

echo "Installing wechat-fetcher dependencies..."

# 1. Python 版本检查
python_version=$(python3 --version 2>/dev/null | awk '{print $2}' || echo "")
if [[ -z "$python_version" ]]; then
    echo "Error: Python 3 is required but not found."
    exit 1
fi

echo "Python version: $python_version"

# 2. 安装 Python 依赖
echo "Installing Python packages..."
pip3 install -q playwright requests

# 3. 安装 Playwright 浏览器
echo "Installing Playwright Chromium..."
python3 -m playwright install chromium

echo "✅ wechat-fetcher dependencies installed successfully!"
echo ""
echo "Usage:"
echo "  python3 scripts/fetch.py \"https://mp.weixin.qq.com/s/xxx\""
