#!/bin/bash
EC2_IP="44.251.131.76"

echo "🔍 正在尝试连接 EC2 实例: $EC2_IP"
echo ""

# 尝试的密钥列表
KEYS=(
    "$HOME/pem/us.pem"
    "$HOME/Downloads/us.pem"
    "$HOME/Downloads/pn.pem"
    "$HOME/Downloads/hk.pem"
    "$HOME/Downloads/mg.pem"
    "$HOME/Downloads/cgfy.pem"
    "$HOME/Downloads/newhk.pem"
    "$HOME/Downloads/dlfy.pem"
    "$HOME/.ssh/id_rsa"
    "$HOME/.ssh/id_ed25519"
    "$HOME/.ssh/personal"
    "$HOME/.ssh/gp"
    "$HOME/.ssh/google_compute_engine"
)

# 尝试的用户名列表
USERS=("ubuntu" "ec2-user" "root")

for USER in "${USERS[@]}"; do
    for KEY in "${KEYS[@]}"; do
        if [ -f "$KEY" ]; then
            echo "尝试: $USER@$EC2_IP - 密钥: $KEY"
            ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -i "$KEY" "$USER@$EC2_IP" "echo '✅ 成功连接！用户: $USER, 密钥: $KEY'" 2>/dev/null
            if [ $? -eq 0 ]; then
                echo ""
                echo "🎉 找到可用连接！"
                echo "用户: $USER"
                echo "密钥路径: $KEY"
                exit 0
            fi
        fi
    done
done

echo ""
echo "❌ 没有找到可用的连接"
echo ""
echo "建议："
echo "1. 检查 AWS EC2 控制台，确认实例关联的密钥对名称"
echo "2. 使用 EC2 Instance Connect 连接"
echo "3. 或者创建新密钥对并重新关联实例"
