#!/bin/bash
# 部署到 EC2 的本地脚本

set -e

EC2_IP="35.74.81.38"
SSH_KEY="$HOME/pem/jpbian.pem"
SSH_USER="ubuntu"
REMOTE_DIR="~/trading-bot"
LOCAL_DIR="trading_bot"

echo "==========================================="
echo "🚀 交易机器人 AWS 部署脚本"
echo "==========================================="
echo ""

# 检测 SSH 连接
echo "📝 检测 EC2 连接..."
ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no -i "$SSH_KEY" "$SSH_USER@$EC2_IP" "echo '✓ 连接正常'"

echo ""
echo "📦 检查本地文件..."
ls -la "$LOCAL_DIR"

echo ""
echo "📤 创建远程目录..."
ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "$SSH_USER@$EC2_IP" "mkdir -p $REMOTE_DIR"

echo ""
echo "📤 上传文件到 EC2..."
cd "$LOCAL_DIR"
scp -o StrictHostKeyChecking=no -i "$SSH_KEY" requirements.txt Dockerfile docker-compose.yml app.py new_strategy_bot.py "$SSH_USER@$EC2_IP:$REMOTE_DIR/"
cd ..

# 创建配置文件模板
echo ""
echo "📝 创建配置文件..."
ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "$SSH_USER@$EC2_IP" << 'ENDSSH'
cd ~/trading-bot
if [ ! -f binance_config.env ]; then
    cat > binance_config.env << 'EOF'
BINANCE_API_KEY=your_api_key_here
BINANCE_SECRET_KEY=your_secret_key_here
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
EOF
fi
ls -la
ENDSSH

echo ""
echo "🐳 检查 Docker 状态..."
ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "$SSH_USER@$EC2_IP" << 'ENDSSH'
cd ~/trading-bot
if command -v docker &> /dev/null; then
    echo "✓ Docker 已安装"
    docker --version
else
    echo "🔧 正在安装 Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    echo "✓ Docker 安装完成！"
    echo ""
    echo "⚠️  请重新运行部署脚本以继续（docker 用户组需要重新登录生效）"
    exit 1
fi
ENDSSH

echo ""
echo "==========================================="
echo "✅ 部署准备完成！"
echo "==========================================="
echo ""
echo "📋 接下来步骤："
echo ""
echo "1. SSH 登录 EC2："
echo "   ssh -i ~/pem/jpbian.pem ubuntu@35.74.81.38"
echo ""
echo "2. 配置 API 密钥："
echo "   cd ~/trading-bot"
echo "   nano binance_config.env"
echo ""
echo "3. 启动机器人："
echo "   docker compose up -d --build"
echo ""
echo "4. 查看日志："
echo "   docker compose logs -f"
echo ""
echo "🌐 访问地址：http://35.74.81.38:7860"
echo "==========================================="
