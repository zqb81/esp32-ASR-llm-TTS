# ============================================================
#  main.py — ESP32（非 S3）主程序 (uasyncio 异步架构版)
#  语音助手 + IoT 环境监测 + MQTT 云端控制
# ============================================================
import gc
import json
import struct
import utime
import uasyncio as asyncio
import config
import hardware as hw
import mqtt_client as mqtt

# ============================================================
#  消息协议定义
# ============================================================
MSG_AUDIO = 0x01    # 音频数据帧
MSG_CMD = 0x02      # JSON 指令帧
MSG_DONE = 0x03     # 结束帧
_MAX_FRAME_LEN = 256 * 1024

# 全局状态与队列
voice_queue = None
voice_active = False
current_voice_state = "idle"  # 记录当前语音状态

_temp_threshold = config.TEMP_ALERT
_humi_threshold = config.HUMI_ALERT
_light_threshold = config.LIGHT_ALERT_LOW

# 异步队列定义（兼容性良好）


class AsyncQueue:
    def __init__(self):
        self._items = []

    def put(self, item):
        self._items.append(item)

    def empty(self):
        return len(self._items) == 0

    async def get(self):
        while not self._items:
            await asyncio.sleep_ms(50)
        return self._items.pop(0)

# ============================================================
#  网络连接辅助
# ============================================================


async def connect_wifi():
    """异步连接 Wi-Fi"""
    import network
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if not sta.isconnected():
        print(f"[WiFi] 连接 {config.WIFI_SSID} ...")
        sta.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        for _ in range(30):
            if sta.isconnected():
                break
            await asyncio.sleep(1)
    if sta.isconnected():
        ip = sta.ifconfig()[0]
        print(f"[WiFi] 已连接 ✓  IP = {ip}")
        return True
    print("[WiFi] 连接失败 ✗")
    return False

# ============================================================
#  异步协议收发
# ============================================================


async def readexactly_async(reader, n):
    """异步精确读取 n 字节，保证多版本 MicroPython 兼容性"""
    buf = bytearray()
    while len(buf) < n:
        chunk = await reader.read(n - len(buf))
        if not chunk:
            raise Exception("Socket EOF")
        buf.extend(chunk)
    return bytes(buf)


async def send_frame_async(writer, frame_type, data):
    """异步发送一帧: type(1B) + length(4B) + data"""
    header = struct.pack(">BI", frame_type, len(data))
    writer.write(header)
    writer.write(data)
    await writer.drain()


async def recv_frame_async(reader):
    """异步接收一帧，返回 (type, data)"""
    try:
        header = await readexactly_async(reader, 5)
    except Exception:
        return None, None

    frame_type, length = struct.unpack(">BI", header)
    if length > _MAX_FRAME_LEN:
        print(f"[ERR] 帧长度异常: {length}，断开连接")
        return None, None

    try:
        data = await readexactly_async(reader, length) if length > 0 else b""
    except Exception:
        return None, None

    return frame_type, data

# ============================================================
#  异步辅助反馈
# ============================================================


async def buzzer_beep_async(times=3):
    """异步蜂鸣器反馈"""
    for _ in range(times):
        hw.buzzer_on()
        await asyncio.sleep_ms(config.BUZZER_ON_MS)
        hw.buzzer_off()
        await asyncio.sleep_ms(config.BUZZER_OFF_MS)


async def led_blink_async(times=5, interval_ms=100):
    """异步LED闪烁"""
    for _ in range(times):
        hw.led_on()
        await asyncio.sleep_ms(interval_ms)
        hw.led_off()
        await asyncio.sleep_ms(interval_ms)

# ============================================================
#  语音流程协程
# ============================================================


