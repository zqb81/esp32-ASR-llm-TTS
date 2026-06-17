# ============================================================
#  hardware.py — 硬件驱动封装
#  ESP32（非 S3）+ INMP441 + MAX98357A + LED + 电机 + 传感器
# ============================================================
import gc
import utime
import math
import uasyncio as asyncio
from machine import Pin, PWM, I2S, ADC, Timer
import config

# ============================================================
#  I2S 麦克风  INMP441
# ============================================================
mic_i2s = None


def init_mic():
    global mic_i2s
    # 防止重复初始化导致旧 I2S 外设泄漏
    if mic_i2s is not None:
        try:
            mic_i2s.deinit()
        except Exception:
            pass
    mic_i2s = I2S(
        config.MIC_I2S_ID,
        sck=Pin(config.MIC_SCK),
        ws=Pin(config.MIC_WS),
        sd=Pin(config.MIC_SD),
        mode=I2S.RX,
        bits=config.BITS,
        format=I2S.MONO,
        rate=config.SAMPLE_RATE,
        ibuf=config.BUF_SIZE,
    )
    return mic_i2s


def deinit_mic():
    global mic_i2s
    if mic_i2s:
        try:
            mic_i2s.deinit()
        except Exception:
            pass
        mic_i2s = None


def mic_read(buf):
    """从麦克风读取一帧数据到 buf，返回实际读取字节数"""
    if mic_i2s is None:
        return 0
    return mic_i2s.readinto(buf)


# ============================================================
#  I2S 功放  MAX98357A
# ============================================================
spk_i2s = None


def init_speaker():
    global spk_i2s
    # 防止重复初始化导致旧 I2S 外设泄漏
    if spk_i2s is not None:
        try:
            spk_i2s.deinit()
        except Exception:
            pass
    spk_i2s = I2S(
        config.SPK_I2S_ID,
        sck=Pin(config.SPK_SCK),
        ws=Pin(config.SPK_WS),
        sd=Pin(config.SPK_SD),
        mode=I2S.TX,
        bits=config.BITS,
        format=I2S.MONO,
        rate=config.SAMPLE_RATE,
        ibuf=config.BUF_SIZE,
    )
    return spk_i2s


def deinit_speaker():
    global spk_i2s
    if spk_i2s:
        try:
            spk_i2s.deinit()
        except Exception:
            pass
        spk_i2s = None


def speaker_write(data):
    """向喇叭写入 PCM 数据"""
    if spk_i2s is None:
        return 0
    return spk_i2s.write(data)


# ============================================================
#  状态 LED
# ============================================================
led_pin = None


def init_led():
    global led_pin
    led_pin = Pin(config.LED_PIN, Pin.OUT)
    led_pin.value(0)


def led_on():
    if led_pin:
        led_pin.value(1)


def led_off():
    if led_pin:
        led_pin.value(0)


def led_blink(times=3, interval_ms=200):
    """闪烁 LED"""
    import utime
    for _ in range(times):
        led_on()
        utime.sleep_ms(interval_ms)
        led_off()
        utime.sleep_ms(interval_ms)


# ============================================================
#  按键（中断驱动 + 软件消抖）
# ============================================================
btn_pin = None

# 中断标志（ISR 中只写基本类型，不做内存分配）
btn_pressed = False   # True = 按键当前处于按下状态
btn_released = False   # True = 短按松开（单次消费）
btn_long_press = False   # True = 长按松开（单次消费）
btn_press_start_ms = 0   # 按下时刻时间戳
btn_last_irq_ms = 0   # 上次中断时间戳（消抖用）
_DEBOUNCE_MS = 50        # 消抖窗口


def _btn_irq_handler(pin):
    """中断回调（在 ISR 上下文执行，禁止分配内存）"""
    global btn_pressed, btn_released, btn_long_press
    global btn_press_start_ms, btn_last_irq_ms
    now = utime.ticks_ms()
    if utime.ticks_diff(now, btn_last_irq_ms) < _DEBOUNCE_MS:
        return  # 消抖：忽略窗口内的重复触发
    btn_last_irq_ms = now

    if pin.value() == 0:        # 按下（低电平有效）
        btn_pressed = True
        btn_released = False
        btn_long_press = False
        btn_press_start_ms = now
    else:                       # 松开
        btn_pressed = False
        duration = utime.ticks_diff(now, btn_press_start_ms)
        if duration >= config.BTN_LONG_PRESS_MS:
            btn_long_press = True   # 长按
        else:
            btn_released = True     # 短按


