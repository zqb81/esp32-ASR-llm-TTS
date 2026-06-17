# deploy_win.ps1 — 一键部署 ESP32 IoT 语音助手到腾讯云服务器 (Windows PowerShell 版)
# 用法: .\deploy\deploy_win.ps1

$ErrorActionPreference = "Stop"

$REMOTE_HOST = "tencent-vps"
$REMOTE_DIR = "/opt/esp-iot"
# 服务器公网 IP（仅用于部署完成后的提示输出；按实际填写）
if (-not $SERVER_IP) { $SERVER_IP = "YOUR_SERVER_IP" }

Write-Host "==> 同步项目文件到服务器..." -ForegroundColor Green
ssh $REMOTE_HOST "mkdir -p $REMOTE_DIR/logs $REMOTE_DIR/web"

# 同步 server.py
scp server.py "$($REMOTE_HOST):$($REMOTE_DIR)/server.py"

# 同步 web 目录
scp -r web/* "$($REMOTE_HOST):$($REMOTE_DIR)/web/"

# 同步 systemd 服务文件
scp deploy/esp-iot.service "$($REMOTE_HOST):/etc/systemd/system/esp-iot.service"

Write-Host "==> 安装 Python 依赖..." -ForegroundColor Green
ssh $REMOTE_HOST "pip3 install paho-mqtt openai 2>&1 | tail -5"

Write-Host "==> 配置 Nginx..." -ForegroundColor Green
scp deploy/nginx-iot.conf "$($REMOTE_HOST):/etc/nginx/sites-available/iot.lzjqpb.icu"
ssh $REMOTE_HOST "
  ln -sf /etc/nginx/sites-available/iot.lzjqpb.icu /etc/nginx/sites-enabled/iot.lzjqpb.icu
  nginx -t && systemctl reload nginx && echo 'Nginx OK'
"

Write-Host "==> 配置 systemd 服务..." -ForegroundColor Green
ssh $REMOTE_HOST "
  systemctl daemon-reload
  systemctl enable esp-iot
  systemctl restart esp-iot
  sleep 2
  systemctl status esp-iot --no-pager -l | head -20
"

Write-Host "==> 申请 SSL 证书..." -ForegroundColor Green
ssh $REMOTE_HOST "certbot --nginx -d iot.lzjqpb.icu --non-interactive --agree-tos --email admin@lzjqpb.icu 2>&1 || echo 'SSL 申请失败，请先配置 DNS 解析'"

Write-Host "`n==> 部署完成!" -ForegroundColor Green
Write-Host "    HTTP: http://$SERVER_IP:8080"
Write-Host "    域名: http://iot.lzjqpb.icu (DNS 解析后)"
Write-Host "    MQTT WS: ws://iot.lzjqpb.icu/mqtt"
Write-Host ""
Write-Host "查看日志: ssh $REMOTE_HOST 'journalctl -u esp-iot -f'"