async def record_and_upload_async(writer):
    """流式上传麦克风录音"""
    buf = bytearray(config.BUF_SIZE)
    hw.clear_button_state()
    hw.init_mic()
    await asyncio.sleep_ms(100)  # 等麦克风稳定

    print("[REC] 开始录音，松开按键结束...")
    global current_voice_state
    current_voice_state = "listening"
    update_rgb_state()
    hw.led_on()

    # 告诉服务器开始录音
    await send_frame_async(writer, MSG_CMD, json.dumps(
        {"action": "start_recording"}).encode())

    total_bytes = 0
    max_bytes = config.SAMPLE_RATE * 2 * config.MAX_RECORD_SEC
    frame_count = 0

    try:
        while not hw.consume_button_release() and total_bytes < max_bytes:
            n = hw.mic_read(buf)
            if n > 0:
                await send_frame_async(writer, MSG_AUDIO, bytes(buf[:n]))
                total_bytes += n
                frame_count += 1
                if frame_count % 20 == 0:
                    gc.collect()
            await asyncio.sleep_ms(1)  # 释放控制权以让出 CPU
    finally:
        hw.deinit_mic()

    hw.led_off()
    current_voice_state = "thinking"
    update_rgb_state()

    # 定时器缓存传感器数据
    temp, humi, light = hw.get_cached_sensors()
    if temp is None:
        temp, humi = hw.read_dht()
    if light is None:
        light = hw.read_light()
    sensor_data = {
        "action": "stop_recording",
        "temperature": temp,
        "humidity": humi,
        "light": light,
    }
    await send_frame_async(writer, MSG_CMD, json.dumps(sensor_data).encode())
    print(f"[REC] 录音结束，共上传 {total_bytes} 字节")
    gc.collect()


async def receive_response_async(reader):
    """异步接收服务器音频播放及动作执行"""
    actions = []
    hw.init_speaker()
    global current_voice_state
    current_voice_state = "speaking"
    update_rgb_state()
    print("[PLAY] 接收回复...")

    try:
        while True:
            ftype, data = await recv_frame_async(reader)
            if ftype is None or ftype == MSG_DONE:
                break

            if ftype == MSG_AUDIO:
                written = hw.speaker_write(data)
                if written != len(data):
                    print(f"[WARN] I2S 写入不完整: {written}/{len(data)}")
                await asyncio.sleep_ms(1)

            elif ftype == MSG_CMD:
                try:
                    cmd = json.loads(data.decode())
                    print(f"[CMD] 收到指令: {cmd}")
                    actions.append(cmd)

                    if cmd.get("action") == "reply":
                        reply_text = cmd.get("text", "")
                        if reply_text:
                            mqtt.publish(config.TOPIC_VOICE_OUT, reply_text)
                            print(f"[WEB-VOICE] 已发布回复: {reply_text[:50]}")
                    else:
                        execute_action(cmd)
                except Exception as e:
                    print(f"[CMD] 解析失败: {e}")
    finally:
        hw.deinit_speaker()

    print("[PLAY] 播放/执行完毕")
    gc.collect()
    return actions

# ============================================================
#  动作执行逻辑 (支持异步任务调度)
# ============================================================


def execute_action(cmd):
    act = cmd.get("action", "")

    if act in ("motor", "fan"):
        if cmd.get("state") == "on":
            hw.motor_on(cmd.get("speed", config.MOTOR_DUTY))
            print("[ACT] 风扇/电机开启")
        else:
            hw.motor_off()
            print("[ACT] 风扇/电机关闭")

    elif act == "led":
        if cmd.get("state") == "on":
            hw.led_on()
        else:
            hw.led_off()
        print(f"[ACT] LED {cmd.get('state')}")

    elif act == "rgb":
        if "color" in cmd:
            c_name = cmd["color"]
            if c_name in ("rainbow", "welcome", "pulse", "alarm", "breath"):
                hw.set_rgb_animation(c_name, cmd.get(
                    "r", 255), cmd.get("g", 255), cmd.get("b", 255))
            else:
                hw.rgb_color(c_name)
        else:
            hw.set_rgb_static(cmd.get("r", 0), cmd.get(
                "g", 0), cmd.get("b", 0))
        print("[ACT] RGB 已更新")

    elif act == "buzzer":
        if cmd.get("state") == "on":
            asyncio.get_event_loop().create_task(buzzer_beep_async(cmd.get("times", 3)))
        else:
            hw.buzzer_off()
        print(f"[ACT] 蜂鸣器 {cmd.get('state')}")

    else:
        print(f"[ACT] 未知指令: {act}")