def init_button():
    global btn_pin
    btn_pin = Pin(config.BUTTON_PIN, Pin.IN, Pin.PULL_UP)
    # 双边沿触发：按下 + 松开 都能捕获
    btn_pin.irq(trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING,
                handler=_btn_irq_handler)


def is_button_pressed():
    """按键当前是否按下"""
    return btn_pressed


def consume_button_release():
    """
    消费一次短按松开事件。
    返回 True 表示自上次调用以来发生了短按松开。
    """
    global btn_released
    if btn_released:
        btn_released = False
        return True
    return False


def consume_long_press():
    """
    消费一次长按事件。
    返回 True 表示自上次调用以来发生了长按松开。
    """
    global btn_long_press
    if btn_long_press:
        btn_long_press = False
        return True
    return False


def clear_button_state():
    """清除所有按键标志（录音开始前调用）"""
    global btn_pressed, btn_released, btn_long_press
    btn_pressed = False
    btn_released = False
    btn_long_press = False


# ============================================================
#  电机 (PWM)
# ============================================================
motor_pwm = None


def init_motor():
    global motor_pwm
    motor_pwm = PWM(Pin(config.MOTOR_PIN), freq=config.MOTOR_FREQ)
    motor_pwm.duty(0)


def motor_on(speed=1023):
    """speed: 0~1023"""
    if motor_pwm:
        motor_pwm.duty(min(max(speed, 0), 1023))
        _start_motor_safety_timer()


def motor_off():
    if motor_pwm:
        motor_pwm.duty(0)
    _stop_motor_safety_timer()


# ---- 电机安全自动关闭定时器 ----
_motor_safety_timer = None


def _motor_safety_cb(timer):
    """电机超时自动关闭（Timer 回调）"""
    print("[SAFETY] 电机超时自动关闭!")
    motor_off()


def _start_motor_safety_timer():
    global _motor_safety_timer
    _stop_motor_safety_timer()
    _motor_safety_timer = Timer(1)
    _motor_safety_timer.init(
        period=config.MOTOR_TIMEOUT_MS,
        mode=Timer.ONE_SHOT,
        callback=_motor_safety_cb
    )


def _stop_motor_safety_timer():
    global _motor_safety_timer
    if _motor_safety_timer:
        _motor_safety_timer.deinit()
        _motor_safety_timer = None


# ============================================================
#  RGB LED (WS2812 / NeoPixel)
# ============================================================
np_instance = None

# ---- RGB 颜色定义 ----
COLOR_RED = (255, 0, 0)
COLOR_ORANGE = (255, 127, 0)
COLOR_YELLOW = (255, 255, 0)
COLOR_GREEN = (0, 255, 0)
COLOR_CYAN = (0, 255, 255)
COLOR_BLUE = (0, 0, 255)
COLOR_PURPLE = (139, 0, 255)
COLOR_PINK = (255, 192, 203)
COLOR_WHITE = (255, 255, 255)
COLOR_OFF = (0, 0, 0)
RAINBOW_COLORS = [COLOR_RED, COLOR_ORANGE, COLOR_YELLOW, COLOR_GREEN, COLOR_CYAN, COLOR_BLUE, COLOR_PURPLE]

# 后台动画任务句柄
_current_anim_task = None

# RGB 亮度控制变量 (0-100)
rgb_brightness = 100
_last_static_rgb = (0, 0, 10)  # 默认微蓝


def init_rgb():
    global np_instance
    try:
        import neopixel
        np_instance = neopixel.NeoPixel(Pin(config.RGB_PIN), config.RGB_NUM)
    except Exception as e:
        print("[WARN] NeoPixel 初始化失败:", e)
        np_instance = None


def set_rgb_brightness(val):
    global rgb_brightness
    rgb_brightness = max(0, min(100, int(val)))
    if _current_anim_task is None:
        rgb_set(_last_static_rgb[0], _last_static_rgb[1], _last_static_rgb[2])


def rgb_set(r, g, b):
    """设置 RGB 颜色 (0-255)，整串灯带统一显示同一颜色 (应用当前亮度缩放)"""
    if np_instance:
        factor = rgb_brightness / 100.0
        color = (int(r * factor), int(g * factor), int(b * factor))
        for i in range(len(np_instance)):
            np_instance[i] = color
        np_instance.write()


def rgb_off():
    set_rgb_static(0, 0, 0)


