#!/bin/bash
# deploy.sh — 一键部署 ESP32 IoT 语音助手到腾讯云服务器
# 用法: bash deploy.sh

set -e

REMOTE_HOST="tencent-vps"
REMOTE_DIR="/opt/esp-iot"
# 服务器公网 IP（仅用于部署完成后的提示输出；按实际填写）
SERVER_IP="${SERVER_IP:-YOUR_SERVER_IP}"

echo "==> 同步项目文件到服务器..."
ssh "$REMOTE_HOST" "mkdir -p $REMOTE_DIR/logs $REMOTE_DIR/web"

# 同步 server.py
scp server.py "$REMOTE_HOST:$REMOTE_DIR/server.py"

# 同步 web 目录
scp -r web/* "$REMOTE_HOST:$REMOTE_DIR/web/"

# 同步 systemd 服务文件
scp deploy/esp-iot.service "$REMOTE_HOST:/etc/systemd/system/esp-iot.service"

echo "==> 安装 Python 依赖..."
ssh "$REMOTE_HOST" "pip3 install paho-mqtt openai 2>&1 | tail -5"

echo "==> 配置 Nginx..."
scp deploy/nginx-iot.conf "$REMOTE_HOST:/etc/nginx/sites-available/iot.lzjqpb.icu"
ssh "$REMOTE_HOST" "
  ln -sf /etc/nginx/sites-available/iot.lzjqpb.icu /etc/nginx/sites-enabled/iot.lzjqpb.icu
  nginx -t && systemctl reload nginx && echo 'Nginx OK'
"

echo "==> 配置 systemd 服务..."
ssh "$REMOTE_HOST" "
  systemctl daemon-reload
  systemctl enable esp-iot
  systemctl restart esp-iot
  sleep 2
  systemctl status esp-iot --no-pager -l | head -20
"

echo "==> 申请 SSL 证书 (需要先将 iot.lzjqpb.icu DNS 解析到服务器 IP)..."
ssh "$REMOTE_HOST" "certbot --nginx -d iot.lzjqpb.icu --non-interactive --agree-tos --email admin@lzjqpb.icu 2>&1 || echo 'SSL 申请失败，请先配置 DNS 解析'"

echo ""
echo "==> 部署完成!"
echo "    HTTP: http://${SERVER_IP}:8080"
echo "    域名: http://iot.lzjqpb.icu (DNS 解析后)"
echo "    MQTT WS: ws://iot.lzjqpb.icu/mqtt"
echo ""
echo "查看日志: ssh $REMOTE_HOST 'journalctl -u esp-iot -f'"