def update_rgb_state():
    """根据优先级统一更新 RGB 状态与后台动画"""
    global current_voice_state, voice_active

    # Priority 1: 语音活动状态 (录音、思考、说话等)
    if voice_active:
        if current_voice_state == "listening":
            hw.set_rgb_animation("breath", 0, 120, 0, speed_ms=18)  # 绿光快速呼吸
        elif current_voice_state == "thinking":
            hw.set_rgb_animation("rainbow", speed_ms=80)            # 炫彩彩虹跑马灯
        elif current_voice_state == "speaking":
            hw.set_rgb_animation("pulse", 0, 0, 160, speed_ms=15)    # 蓝光双重脉冲心跳
        elif current_voice_state == "error":
            hw.set_rgb_animation("alarm", 200, 0, 0, interval_ms=150)  # 红光高频快闪
        return

    # Priority 2: WiFi 断线状态
    import network
    sta = network.WLAN(network.STA_IF)
    if not sta.isconnected():
        # 橙色呼吸灯 (表示WiFi连接异常)
        hw.set_rgb_animation("breath", 180, 80, 0, speed_ms=30)
        return

    # Priority 3: 传感器阈值警报
    temp, humi, light = hw.get_cached_sensors()
    if temp is not None and temp >= _temp_threshold:
        hw.set_rgb_animation("alarm", 255, 0, 0, interval_ms=200)   # 温度报警：红光急闪
        return
    if humi is not None and humi >= _humi_threshold:
        hw.set_rgb_animation("alarm", 0, 150, 150,
                             interval_ms=350)  # 湿度报警：青光慢闪
        return
    if light is not None and light < config.LIGHT_ALERT_LOW:
        hw.set_rgb_animation("breath", 120, 80, 0,
                             speed_ms=30)     # 光照过低：环境黄光夜灯呼吸
        return

    # Priority 4: 正常空闲状态
    hw.set_rgb_animation("breath", 0, 0, 20, speed_ms=40)           # 柔和微蓝呼吸


def emergency_shutdown():
    """紧急关闭硬件状态"""
    print("[EMERGENCY] 长按触发，关闭所有设备!")
    hw.motor_off()
    hw.led_off()
    hw.buzzer_off()
    hw.rgb_color("error")
    # 阻塞式运行紧急状态的鸣叫和闪烁是允许的
    hw.buzzer_beep(2)
    hw.led_blink(5, 100)
    hw.rgb_color("idle")

# ============================================================
#  阈值监控与数据发布
# ============================================================


def check_threshold_alerts():
    alerts = []
    temp, humi, light = hw.get_cached_sensors()

    if temp is not None and temp >= _temp_threshold:
        alerts.append(f"T>{_temp_threshold}C")
        hw.motor_on()
        asyncio.get_event_loop().create_task(buzzer_beep_async(1))
        mqtt.publish_alert("temperature", temp, _temp_threshold)

    if humi is not None and humi >= _humi_threshold:
        alerts.append(f"H>{_humi_threshold}%")
        mqtt.publish_alert("humidity", humi, _humi_threshold)

    if light is not None and light < _light_threshold:
        alerts.append("LightLow")
        mqtt.publish_alert("light_low", light, _light_threshold)

    return alerts

# ============================================================
#  MQTT 回调
# ============================================================


