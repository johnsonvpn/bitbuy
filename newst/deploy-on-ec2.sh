#!/bin/bash
# 在 EC2 浏览器终端中执行这个脚本

set -e

echo "========================================"
echo "🚀 开始部署交易机器人"
echo "========================================"

# 1. 创建目录
mkdir -p ~/trading-bot
cd ~/trading-bot

echo ""
echo "📦 步骤 1/7: 创建配置文件"

# 创建 requirements.txt
cat > requirements.txt << 'EOF'
ccxt>=4.0.0
pandas>=2.0.0
python-dotenv>=1.0.0
flask>=2.3.0
requests>=2.31.0
EOF

# 创建 Dockerfile
cat > Dockerfile << 'EOF'
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app.py .
COPY new_strategy_bot.py .

# 创建数据目录
RUN mkdir -p /app/data

# 暴露端口
EXPOSE 7860

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data

# 启动命令
CMD ["python", "app.py"]
EOF

# 创建 docker-compose.yml
cat > docker-compose.yml << 'EOF'
version: '3.8'

services:
  trading-bot:
    build: .
    container_name: trading-bot
    restart: unless-stopped
    ports:
      - "7860:7860"
    volumes:
      - bot-data:/app/data
      - ./binance_config.env:/app/binance_config.env:ro
    environment:
      - PYTHONUNBUFFERED=1
      - DATA_DIR=/app/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:7860/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

volumes:
  bot-data:
EOF

# 创建 binance_config.env 模板
cat > binance_config.env.template << 'EOF'
OKX_API_KEY=your_api_key_here
OKX_SECRET_KEY=your_secret_key_here
OKX_PASSPHRASE=your_passphrase_here
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
EOF

echo ""
echo "📝 步骤 2/7: 创建 binance_config.env (请修改为你的真实 API 密钥！)"
echo "当前使用模板，你需要之后手动编辑 binance_config.env 文件"
cp binance_config.env.template binance_config.env

echo ""
echo "🐍 步骤 3/7: 创建 app.py"
# 这里需要把本地的 app.py 和 new_strategy_bot.py
# 我们用 base64 编码的方式传输，避免多行复制问题

echo "正在下载 app.py..."

# 因为无法从本地传输，我们用另一种方式：让用户从 GitHub 或者使用简单的 echo 方式
# 这里我们先创建一个简单的提示

echo ""
echo "⚠️  注意：需要手动创建 app.py 和 new_strategy_bot.py"
echo ""
echo "你有两个选择："
echo "1. 如果你有代码仓库，可以 git clone"
echo "2. 或者手动创建文件"
echo ""
echo "你现在有 AWS EC2 控制台吗？(y/n)"

# 先安装 Docker
echo ""
echo "🐳 步骤 4/7: 安装 Docker"
if ! command -v docker &> /dev/null; then
    echo "正在安装 Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    echo "✅ Docker 安装完成"
else
    echo "✅ Docker 已安装"
fi

echo ""
echo "========================================"
echo "✅ 准备工作完成！"
echo "========================================"
echo ""
echo "下一步："
echo "1. 编辑 binance_config.env，填入你的 OKX API 密钥"
echo "   nano binance_config.env"
echo ""
echo "2. 然后：你需要把 app.py 和 new_strategy_bot.py 放到这个目录下"
echo "   你可以："
echo "   a) 使用 git clone 你的代码仓库"
echo "   b) 或者手动创建文件：nano app.py（然后复制粘贴代码）"
echo ""
echo "3. 最后运行："
echo "   newgrp docker"
echo "   cd ~/trading-bot"
echo "   docker compose up -d --build"
echo ""
