#!/bin/bash
# AWS EC2 一键部署脚本
# 在本地运行此脚本，将项目部署到 AWS EC2

# 配置变量 - 请修改为你的实际值
EC2_USER="ubuntu"                    # EC2 用户名 (ubuntu 或 ec2-user)
EC2_HOST="your-ec2-public-ip"        # EC2 公网 IP
KEY_PATH="your-key.pem"              # SSH 密钥路径
REMOTE_DIR="~/trading-bot"           # 远程目录

echo "========================================"
echo "AWS EC2 交易机器人部署脚本"
echo "========================================"

# 检查参数
if [ "$EC2_HOST" = "your-ec2-public-ip" ]; then
    echo "❌ 错误：请先修改脚本中的 EC2_HOST 变量为你的 EC2 公网 IP"
    exit 1
fi

if [ ! -f "$KEY_PATH" ]; then
    echo "❌ 错误：SSH 密钥文件不存在: $KEY_PATH"
    echo "请修改 KEY_PATH 变量为你的密钥文件路径"
    exit 1
fi

echo "📦 步骤 1/5: 在 EC2 上安装 Docker..."
ssh -i "$KEY_PATH" "$EC2_USER@$EC2_HOST" << 'ENDSSH'
# 检查 Docker 是否已安装
if ! command -v docker &> /dev/null; then
    echo "安装 Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    echo "✅ Docker 安装完成"
else
    echo "✅ Docker 已安装"
fi

# 创建目录
mkdir -p ~/trading-bot
ENDSSH

echo "📤 步骤 2/5: 上传项目文件..."
scp -i "$KEY_PATH" \
    requirements.txt \
    Dockerfile \
    docker-compose.yml \
    app.py \
    new_strategy_bot.py \
    "$EC2_USER@$EC2_HOST:$REMOTE_DIR/"

echo "⚙️  步骤 3/5: 上传配置文件..."
if [ -f "binance_config.env" ]; then
    scp -i "$KEY_PATH" binance_config.env "$EC2_USER@$EC2_HOST:$REMOTE_DIR/"
    echo "✅ 配置文件已上传"
else
    echo "⚠️  警告：未找到 binance_config.env 文件"
    echo "请在 EC2 上手动创建配置文件"
fi

echo "🐳 步骤 4/5: 构建并启动 Docker 容器..."
ssh -i "$KEY_PATH" "$EC2_USER@$EC2_HOST" << 'ENDSSH'
cd ~/trading-bot
docker compose up -d --build
echo "✅ 容器已启动"
ENDSSH

echo "🔍 步骤 5/5: 检查运行状态..."
ssh -i "$KEY_PATH" "$EC2_USER@$EC2_HOST" << 'ENDSSH'
docker compose ps
echo ""
echo "========================================"
echo "✅ 部署完成！"
echo "========================================"
echo "Web 界面: http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4):7860"
echo ""
echo "查看日志: docker compose logs -f"
echo "停止服务: docker compose down"
echo "重启服务: docker compose restart"
ENDSSH

echo ""
echo "🎉 部署成功！"