def rgb_color(name):
    """快捷设置静态颜色：idle, listening, thinking, speaking, error (自动关闭动画)"""
    stop_animation()
    colors = {
        "idle":      (0, 0, 10),      # 微蓝
        "listening": (0, 80, 0),      # 绿色
        "thinking":  (80, 80, 0),     # 黄色
        "speaking":  (0, 0, 80),      # 蓝色
        "error":     (80, 0, 0),      # 红色
    }
    r, g, b = colors.get(name, (0, 0, 0))
    global _last_static_rgb
    _last_static_rgb = (r, g, b)
    rgb_set(r, g, b)



def rgb_demo(delay_ms=150):
    """按彩灯示例循环点亮整串 RGB 灯 (同步)"""
    if np_instance is None:
        return
    for r, g, b in RAINBOW_COLORS:
        rgb_set(r, g, b)
        utime.sleep_ms(delay_ms)


def rgb_welcome_sync(delay_ms=80):
    """同步欢迎启动动画，在 init_all 阶段立即点亮 (不依赖 asyncio 循环)"""
    if np_instance is None:
        return
    # 1. 依次点亮五彩
    colors = [COLOR_RED, COLOR_ORANGE, COLOR_YELLOW, COLOR_GREEN, COLOR_BLUE]
    for i in range(len(np_instance)):
        np_instance[i] = colors[i % len(colors)]
        np_instance.write()
        utime.sleep_ms(delay_ms)
    # 2. 闪烁两次白色
    for _ in range(2):
        rgb_set(0, 0, 0)
        utime.sleep_ms(100)
        rgb_set(100, 100, 100)
        utime.sleep_ms(150)
    # 3. 渐变关闭
    for val in range(100, -1, -10):
        factor = val / 100.0
        rgb_set(int(100 * factor), int(100 * factor), int(100 * factor))
        utime.sleep_ms(15)


# ============================================================
#  异步后台动画协程与任务控制器
# ============================================================
async def _anim_rainbow_cycle_co(speed_ms=80):
    """彩虹循环渐变动画"""
    if np_instance is None:
        return
    step = 0
    num_colors = len(RAINBOW_COLORS)
    while True:
        for i in range(len(np_instance)):
            color_idx = (step + i) % num_colors
            np_instance[i] = RAINBOW_COLORS[color_idx]
        np_instance.write()
        step += 1
        await asyncio.sleep_ms(speed_ms)


async def _anim_welcome_co():
    """欢迎启动动画的后台异步版本"""
    if np_instance is None:
        return
    colors = [COLOR_RED, COLOR_ORANGE, COLOR_YELLOW, COLOR_GREEN, COLOR_BLUE]
    for i in range(len(np_instance)):
        np_instance[i] = colors[i % len(colors)]
        np_instance.write()
        await asyncio.sleep_ms(150)
    for _ in range(2):
        rgb_set(0, 0, 0)
        await asyncio.sleep_ms(100)
        rgb_set(100, 100, 100)
        await asyncio.sleep_ms(150)
    for val in range(100, -1, -5):
        factor = val / 100.0
        rgb_set(int(100 * factor), int(100 * factor), int(100 * factor))
        await asyncio.sleep_ms(20)


async def _anim_pulse_co(r, g, b, speed_ms=15):
    """双脉冲心跳效果"""
    while True:
        # 第一拍：强脉冲
        for val in range(10, 256, 15):
            factor = val / 255.0
            rgb_set(int(r * factor), int(g * factor), int(b * factor))
            await asyncio.sleep_ms(speed_ms)
        for val in range(255, 9, -20):
            factor = val / 255.0
            rgb_set(int(r * factor), int(g * factor), int(b * factor))
            await asyncio.sleep_ms(speed_ms)
        
        # 第二拍：弱脉冲
        for val in range(10, 120, 15):
            factor = val / 255.0
            rgb_set(int(r * 0.3 * factor), int(g * 0.3 * factor), int(b * 0.3 * factor))
            await asyncio.sleep_ms(speed_ms)
        for val in range(120, 9, -20):
            factor = val / 255.0
            rgb_set(int(r * 0.3 * factor), int(g * 0.3 * factor), int(b * 0.3 * factor))
            await asyncio.sleep_ms(speed_ms)
        
        # 心跳停顿
        await asyncio.sleep_ms(600)


