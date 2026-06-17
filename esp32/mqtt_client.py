# ============================================================
#  mqtt_client.py — MQTT 云端通信模块
#  连接 Broker、发布传感器数据、订阅云端指令、回调处理
# ============================================================
import json
import utime
import config

# ---- MQTT 连接状态 ----
_mqtt = None
_mqtt_connected = False
_last_publish_ms = 0
_cmd_callback = None          # 外部注册的指令回调函数


def is_connected():
    return _mqtt_connected


# ============================================================
#  连接 & 断开
# ============================================================
def connect(action_callback=None):
    """
    连接 MQTT Broker。
    action_callback(topic, payload) 在收到指令时被调用。
    """
    global _mqtt, _mqtt_connected, _cmd_callback
    _cmd_callback = action_callback

    try:
        from umqtt.simple import MQTTClient

        _mqtt = MQTTClient(
            config.MQTT_CLIENT_ID,
            config.MQTT_BROKER,
            port=config.MQTT_PORT,
            keepalive=config.MQTT_KEEPALIVE,
        )
        _mqtt.set_callback(_on_message)
        _mqtt.connect()
        _mqtt_connected = True

        # 订阅所有指令主题
        _mqtt.subscribe(config.TOPIC_CMD)
        # 订阅网页语音输入主题
        _mqtt.subscribe(config.TOPIC_VOICE_IN)

        # 发布上线状态
        publish(config.TOPIC_STATUS, '{"state":"online"}')
        print(f"[MQTT] 已连接 {config.MQTT_BROKER}:{config.MQTT_PORT} ✓")
        return True

    except Exception as e:
        print(f"[MQTT] 连接失败: {e}")
        _mqtt_connected = False
        return False


def disconnect():
    global _mqtt, _mqtt_connected
    if _mqtt and _mqtt_connected:
        try:
            publish(config.TOPIC_STATUS, '{"state":"offline"}')
            _mqtt.disconnect()
        except Exception:
            pass
    _mqtt_connected = False
    _mqtt = None


# ============================================================
#  发布
# ============================================================
def publish(topic, payload):
    """发布消息到主题"""
    global _mqtt_connected
    if not _mqtt or not _mqtt_connected:
        return False
    try:
        _mqtt.publish(topic, payload)
        return True
    except Exception as e:
        print(f"[MQTT] 发布失败: {e}")
        _mqtt_connected = False
        return False


def publish_sensor_data(temp, humi, light):
    """发布传感器数据 + 设备在线状态"""
    import hardware as hw
    data = {
        "temperature": temp,
        "humidity": humi,
        "light": light,
        "devices": hw.get_device_status(),
        "ts": utime.time(),
    }
    return publish(config.TOPIC_SENSOR, json.dumps(data))


def publish_alert(alert_type, value, threshold):
    """发布阈值报警"""
    data = {
        "type": alert_type,
        "value": value,
        "threshold": threshold,
        "ts": utime.time(),
    }
    return publish(config.TOPIC_ALERT, json.dumps(data))


def publish_state(device, state_dict):
    """
    发布设备状态回执 (retain=True)。
    新客户端连接后立即获得最新状态，实现多端同步。
    例如: publish_state('fan', {'state':'on','ts':12345})
    """
    global _mqtt_connected
    if not _mqtt or not _mqtt_connected:
        return False
    topic = config.TOPIC_STATE_PREFIX + device
    payload = json.dumps(state_dict)
    try:
        _mqtt.publish(topic, payload, retain=True)
        return True
    except Exception as e:
        print(f"[MQTT] 状态回执发布失败: {e}")
        _mqtt_connected = False
        return False


def publish_periodic(temp, humi, light):
    """
    按 PUBLISH_INTERVAL_MS 间隔发布传感器数据。
    在主循环中调用，自动判断是否到了发布时间。
    返回 True 表示已发布。
    """
    global _last_publish_ms
    now = utime.ticks_ms()
    if utime.ticks_diff(now, _last_publish_ms) >= config.PUBLISH_INTERVAL_MS:
        _last_publish_ms = now
        return publish_sensor_data(temp, humi, light)
    return False


# ============================================================
#  非阻塞消息处理
# ============================================================
def check_messages():
    """
    非阻塞检查 MQTT 消息。
    在主循环的空闲时段调用，处理收到的订阅消息。
    """
    global _mqtt_connected
    if not _mqtt or not _mqtt_connected:
        return
    try:
        _mqtt.check_msg()
    except Exception as e:
        print(f"[MQTT] 消息检查失败: {e}")
        _mqtt_connected = False


# ============================================================
#  内部回调：收到订阅消息时触发
# ============================================================
def _on_message(topic_bytes, payload_bytes):
    """
    MQTT 订阅回调。
    解析主题和载荷，转发给外部 action_callback。

    主题格式: esp32/iot/cmd/<command>  或  esp32/iot/voice/input
    """
    try:
        topic = topic_bytes.decode()
        payload = payload_bytes.decode().strip()
        print(f"[MQTT] 收到指令: {topic} = {payload}")

        # 网页语音输入单独处理
        if topic == config.TOPIC_VOICE_IN:
            if _cmd_callback:
                _cmd_callback("voice_input", payload)
            return

        # 从主题中提取指令名
        cmd_name = topic.split("/")[-1]

        if _cmd_callback:
            _cmd_callback(cmd_name, payload)

    except Exception as e:
        print(f"[MQTT] 回调解析失败: {e}")