def _on_mqtt_cmd(cmd_name, payload):
    global _temp_threshold, _humi_threshold
    print(f"[MQTT-CMD] {cmd_name} = {payload}")
    ts = utime.time()

    if cmd_name == "fan":
        if payload == "off":
            hw.motor_off()
            print("[MQTT-CMD] 风扇已关闭")
            mqtt.publish_state("fan", {"state": "off", "speed": 0, "ts": ts})
        else:
            try:
                # 尝试解析为数字转速 (0~1023)
                speed = int(payload)
                speed = max(0, min(1023, speed))
                hw.motor_on(speed)
                print(f"[MQTT-CMD] 风扇已开启，转速: {speed}")
                mqtt.publish_state("fan", {"state": "on", "speed": speed, "ts": ts})
            except ValueError:
                # 如果是 "on"，按默认转速开启
                if payload == "on":
                    hw.motor_on(config.MOTOR_DUTY)
                    print(f"[MQTT-CMD] 风扇已开启，使用默认转速: {config.MOTOR_DUTY}")
                    mqtt.publish_state("fan", {"state": "on", "speed": config.MOTOR_DUTY, "ts": ts})

    elif cmd_name == "led":
        try:
            brightness = int(payload)
            brightness = max(0, min(100, brightness))
            duty = int(brightness * 1023 / 100)
            if duty > 0:
                hw.led_on()
            else:
                hw.led_off()
            print(f"[MQTT-CMD] LED 亮度: {brightness}%")
            mqtt.publish_state(
                "led", {"state": "on" if duty > 0 else "off", "brightness": brightness, "ts": ts})
        except ValueError:
            print("[MQTT-CMD] LED 亮度值无效")

    elif cmd_name == "threshold":
        try:
            new_thresh = int(payload)
            _temp_threshold = new_thresh
            print(f"[MQTT-CMD] 温度报警阈值已设为: {new_thresh}°C")
            mqtt.publish_state("threshold", {"value": new_thresh, "ts": ts})
        except ValueError:
            print("[MQTT-CMD] 阈值值无效")

    elif cmd_name == "threshold_humi":
        try:
            new_thresh = int(payload)
            _humi_threshold = new_thresh
            print(f"[MQTT-CMD] 湿度报警阈值已设为: {new_thresh}%")
            mqtt.publish_state("threshold_humi", {"value": new_thresh, "ts": ts})
        except ValueError:
            print("[MQTT-CMD] 湿度阈值值无效")

    elif cmd_name == "threshold_light":
        try:
            new_thresh = int(payload)
            _light_threshold = new_thresh
            print(f"[MQTT-CMD] 光照报警阈值已设为: {new_thresh} lx")
            mqtt.publish_state("threshold_light", {"value": new_thresh, "ts": ts})
        except ValueError:
            print("[MQTT-CMD] 光照阈值值无效")

    elif cmd_name == "buzzer":
        if payload == "on":
            asyncio.get_event_loop().create_task(buzzer_beep_async(3))
            print("[MQTT-CMD] 蜂鸣器已响 (异步启动)")
        else:
            hw.buzzer_off()
            print("[MQTT-CMD] 蜂鸣器已停")
        mqtt.publish_state("buzzer", {"state": payload, "ts": ts})

    elif cmd_name == "poll":
        temp, humi, light = hw.get_cached_sensors()
        mqtt.publish_sensor_data(temp, humi, light)
        print("[MQTT-CMD] 已立即上报传感器数据")

    elif cmd_name == "rgb":
        try:
            if isinstance(payload, str):
                p_str = payload.strip()
                if p_str.startswith("{"):
                    cmd = json.loads(p_str)
                else:
                    cmd = {"action": "rgb", "color": p_str}
            else:
                cmd = payload

            if "color" in cmd:
                c_name = cmd["color"]
                if c_name in ("rainbow", "welcome", "pulse", "alarm", "breath"):
                    hw.set_rgb_animation(c_name, cmd.get(
                        "r", 255), cmd.get("g", 255), cmd.get("b", 255))
                else:
                    hw.rgb_color(c_name)
            else:
                hw.set_rgb_static(cmd.get("r", 0), cmd.get(
                    "g", 0), cmd.get("b", 0))
            print("[MQTT-CMD] RGB 已更新")
            rgb_state = dict(cmd)
            rgb_state["ts"] = ts
            mqtt.publish_state("rgb", rgb_state)
        except Exception as e:
            print(f"[MQTT-CMD] RGB 指令解析失败: {e}")

    elif cmd_name == "rgb_brightness":
        try:
            brightness = int(payload)
            brightness = max(0, min(100, brightness))
            hw.set_rgb_brightness(brightness)
            print(f"[MQTT-CMD] RGB 亮度已设为: {brightness}%")
            mqtt.publish_state("rgb_brightness", {"value": brightness, "ts": ts})
        except Exception as e:
            print(f"[MQTT-CMD] RGB 亮度解析失败: {e}")


    elif cmd_name == "voice_input":
        if voice_queue:
            voice_queue.put({"type": "text", "text": payload})
            print(f"[MQTT-CMD] 网页语音输入已入队: {payload}")

    else:
        print(f"[MQTT-CMD] 未知指令: {cmd_name}")