async def _anim_alarm_flash_co(r, g, b, interval_ms=200):
    """高频报警闪烁"""
    while True:
        rgb_set(r, g, b)
        await asyncio.sleep_ms(interval_ms)
        rgb_set(0, 0, 0)
        await asyncio.sleep_ms(interval_ms)


async def _anim_breath_co(r, g, b, speed_ms=30):
    """基于正弦波的平滑呼吸灯"""
    angle = 0.0
    while True:
        factor = (math.sin(angle) + 1.0) / 2.0
        # 留有 5% 的微光底色，不彻底熄灭
        factor = 0.05 + 0.95 * factor
        rgb_set(int(r * factor), int(g * factor), int(b * factor))
        angle += 0.06
        if angle > 2 * math.pi:
            angle -= 2 * math.pi
        await asyncio.sleep_ms(speed_ms)


def stop_animation():
    """安全停止正在运行的后台动画"""
    global _current_anim_task
    if _current_anim_task is not None:
        try:
            _current_anim_task.cancel()
        except Exception:
            pass
        _current_anim_task = None


def start_animation(coro_func, *args, **kwargs):
    """在后台启动异步动画，自动取消前一个任务"""
    global _current_anim_task
    stop_animation()
    if np_instance is None:
        return
    try:
        loop = asyncio.get_event_loop()
        _current_anim_task = loop.create_task(coro_func(*args, **kwargs))
    except Exception as e:
        print("[WARN] 无法启动后台动画:", e)


def set_rgb_static(r, g, b):
    """设置静态颜色并关闭动画"""
    global _last_static_rgb
    stop_animation()
    _last_static_rgb = (r, g, b)
    rgb_set(r, g, b)



def set_rgb_animation(name, *args, **kwargs):
    """开开启协程动画"""
    if name == "rainbow":
        # rainbow cycle only accepts speed_ms (if any was sent)
        speed = kwargs.get("speed_ms", 80)
        start_animation(_anim_rainbow_cycle_co, speed_ms=speed)
    elif name == "welcome":
        # welcome takes 0 arguments
        start_animation(_anim_welcome_co)
    elif name == "pulse":
        start_animation(_anim_pulse_co, *args, **kwargs)
    elif name == "alarm":
        start_animation(_anim_alarm_flash_co, *args, **kwargs)
    elif name == "breath":
        start_animation(_anim_breath_co, *args, **kwargs)
    else:
        print("[WARN] 未知动画:", name)


# ============================================================
#  温湿度传感器 (DHT22 / DHT11)
# ============================================================
dht_sensor = None


def init_dht():
    global dht_sensor
    try:
        import dht
        if config.DHT_TYPE == 22:
            dht_sensor = dht.DHT22(Pin(config.DHT_PIN))
        else:
            dht_sensor = dht.DHT11(Pin(config.DHT_PIN))
    except Exception as e:
        print("[WARN] DHT 初始化失败:", e)


def read_dht():
    """返回 (temperature, humidity) 或 (None, None)"""
    if dht_sensor is None:
        return None, None
    try:
        dht_sensor.measure()
        return dht_sensor.temperature(), dht_sensor.humidity()
    except Exception:
        return None, None


# ---- 传感器定时采集（缓存值，避免录音时阻塞读取 DHT 的 2s 延迟）----
_cached_temp = None
_cached_humi = None
_cached_light = None
_sensor_timer = None
_sensor_read_flag = False  # 定时器设置，主循环读取 DHT
_dht_ok = False   # DHT 传感器是否在线
_light_ok = False  # 光敏传感器是否在线


def _sensor_timer_cb(timer):
    """定时器回调：设置读取标志（不在此读 DHT，因为 DHT 会关中断 2s）"""
    global _sensor_read_flag, _cached_light, _light_ok
    _sensor_read_flag = True
    # 光敏传感器可以安全在回调中读（ADC 很快）
    if light_adc is not None:
        try:
            _cached_light = light_adc.read()
            _light_ok = True
        except Exception:
            _light_ok = False


def poll_sensor_if_needed():
    """
    在主循环中调用，检查是否需要执行 DHT 读取。
    DHT 会关中断 ~2s，所以必须在主循环中执行而非定时器回调。
    """
    global _sensor_read_flag, _cached_temp, _cached_humi, _dht_ok
    if _sensor_read_flag:
        _sensor_read_flag = False
        if dht_sensor is not None:
            try:
                dht_sensor.measure()
                _cached_temp = dht_sensor.temperature()
                _cached_humi = dht_sensor.humidity()
                _dht_ok = True
            except Exception:
                _dht_ok = False


