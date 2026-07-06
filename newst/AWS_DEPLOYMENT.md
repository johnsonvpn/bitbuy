# AWS 部署指南

本指南将帮助你将交易机器人部署到 Amazon Web Services (AWS) 上。

## 方案对比

| 方案 | 优点 | 缺点 | 推荐度 |
|------|------|------|--------|
| **EC2 + Docker** | 完全控制，灵活 | 需要自己维护服务器 | ⭐⭐⭐⭐⭐ |
| **ECS/Fargate** | 托管服务，自动扩缩容 | 配置稍复杂 | ⭐⭐⭐⭐ |
| **Lightsail** | 简单易用，价格固定 | 功能相对简单 | ⭐⭐⭐ |

---

## 方案一：EC2 + Docker（推荐）

### 1. 创建 EC2 实例

1. 登录 AWS 控制台
2. 进入 **EC2** 服务
3. 点击 **启动实例**
4. 选择操作系统：推荐使用 **Ubuntu Server 22.04 LTS**
5. 选择实例类型：推荐 `t2.micro`（免费套餐可用）或 `t3.small`
6. 配置安全组：
   - SSH: 端口 22 (仅限你的 IP)
   - HTTP: 端口 7860 (可选，用于 Web 界面)
7. 选择或创建密钥对
8. 启动实例

### 2. 连接到 EC2 实例

```bash
ssh -i your-key.pem ubuntu@your-ec2-public-ip
```

### 3. 安装 Docker 和 Docker Compose

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装 Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# 将用户添加到 docker 组
sudo usermod -aG docker $USER

# 重新登录使组权限生效
exit

# 重新连接后验证 Docker 安装
docker --version
docker compose version
```

### 4. 上传项目文件

使用 SCP 上传文件到 EC2：

```bash
# 在本地终端执行
scp -i your-key.pem requirements.txt Dockerfile docker-compose.yml app.py new_strategy_bot.py ubuntu@your-ec2-public-ip:~/trading-bot/

# 上传配置文件（确保配置文件安全！）
scp -i your-key.pem binance_config.env ubuntu@your-ec2-public-ip:~/trading-bot/
```

或者使用 git 克隆（如果项目在 GitHub 上）：

```bash
git clone your-repo-url ~/trading-bot
cd ~/trading-bot
# 手动创建 binance_config.env 文件
```

### 5. 启动容器

```bash
cd ~/trading-bot

# 构建并启动
docker compose up -d --build

# 查看日志
docker compose logs -f

# 查看状态
docker compose ps
```

### 6. 配置持久化

修改代码中的状态文件路径到 `/app/data/` 目录（可选但推荐）：

```python
STATE_FILE = Path('/app/data/state.json')
```

---

## 方案二：ECS Fargate（托管容器服务）

### 1. 推送 Docker 镜像到 ECR

1. 创建 ECR 仓库
2. 构建并推送镜像：

```bash
# 登录 ECR
aws ecr get-login-password --region your-region | docker login --username AWS --password-stdin your-account-id.dkr.ecr.your-region.amazonaws.com

# 构建镜像
docker build -t trading-bot .

# 打标签
docker tag trading-bot:latest your-account-id.dkr.ecr.your-region.amazonaws.com/trading-bot:latest

# 推送
docker push your-account-id.dkr.ecr.your-region.amazonaws.com/trading-bot:latest
```

### 2. 创建 ECS 任务定义和服务

1. 在 ECS 控制台创建任务定义
2. 使用 Fargate 启动类型
3. 配置容器使用 ECR 中的镜像
4. 配置环境变量（通过 Secrets Manager 或 Parameter Store 管理密钥）
5. 创建服务运行任务

---

## 方案三：Lightsail（简单方案）

1. 进入 Lightsail 控制台
2. 创建实例，选择 **Ubuntu**
3. 选择实例计划
4. 启动后连接到实例
5. 按照方案一的步骤安装 Docker 并运行

---

## 配置环境变量（重要）

确保 `binance_config.env` 包含以下内容：

```env
OKX_API_KEY=your_api_key
OKX_SECRET_KEY=your_secret_key
OKX_PASSPHRASE=your_passphrase
TELEGRAM_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

**安全提示**：
- 不要将 `.env` 文件提交到 Git
- 生产环境建议使用 AWS Secrets Manager 或 Parameter Store
- 限制 EC2 安全组的入站规则

---

## 监控与维护

### 查看日志

```bash
# 实时查看日志
docker compose logs -f

# 查看最近 100 行
docker compose logs --tail=100
```

### 重启服务

```bash
docker compose restart
```

### 更新代码

```bash
# 1. 上传新代码
# 2. 重新构建并启动
docker compose up -d --build
```

### 备份数据

```bash
# 备份数据卷
docker run --rm -v trading-bot_bot-data:/data -v $(pwd):/backup alpine tar czf /backup/bot-data-backup.tar.gz /data
```

---

## 成本估算

| 资源 | 月费用（估算） |
|------|---------------|
| t2.micro EC2 | 免费/约 $10 |
| t3.small EC2 | 约 $15 |
| EBS 存储 (8GB) | 约 $1 |
| 数据传输 | 通常可忽略 |

---

## 安全建议

1. **密钥管理**：使用 AWS Secrets Manager 存储 API 密钥
2. **网络安全**：限制安全组入站规则，使用 VPN 或 SSM Session Manager
3. **定期更新**：保持系统和依赖包更新
4. **监控告警**：配置 CloudWatch 告警监控服务状态
5. **备份策略**：定期备份状态文件和数据

---

## 本地测试

在部署到 AWS 之前，建议先在本地测试：

```bash
# 本地构建并运行
docker compose up -d --build

# 访问 http://localhost:7860 查看界面
```