# ============================================================
#  异步任务声明
# ============================================================


async def wifi_watchdog_task():
    """检测 WiFi 状态并自动重连"""
    while True:
        await asyncio.sleep(15)
        if hw.is_wifi_lost():
            print("[WiFi] 检测到断线，开始异步重连...")
            update_rgb_state()  # 触发断网橙色呼吸
            import network
            sta = network.WLAN(network.STA_IF)
            sta.active(True)
            if not sta.isconnected():
                sta.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
                for _ in range(15):
                    if sta.isconnected():
                        break
                    await asyncio.sleep(1)

            if sta.isconnected():
                print(f"[WiFi] 重连成功 ✓ IP = {sta.ifconfig()[0]}")
                # 闪烁绿光两次指示重连成功
                hw.set_rgb_static(0, 120, 0)
                await asyncio.sleep_ms(300)
                hw.set_rgb_static(0, 0, 0)
                await asyncio.sleep_ms(150)
                hw.set_rgb_static(0, 120, 0)
                await asyncio.sleep_ms(300)
                update_rgb_state()
                mqtt.connect(_on_mqtt_cmd)
            else:
                print("[WiFi] 重连失败，等待下次尝试")
                update_rgb_state()


async def mqtt_check_task():
    """极高频轮询检查 MQTT 接收消息"""
    while True:
        if mqtt.is_connected():
            mqtt.check_messages()
        await asyncio.sleep_ms(50)


async def sensor_periodic_task():
    """周期性执行温湿度/光照测量、报警检测、发布及 OLED 渲染"""
    last_publish_ms = 0
    while True:
        await asyncio.sleep(5)
        
        # 触发生命周期读取
        hw.poll_sensor_if_needed()
        temp, humi, light = hw.get_cached_sensors()
        print(f"[SENSOR] temp={temp}C, humi={humi}%, light={light}")

        alerts = check_threshold_alerts()
        update_rgb_state()

        # 定时上报
        now = utime.ticks_ms()
        if utime.ticks_diff(now, last_publish_ms) >= config.PUBLISH_INTERVAL_MS:
            last_publish_ms = now
            if mqtt.is_connected():
                mqtt.publish_sensor_data(temp, humi, light)

        # 刷新 OLED
        hw.oled_display(temp, humi, light, True, mqtt.is_connected(), alerts)


async def button_monitor_task():
    """监听物理按键中断标志"""
    button_was_pressed = False
    while True:
        await asyncio.sleep_ms(20)

        # 紧急长按处理
        if hw.consume_long_press():
            emergency_shutdown()
            hw.clear_button_state()
            button_was_pressed = False
            continue

        pressed = hw.is_button_pressed()
        if pressed and not button_was_pressed:
            # 短按按下沿触发
            if not voice_active and voice_queue:
                voice_queue.put({"type": "mic"})
                print("[BTN] 物理按键录音请求已入队")
        button_was_pressed = pressed