def get_cached_sensors():
    """获取缓存的传感器数据（录音结束时调用）"""
    return _cached_temp, _cached_humi, _cached_light


def get_device_status():
    """返回所有外设的在线状态字典（用于 MQTT 上报）"""
    return {
        "dht": _dht_ok and dht_sensor is not None,
        "light": _light_ok and light_adc is not None,
        "motor": motor_pwm is not None,
        "rgb": np_instance is not None,
        "oled": oled is not None,
        "buzzer": buzzer_pwm is not None,
    }


def start_sensor_timer():
    """启动传感器定时采集"""
    global _sensor_timer
    stop_sensor_timer()
    _sensor_timer = Timer(2)
    _sensor_timer.init(
        period=config.SENSOR_INTERVAL_MS,
        mode=Timer.PERIODIC,
        callback=_sensor_timer_cb
    )


def stop_sensor_timer():
    global _sensor_timer
    if _sensor_timer:
        _sensor_timer.deinit()
        _sensor_timer = None


# ============================================================
#  光敏传感器 (ADC)
# ============================================================
light_adc = None


def init_light_sensor():
    global light_adc
    light_adc = ADC(Pin(config.LIGHT_SENSOR_PIN), atten=ADC.ATTN_11DB)


def read_light():
    """返回 0~4095 光照值"""
    if light_adc is None:
        return None
    return light_adc.read()


# ---- WiFi 看门狗定时器 ----
_wifi_timer = None
_wifi_lost = False
_wifi_last_check_ms = 0


def _wifi_check_cb(timer):
    """WiFi 状态检查（Timer 回调，仅设标志，实际重连在主循环）"""
    global _wifi_lost, _wifi_last_check_ms
    import network
    try:
        sta = network.WLAN(network.STA_IF)
        if not sta.isconnected():
            _wifi_lost = True
        else:
            _wifi_lost = False
    except Exception:
        _wifi_lost = True


def start_wifi_watchdog():
    """启动 WiFi 看门狗"""
    global _wifi_timer
    stop_wifi_watchdog()
    _wifi_timer = Timer(3)
    _wifi_timer.init(
        period=config.WIFI_CHECK_INTERVAL_MS,
        mode=Timer.PERIODIC,
        callback=_wifi_check_cb
    )


def stop_wifi_watchdog():
    global _wifi_timer
    if _wifi_timer:
        _wifi_timer.deinit()
        _wifi_timer = None


def is_wifi_lost():
    """WiFi 是否断开（消费型读取）"""
    global _wifi_lost
    if _wifi_lost:
        _wifi_lost = False
        return True
    return False


# ============================================================
#  OLED 显示屏 (SSD1306 I2C)
# ============================================================
oled = None


def recover_i2c(scl_pin_num, sda_pin_num):
    """
    I2C 总线恢复程序：如果从机在传输中途断开连接，可能导致 SDA 被从机拉低，
    此时主控重新初始化也无法正常通信。通过手动对 SCL 产生最多 9 个时钟脉冲，
    可以让从机释放 SDA，然后发送一个 STOP 信号恢复总线。
    """
    try:
        scl = Pin(scl_pin_num, Pin.OUT, value=1)
        sda = Pin(sda_pin_num, Pin.IN, Pin.PULL_UP)
        
        # 如果 SDA 为低电平，说明总线被挂起，进行恢复
        if sda.value() == 0:
            print("[I2C] 检测到 SDA 被拉低，尝试发送时钟脉冲恢复总线...")
            for _ in range(9):
                scl.value(0)
                utime.sleep_us(10)
                scl.value(1)
                utime.sleep_us(10)
                if sda.value() == 1:
                    print("[I2C] SDA 已释放，总线恢复成功 ✓")
                    break
            
            # 生成 STOP 信号：当 SCL 为高电平时，SDA 由低变高
            scl.value(1)
            sda = Pin(sda_pin_num, Pin.OUT, value=0)
            utime.sleep_us(10)
            sda.value(1)
            utime.sleep_us(10)
            # 设回输入拉高状态
            sda = Pin(sda_pin_num, Pin.IN, Pin.PULL_UP)
    except Exception as e:
        print(f"[I2C] 总线恢复异常: {e}")


