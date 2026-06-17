# ============================================================
#  config.py — ESP32 引脚定义 & 全局配置
#  语音助手 + IoT 环境监测 + MQTT 云端控制
#  ★ 芯片: ESP32（非 S3），GPIO 0~39
# ============================================================

# ---------- 密钥 / 私有配置 ----------
# 真实值放在 secrets.py（已 gitignore，不入库）；缺失时回退占位符，
# 便于首次烧录前一眼看出需要填写。复制 esp32/secrets.example.py 即可。
try:
    from secrets import WIFI_SSID, WIFI_PASSWORD, SERVER_IP, MQTT_BROKER
except ImportError:
    WIFI_SSID = "YOUR_WIFI_SSID"
    WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"
    SERVER_IP = "YOUR_SERVER_IP"
    MQTT_BROKER = "YOUR_MQTT_BROKER"

# ---------- PC 语音服务器 ----------
SERVER_PORT = 9000

# ---------- MQTT Broker ----------
MQTT_PORT = 1883
MQTT_CLIENT_ID = "esp32_voice_iot_001"
MQTT_KEEPALIVE = 60

# MQTT 主题
TOPIC_SENSOR = "esp32/iot/sensor"       # 传感器数据（发布）
TOPIC_STATUS = "esp32/iot/status"       # 设备在线状态（发布）
TOPIC_ALERT = "esp32/iot/alert"        # 阈值报警（发布）
TOPIC_CMD = "esp32/iot/cmd/#"        # 指令（订阅，通配符）
TOPIC_CMD_FAN = "esp32/iot/cmd/fan"      # 风扇 on/off
TOPIC_CMD_LED = "esp32/iot/cmd/led"      # LED brightness 0~100
TOPIC_CMD_THRESH = "esp32/iot/cmd/threshold"  # 温度阈值 temp=30
TOPIC_CMD_BUZZ = "esp32/iot/cmd/buzzer"   # 蜂鸣器 on/off
TOPIC_CMD_POLL = "esp32/iot/cmd/poll"     # 立即上报传感器数据

# ---------- 状态回执主题 (多端同步) ----------
TOPIC_STATE_PREFIX = "esp32/iot/state/"     # 状态回执前缀 (retain=True)

# ---------- 网页语音对话 ----------
TOPIC_VOICE_IN = "esp32/iot/voice/input"   # 网页语音输入文本（ESP32 订阅）
TOPIC_VOICE_OUT = "esp32/iot/voice/output"  # AI 回复文本（ESP32 发布）

# ---------- I2S 麦克风 INMP441 ----------
MIC_SCK = 14       # BCLK
MIC_WS = 33        # LRCLK（15 号脚留给电机）
MIC_SD = 32        # DOUT
MIC_I2S_ID = 0

# ---------- I2S 功放 MAX98357A ----------
# ★ ESP32 的 GPIO6~11 是 Flash 引脚，不可用！
SPK_SCK = 25       # BCLK
SPK_WS = 26        # LRCLK
SPK_SD = 27        # DIN
SPK_I2S_ID = 1

# ---------- 音频参数 ----------
SAMPLE_RATE = 16000
BITS = 16
CHANNELS = 1
BUF_SIZE = 4096
CHUNK_SIZE = 2048
MAX_RECORD_SEC = 10

# ---------- 按键 ----------
BUTTON_PIN = 22         # BOOT 键，按下低电平
BTN_LONG_PRESS_MS = 3000

# ---------- 状态 LED ----------
LED_PIN = 2            # 板载 LED

# ---------- 电机/风扇 (PWM) ----------
MOTOR_PIN = 15         # 15 号脚接电机/风扇
MOTOR_FREQ = 50
MOTOR_DUTY = 512
MOTOR_TIMEOUT_MS = 60000

# ---------- RGB LED (WS2812) ----------
RGB_PIN = 16           # 16 号脚接 RGB 灯带
RGB_NUM = 5            # 5 颗 RGB LED

# ---------- 传感器 ----------
LIGHT_SENSOR_PIN = 34  # ADC 光敏（GPIO34 仅输入，适合 ADC）
DHT_PIN = 4            # DHT11/22 温湿度
DHT_TYPE = 22
SENSOR_INTERVAL_MS = 5000

# ---------- OLED 显示屏 (SSD1306 I2C) ----------
# ★ GPIO12 是启动引脚，避免使用；改用标准 I2C 引脚
OLED_SCL = 18          # I2C 时钟
OLED_SDA = 23          # I2C 数据
OLED_WIDTH = 128
OLED_HEIGHT = 64

# ---------- 蜂鸣器 (PWM) ----------
BUZZER_PIN = 17        # 17 号脚接蜂鸣器
BUZZER_FREQ = 1000
BUZZER_ON_MS = 500
BUZZER_OFF_MS = 500

# ---------- 报警阈值 ----------
TEMP_ALERT = 30     # 温度报警阈值 (°C)
HUMI_ALERT = 80     # 湿度报警阈值 (%)
LIGHT_ALERT_LOW = 500    # 光照过低报警

# ---------- 发布间隔 ----------
PUBLISH_INTERVAL_MS = 10000  # MQTT 传感器发布间隔 10s

# ---------- WiFi 看门狗 ----------
WIFI_CHECK_INTERVAL_MS = 15000
