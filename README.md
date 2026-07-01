# ESP32 智能语音物联网系统

基于 ESP32 + 小米 MiMo 大模型的语音交互物联网环境监测系统，支持语音控制、环境监测、AI 对话。

## 系统架构

```
┌──────────┐    TCP/MQTT    ┌────────────┐    HTTP     ┌──────────┐
│  ESP32   │ ◄────────────► │  server.py │ ◄─────────► │  Web端   │
│(MicroPython)│              │  (Python)  │             │(浏览器)  │
└──────────┘                └─────┬──────┘             └──────────┘
                                  │
                            ┌─────▼──────┐
                            │  MiMo API  │
                            │ASR/LLM/TTS │
                            └────────────┘
```

## 功能

| 模块 | 说明 |
|------|------|
| **语音交互** | ESP32 录音 → MiMo ASR 语音识别 → LLM 大模型对话 → MiMo TTS 语音合成 → 播放 |
| **环境监测** | 温湿度 (DHT11)、光照 (ADC)、阈值报警 (MQTT + OLED + 蜂鸣器) |
| **设备控制** | 风扇 (PWM)、蜂鸣器、LED、RGB 灯带 (WS2812)，支持语音/网页/小程序控制 |
| **Web 控制台** | 实时传感器数据、AI 聊天、语音输入、设备控制面板 |
| **微信小程序** | 移动端监控与控制（开发中） |

## 项目结构

```
├── esp32/                  # ESP32 MicroPython 固件
│   ├── main.py             # 主程序（语音交互 + 传感器 + MQTT）
│   ├── hardware.py         # 硬件驱动（按键、RGB、蜂鸣器、OLED）
│   ├── config.py           # 引脚与参数配置
│   ├── mqtt_client.py      # MQTT 客户端
│   ├── secrets.py          # WiFi/服务器凭据（不提交）
│   └── ssd1306.py / tm1637.py  # OLED / 数码管驱动
├── server.py               # 统一服务器（TCP 9000 + HTTP 8080 + MQTT 桥接）
├── web/                    # Web 控制台（HTML + JS + CSS）
├── miniapp/                # 微信小程序
├── deploy/                 # 部署脚本（Linux systemd + nginx / Windows）
│   ├── deploy.sh           # Linux 一键部署
│   ├── deploy_win.ps1      # Windows 部署脚本
│   ├── esp-iot.service     # systemd 服务文件
│   └── nginx-iot.conf      # nginx 反向代理配置
├── .env.example            # 环境变量模板
└── xiaomi-mimo-2.5.py      # MiMo API 调用示例
```

## 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 XIAOMI_API_KEY 等
```

### 2. 启动服务器

```bash
pip install python-dotenv  # 可选
python server.py
```

- HTTP 控制台: `http://localhost:8080`
- TCP 语音端口: `9000`
- MQTT 桥接: `localhost:1883`（需运行 Mosquitto）

### 3. 烧录 ESP32

1. 安装 [MicroPython](https://micropython.org/download/ESP32_GENERIC/) 固件到 ESP32
2. 复制 `esp32/secrets.example.py` → `esp32/secrets.py`，填入 WiFi 和服务器地址
3. 使用 Thonny 或 `mpremote` 将 `esp32/` 目录下的文件上传到 ESP32

### 4. Linux 部署（云端/树莓派）

```bash
sudo bash deploy/deploy.sh
```

自动完成：创建 systemd 服务 + nginx 反向代理 + SSL 配置。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `XIAOMI_API_KEY` | 小米 MiMo API 密钥 | - |
| `TCP_HOST` | TCP 监听地址 | `0.0.0.0` |
| `TCP_PORT` | TCP 语音端口 | `9000` |
| `HTTP_PORT` | HTTP 控制台端口 | `8080` |
| `MQTT_BRIDGE_HOST` | MQTT 桥接地址 | `127.0.0.1` |
| `MQTT_BRIDGE_PORT` | MQTT 桥接端口 | `1883` |
| `DEFAULT_CITY` | 天气查询默认城市 | `广州` |

## 硬件接线

| ESP32 引脚 | 外设 |
|------------|------|
| GPIO 0 | 按键（短按语音、长按关机） |
| GPIO 2 | WS2812 RGB 灯带 |
| GPIO 4 | I2S 麦克风 (INMP441) |
| GPIO 5 | I2S 喇叭 (MAX98357) |
| GPIO 14 | DHT11 温湿度 |
| GPIO 15 | 蜂鸣器 |
| GPIO 18 | 风扇 PWM |
| GPIO 21 | I2C SDA (OLED) |
| GPIO 22 | I2C SCL (OLED) |
| GPIO 34 | 光照传感器 (ADC) |

## License

MIT