def init_oled():
    global oled
    try:
        # 首先尝试总线恢复，确保 SDA 不被从机锁死
        recover_i2c(config.OLED_SCL, config.OLED_SDA)
        
        from ssd1306 import SSD1306_I2C
        from machine import SoftI2C
        
        # 降频至 100kHz，增加信号传输的容错与抗干扰性
        i2c = SoftI2C(scl=Pin(config.OLED_SCL),
                      sda=Pin(config.OLED_SDA), freq=100000)
        
        # 扫描 I2C 设备，若 0x3C 不在线则直接返回 None，避免初始化异常
        devices = i2c.scan()
        if 0x3C not in devices:
            print("[OLED] 未扫描到 OLED 设备 (0x3C)")
            oled = None
            return
            
        oled = SSD1306_I2C(config.OLED_WIDTH, config.OLED_HEIGHT, i2c)
        oled.fill(0)
        oled.show()
        print("[OLED] 初始化成功 ✓")
    except Exception as e:
        print(f"[OLED] 初始化失败: {e}")
        oled = None


def oled_display(temp, humi, light, wifi_ok, mqtt_ok, alerts):
    """
    OLED 全屏刷新：
    行0: 网络状态
    行1: 温度 + 湿度
    行2: 光照
    行3: 报警信息
    """
    global oled
    if oled is None:
        init_oled()
        if oled is None:
            return
    try:
        oled.fill(0)
        # 网络状态
        net = ("W" if wifi_ok else "-") + " " + ("M" if mqtt_ok else "-")
        oled.text(f"[{net}] Voice+IoT", 0, 0)
        # 温度 + 湿度
        t = f"T:{temp}C" if temp is not None else "T:--"
        h = f"H:{humi}%" if humi is not None else "H:--"
        oled.text(f"{t}  {h}", 0, 16)
        # 光照
        l = f"L:{light}" if light is not None else "L:--"
        oled.text(l, 0, 32)
        # 报警
        if alerts:
            oled.text("!! " + alerts[0][:12], 0, 48)
        else:
            oled.text("OK Normal", 0, 48)
        oled.show()
    except Exception as e:
        print("[WARN] OLED 刷新失败，尝试重置 OLED 并重新初始化:", e)
        oled = None
        try:
            init_oled()
        except Exception:
            pass


def oled_msg(text, ms=2000):
    """OLED 中央短暂消息"""
    global oled
    if oled is None:
        init_oled()
        if oled is None:
            utime.sleep_ms(ms)
            return
    try:
        oled.fill(0)
        x = max(0, (config.OLED_WIDTH - len(text) * 8) // 2)
        y = (config.OLED_HEIGHT - 8) // 2
        oled.text(text, x, y)
        oled.show()
    except Exception as e:
        print("[WARN] OLED 消息显示失败，尝试重新初始化:", e)
        oled = None
        try:
            init_oled()
        except Exception:
            pass
    utime.sleep_ms(ms)


# ============================================================
#  蜂鸣器 (PWM)
# ============================================================
buzzer_pwm = None


def init_buzzer():
    global buzzer_pwm
    buzzer_pwm = PWM(Pin(config.BUZZER_PIN), freq=config.BUZZER_FREQ)
    buzzer_pwm.duty(0)


def buzzer_on():
    if buzzer_pwm:
        buzzer_pwm.freq(config.BUZZER_FREQ)
        buzzer_pwm.duty(512)


def buzzer_off():
    if buzzer_pwm:
        buzzer_pwm.duty(0)


def buzzer_beep(times=3):
    """蜂鸣器响 times 次"""
    for _ in range(times):
        buzzer_on()
        utime.sleep_ms(config.BUZZER_ON_MS)
        buzzer_off()
        utime.sleep_ms(config.BUZZER_OFF_MS)


# ============================================================
#  一键初始化全部硬件
# ============================================================
def init_all():
    print("[HW] 初始化 LED...")
    init_led()
    print("[HW] 初始化按键...")
    init_button()
    print("[HW] 初始化 RGB...")
    init_rgb()
    try:
        rgb_welcome_sync(80)
    except Exception as e:
        print("[WARN] RGB 欢迎动画失败:", e)
    print("[HW] 初始化电机/风扇...")
    init_motor()
    print("[HW] 初始化温湿度...")
    init_dht()
    print("[HW] 初始化光敏...")
    init_light_sensor()
    print("[HW] 初始化 OLED...")
    init_oled()
    print("[HW] 初始化蜂鸣器...")
    init_buzzer()
    print("[HW] 全部初始化完成 ✓")
    gc.collect()