async def voice_processing_task():
    """串行安全消费语音任务（本地录音 或 Web文本代播）"""
    global voice_active, current_voice_state
    while True:
        req = await voice_queue.get()
        voice_active = True

        reader, writer = None, None
        try:
            # 建立异步 TCP 连线
            addr = config.SERVER_IP
            port = config.SERVER_PORT
            print(f"[TCP] 正在异步连接语音服务器 {addr}:{port} ...")
            reader, writer = await asyncio.open_connection(addr, port)
            print("[TCP] 服务器连接成功 ✓")

            if req["type"] == "mic":
                # 本地录音及流式上传
                await record_and_upload_async(writer)
                # 播放及执行控制
                await receive_response_async(reader)
            elif req["type"] == "text":
                # Web文本代播
                cmd = json.dumps(
                    {"action": "web_text", "text": req["text"]}).encode()
                await send_frame_async(writer, MSG_CMD, cmd)
                # 播放及执行控制
                await receive_response_async(reader)

        except Exception as e:
            print(f"[VOICE ERR] 异常: {e}")
            current_voice_state = "error"
            update_rgb_state()
            await led_blink_async(5, 100)
            current_voice_state = "idle"
            update_rgb_state()
        finally:
            if writer:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
            current_voice_state = "idle"
            voice_active = False
            update_rgb_state()
            gc.collect()

# ============================================================
#  异步主协程与入口
# ============================================================


async def main_co():
    global voice_queue
    print("=" * 44)
    print("  🗣️  ESP32 语音+IoT 异步助手")
    print("  语音对话 | MQTT云控 | 异步并发")
    print("=" * 44)

    # 1. 初始化队列
    voice_queue = AsyncQueue()

    # 2. 初始化硬件
    hw.init_all()

    # 3. 连接 Wi-Fi (启动时在后台开始 WiFi 橙色呼吸动画)
    update_rgb_state()
    if not await connect_wifi():
        hw.rgb_color("error")
        return

    # WiFi 连接成功，更新状态为常规空闲微蓝呼吸
    update_rgb_state()

    # 4. 连接 MQTT Broker
    hw.oled_msg("MQTT...", 1000)
    if mqtt.connect(_on_mqtt_cmd):
        hw.oled_msg("MQTT OK!", 1000)
    else:
        hw.oled_msg("MQTT Fail", 2000)
        print("[MQTT] 连接失败，IoT 异步云控暂时不可用")

    # 5. 开启硬件自维持 Timer
    hw.start_sensor_timer()
    hw.start_wifi_watchdog()

    # 首次预热采集与发布
    hw.poll_sensor_if_needed()
    utime.sleep_ms(100)
    hw.poll_sensor_if_needed()
    temp, humi, light = hw.get_cached_sensors()
    mqtt.publish_sensor_data(temp, humi, light)

    # 6. 开启异步多任务调度
    print("[SYS] 启动 asyncio 多任务循环...")
    await asyncio.gather(
        wifi_watchdog_task(),
        mqtt_check_task(),
        sensor_periodic_task(),
        button_monitor_task(),
        voice_processing_task()
    )


def main():
    try:
        asyncio.run(main_co())
    except KeyboardInterrupt:
        print("\n[SYS] 用户键盘中断")
    except Exception as e:
        print(f"[SYS CRITICAL] 核心事件循环崩坏: {e}")
        import sys
        sys.print_exception(e)
    finally:
        # 清理释放硬件状态
        hw.stop_sensor_timer()
        hw.stop_wifi_watchdog()
        mqtt.disconnect()
        hw.motor_off()
        hw.buzzer_off()
        hw.rgb_off()
        hw.led_off()
        print("[SYS] 程序运行安全终结")


if __name__ == "__main__":
    main()
