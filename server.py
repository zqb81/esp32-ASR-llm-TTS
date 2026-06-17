"""
server.py — 统一服务器（TCP + HTTP + MQTT 桥接）
  • TCP 9000  — 接收 ESP32 流式音频 → ASR → LLM → TTS → 回传
  • HTTP 8080 — 网页静态文件 + /api/chat + /api/tts + /api/asr + /api/status
  • MQTT 桥接 — 连接本地 Mosquitto，将 AI 回复/动作同步到 MQTT 供多端消费

运行: python server.py [--no-gui]
"""
from openai import OpenAI
import io
import os
import sys
import re
import json
import struct
import socket
import base64
import wave
import threading
import time
import logging
import urllib.request
import urllib.parse
from logging.handlers import RotatingFileHandler
from http.server import HTTPServer, SimpleHTTPRequestHandler

# ============================================================
#  日志配置
# ============================================================
LOG_DIR = "/opt/esp-iot/logs" if os.path.isdir("/opt/esp-iot") else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("esp-iot")
logger.setLevel(logging.DEBUG)

# 文件日志: 10MB x 7 轮转
_fh = RotatingFileHandler(
    os.path.join(LOG_DIR, "server.log"),
    maxBytes=10 * 1024 * 1024,
    backupCount=7,
    encoding="utf-8",
)
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter(
    "[%(asctime)s] %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))

# 控制台日志 (兼容 systemd journal)
_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))

logger.addHandler(_fh)
logger.addHandler(_ch)

# ============================================================
#  依赖导入
# ============================================================

# 可选依赖：pydub 仅网页端 ASR 音频格式转换时需要
try:
    from pydub import AudioSegment
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False
    logger.warning("pydub 未安装，网页端 ASR 音频格式转换不可用")

# 可选依赖：paho-mqtt 用于 MQTT 状态桥接
try:
    import paho.mqtt.client as paho_mqtt
    HAS_PAHO = True
except ImportError:
    HAS_PAHO = False
    logger.warning("paho-mqtt 未安装，MQTT 桥接不可用 (pip install paho-mqtt)")

# ============================================================
#  配置
# ============================================================
# 尝试加载 .env（本地调试用；生产环境直接 export 环境变量即可）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 小米 MiMo API Key —— 必须通过环境变量 XIAOMI_API_KEY 提供（参考 .env.example）
XIAOMI_API_KEY = os.environ.get("XIAOMI_API_KEY", "").strip()
if not XIAOMI_API_KEY:
    logger.warning("未检测到 XIAOMI_API_KEY，请在 .env 或环境变量中设置（参考 .env.example）")

TCP_HOST = "0.0.0.0"
TCP_PORT = 9000          # ESP32 TCP 端口
HTTP_PORT = 8080         # 网页 HTTP 端口

# MQTT 桥接配置 (连接本地 Mosquitto)
MQTT_BRIDGE_HOST = "127.0.0.1"
MQTT_BRIDGE_PORT = 1883
MQTT_STATE_PREFIX = "esp32/iot/state/"   # server 发布 AI 动作状态
MQTT_VOICE_OUT = "esp32/iot/voice/output"  # server 发布 AI 回复文本

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 2048        # 每帧音频字节数 (~64ms)

# 天气查询默认城市
DEFAULT_CITY = "广州"

IS_GUI = False

client = OpenAI(api_key=XIAOMI_API_KEY,
                base_url="https://api.xiaomimimo.com/v1")

# 对话历史模板
SYSTEM_PROMPT = (
    "你是一个友好、自然的智能家居+环境监测助手，用简洁口语回复，每次不超过3句话。"
    "你可以控制: 风扇(电机)、LED灯、RGB彩灯/灯带、蜂鸣器。"
    "你可以查询实时天气信息，当用户询问天气、气温、是否需要带伞等问题时，调用天气工具获取数据后再回答。"
    "当用户要求开关风扇、调节亮度、设置阈值、报警等，请在回复末尾用 [ACTION] 标记添加指令。"
    "支持的指令格式:\n"
    '[ACTION]{"action":"fan","state":"on"/"off","speed":0~1023}[/ACTION]\n'
    '[ACTION]{"action":"led","state":"on"/"off"}[/ACTION]\n'
    '[ACTION]{"action":"buzzer","state":"on"/"off","times":3}[/ACTION]\n'
    '[ACTION]{"action":"rgb","color":"red/green/blue/yellow/purple/white/off"}[/ACTION]\n'
    '[ACTION]{"action":"rgb","color":"rainbow/welcome"}[/ACTION]\n'
    '[ACTION]{"action":"rgb","color":"breath/pulse/alarm","r":0~255,"g":0~255,"b":0~255}[/ACTION]\n'
    '[ACTION]{"action":"rgb","r":0~255,"g":0~255,"b":0~255}[/ACTION]\n'
    "如果温度过高，可以主动建议开风扇或自动触发。"
)
MAX_HISTORY_ROUNDS = 10

# Function Calling 工具定义
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取指定城市的实时天气信息，包括温度、湿度、天气状况、风力等",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称，如：广州、北京、上海",
                    }
                },
                "required": ["city"],
            },
        },
    }
]


def fetch_weather(city: str) -> str:
    """
    通过 wttr.in 获取天气数据，返回中文摘要。
    若请求失败则返回错误提示。
    """
    try:
        # wttr.in 支持中文城市名（URL 编码）
        encoded_city = urllib.parse.quote(city)
        url = f"https://wttr.in/{encoded_city}?format=j1&lang=zh"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.88"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        current = data.get("current_condition", [{}])[0]
        temp_c = current.get("temp_C", "?")
        feels_like = current.get("FeelsLikeC", "?")
        humidity = current.get("humidity", "?")
        desc_list = current.get("lang_zh", [])
        desc = desc_list[0].get("value", "") if desc_list else current.get(
            "weatherDesc", [{}])[0].get("value", "")
        wind_kmph = current.get("windspeedKmph", "?")
        wind_dir = current.get("winddir16Point", "")
        visibility = current.get("visibility", "?")
        uv_index = current.get("uvIndex", "?")

        # 今日预报
        today = data.get("weather", [{}])[0]
        max_temp = today.get("maxtempC", "?")
        min_temp = today.get("mintempC", "?")

        result = (
            f"城市: {city}\n"
            f"当前天气: {desc}\n"
            f"当前温度: {temp_c}°C (体感 {feels_like}°C)\n"
            f"今日温度: {min_temp}°C ~ {max_temp}°C\n"
            f"湿度: {humidity}%\n"
            f"风速: {wind_kmph} km/h {wind_dir}\n"
            f"能见度: {visibility} km\n"
            f"紫外线指数: {uv_index}"
        )
        logger.info(f"[WEATHER] {city}: {desc} {temp_c}°C")
        return result

    except Exception as e:
        logger.error(f"[WEATHER] 获取天气失败 ({city}): {e}")
        return f"无法获取 {city} 的天气信息，请稍后再试。"


# ============================================================
#  统一对话历史 (TCP + HTTP 共享)
# ============================================================
_shared_history = [{"role": "system", "content": SYSTEM_PROMPT}]
_history_lock = threading.Lock()


def get_history_snapshot():
    """获取当前对话历史快照 (线程安全)"""
    with _history_lock:
        return list(_shared_history)


def chat_shared(text, sensor_info=None):
    """
    统一对话入口 (TCP 和 HTTP 共享同一份历史)。
    支持 Function Calling (天气等工具)，最多循环 3 轮工具调用。
    返回 (reply_text, actions_list)。
    """
    user_msg = text
    if sensor_info:
        extras = []
        if sensor_info.get("temperature") is not None:
            extras.append(f"温度 {sensor_info['temperature']}°C")
        if sensor_info.get("humidity") is not None:
            extras.append(f"湿度 {sensor_info['humidity']}%")
        if sensor_info.get("light") is not None:
            extras.append(f"光照 {sensor_info['light']}")
        if extras:
            user_msg += f"\n[传感器数据: {', '.join(extras)}]"

    # 工具函数映射表
    tool_dispatch = {
        "get_weather": lambda args: fetch_weather(args.get("city", DEFAULT_CITY)),
    }

    with _history_lock:
        _shared_history.append({"role": "user", "content": user_msg})

        reply = None
        for _round in range(3):  # 最多 3 轮工具调用
            resp = client.chat.completions.create(
                model="mimo-v2.5",
                messages=_shared_history,
                tools=TOOLS,
                tool_choice="auto",
            )
            msg = resp.choices[0].message

            # 无工具调用 → 正常文本回复
            if not msg.tool_calls:
                reply = msg.content or ""
                _shared_history.append({"role": "assistant", "content": reply})
                break

            # 有工具调用 → 执行并将结果追加到历史
            logger.info(
                f"[TOOL] 第{_round+1}轮工具调用: {[tc.function.name for tc in msg.tool_calls]}")

            # 先追加 assistant 消息（含 tool_calls）
            _shared_history.append(msg.model_dump())

            for tc in msg.tool_calls:
                func_name = tc.function.name
                try:
                    func_args = json.loads(tc.function.arguments)
                except Exception:
                    func_args = {}

                handler = tool_dispatch.get(func_name)
                if handler:
                    result = handler(func_args)
                    logger.info(
                        f"[TOOL] {func_name}({func_args}) → {result[:60]}")
                else:
                    result = f"未知工具: {func_name}"
                    logger.warning(f"[TOOL] 未知工具: {func_name}")

                _shared_history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        # 截断历史：保留 system + 最近 N 轮
        if len(_shared_history) > 1 + MAX_HISTORY_ROUNDS * 4:
            _shared_history[1:] = _shared_history[1 - MAX_HISTORY_ROUNDS * 4:]

    if reply is None:
        reply = "抱歉，我暂时无法处理这个请求。"

    clean_text, actions = parse_actions(reply)
    return clean_text, actions


# ============================================================
#  MQTT 桥接模块
# ============================================================
_mqtt_bridge = None      # paho MQTT client 实例
_device_state = {}       # 缓存最新设备状态 {device: {state, ts}}
_state_lock = threading.Lock()


def mqtt_bridge_init():
    """初始化 MQTT 桥接，连接本地 Mosquitto"""
    global _mqtt_bridge
    if not HAS_PAHO:
        return

    _mqtt_bridge = paho_mqtt.Client(
        client_id="server_bridge_" + str(int(time.time())),
        callback_api_version=paho_mqtt.CallbackAPIVersion.VERSION2,
    )

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0 or (hasattr(rc, 'value') and rc.value == 0):
            logger.info("[MQTT-BRIDGE] 已连接 Mosquitto ✓")
            # 订阅 ESP32 状态回执 (用于 /api/status)
            client.subscribe("esp32/iot/state/#")
            client.subscribe("esp32/iot/sensor")
            client.subscribe("esp32/iot/status")
        else:
            logger.error(f"[MQTT-BRIDGE] 连接失败: rc={rc}")

    def on_message(client, userdata, msg):
        """缓存 ESP32 发布的状态消息"""
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            return

        if topic.startswith("esp32/iot/state/"):
            device = topic.split("/")[-1]
            with _state_lock:
                _device_state[device] = payload
        elif topic == "esp32/iot/sensor":
            with _state_lock:
                _device_state["_sensor"] = payload
        elif topic == "esp32/iot/status":
            with _state_lock:
                _device_state["_status"] = payload

    _mqtt_bridge.on_connect = on_connect
    _mqtt_bridge.on_message = on_message

    try:
        _mqtt_bridge.connect(MQTT_BRIDGE_HOST, MQTT_BRIDGE_PORT, keepalive=30)
        _mqtt_bridge.loop_start()
    except Exception as e:
        logger.warning(f"[MQTT-BRIDGE] 连接 Mosquitto 失败: {e} (MQTT 桥接不可用)")
        _mqtt_bridge = None


def mqtt_publish_state(device, state_dict):
    """发布设备状态到 MQTT (retain=True，新客户端连接后立即获得最新状态)"""
    if _mqtt_bridge and _mqtt_bridge.is_connected():
        topic = f"{MQTT_STATE_PREFIX}{device}"
        payload = json.dumps(state_dict, ensure_ascii=False)
        _mqtt_bridge.publish(topic, payload, retain=True)
        with _state_lock:
            _device_state[device] = state_dict
        logger.debug(f"[MQTT-BRIDGE] 发布状态: {topic} = {payload}")


def mqtt_publish_voice(text):
    """发布 AI 回复文本到 MQTT voice/output"""
    if _mqtt_bridge and _mqtt_bridge.is_connected():
        _mqtt_bridge.publish(MQTT_VOICE_OUT, text, retain=False)


def mqtt_publish_actions(actions, source="tcp"):
    """将 AI 生成的动作指令发布到 MQTT state，供多端同步"""
    ts = int(time.time())
    for act in actions:
        device = act.get("action", "unknown")
        mqtt_publish_state(device, {**act, "ts": ts, "source": source})
        logger.info(f"[MQTT-BRIDGE] AI 指令同步: {device} = {act}")


def get_device_snapshot():
    """获取当前所有设备状态快照 (供 /api/status 使用)"""
    with _state_lock:
        return dict(_device_state)


# ============================================================
#  共享工具函数
# ============================================================
def pcm_to_wav_bytes(pcm_data: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def wav_to_pcm(wav_data: bytes) -> bytes:
    """
    将 WAV 解码为 16kHz/mono/16bit PCM。
    若 TTS 返回的采样率或声道数与 ESP32 I2S 不同，则自动重采样，
    避免播放时出现杂音或变调。
    """
    buf = io.BytesIO(wav_data)
    with wave.open(buf, "rb") as wf:
        src_rate = wf.getframerate()
        src_channels = wf.getnchannels()
        src_sampwidth = wf.getsampwidth()
        pcm = wf.readframes(wf.getnframes())

    # 已经是 16kHz/mono/16bit，直接返回
    if src_rate == SAMPLE_RATE and src_channels == 1 and src_sampwidth == 2:
        return pcm

    logger.info(
        f"[PCM] TTS WAV: {src_rate}Hz/{src_channels}ch/{src_sampwidth*8}bit → 重采样为 {SAMPLE_RATE}Hz/mono/16bit")

    # 用 pydub 重采样（已保证安装，TTS 链路必走此处）
    try:
        seg = AudioSegment(
            data=pcm,
            sample_width=src_sampwidth,
            frame_rate=src_rate,
            channels=src_channels,
        )
        seg = seg.set_frame_rate(SAMPLE_RATE).set_channels(
            1).set_sample_width(2)
        return seg.raw_data
    except Exception as e:
        logger.warning(f"[PCM] pydub 重采样失败，返回原始 PCM: {e}")
        return pcm


def speech_to_text(wav_bytes: bytes) -> str:
    audio_b64 = base64.b64encode(wav_bytes).decode()
    resp = client.chat.completions.create(
        model="mimo-v2.5-asr",
        messages=[{
            "role": "user",
            "content": [{"type": "input_audio",
                         "input_audio": {"data": f"data:audio/wav;base64,{audio_b64}"}}]
        }],
        extra_body={"asr_options": {"language": "zh"}}
    )
    return (resp.choices[0].message.content or "").strip()


def text_to_speech(text: str) -> bytes:
    resp = client.chat.completions.create(
        model="mimo-v2.5-tts",
        messages=[
            {"role": "user",
             "content": "Calm, natural, and relaxed tone — like chatting casually with a close friend."},
            {"role": "assistant", "content": text}
        ],
        audio={"format": "wav", "voice": "Chloe"}
    )
    return base64.b64decode(resp.choices[0].message.audio.data)


def parse_actions(reply: str):
    """从回复中提取 [ACTION]...[/ACTION] 块，返回 (clean_text, actions_list)"""
    actions = []
    for match in re.finditer(r"\[ACTION\](.*?)\[/ACTION\]", reply, re.DOTALL):
        try:
            actions.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            pass
    clean = re.sub(r"\[ACTION\].*?\[/ACTION\]", "",
                   reply, flags=re.DOTALL).strip()
    return clean, actions


def audio_to_wav_bytes(audio_bytes: bytes, src_format: str = "webm") -> bytes:
    """用 pydub+ffmpeg 将任意格式音频转为 16kHz mono 16bit WAV"""
    if not HAS_PYDUB:
        raise RuntimeError("pydub 未安装，无法转换音频格式")
    seg = AudioSegment.from_file(io.BytesIO(audio_bytes), format=src_format)
    seg = seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    buf = io.BytesIO()
    seg.export(buf, format="wav")
    return buf.getvalue()


# ############################################################
#  TCP 服务器（ESP32 通信）
# ############################################################
MSG_AUDIO = 0x01
MSG_CMD = 0x02
MSG_DONE = 0x03
_MAX_FRAME_LEN = 2 * 1024 * 1024


def send_frame(sock, frame_type, data):
    header = struct.pack(">BI", frame_type, len(data))
    sock.sendall(header)
    sock.sendall(data)


def recv_frame(sock):
    header = recv_exact(sock, 5)
    if header is None:
        return None, None
    frame_type, length = struct.unpack(">BI", header)
    if length > _MAX_FRAME_LEN:
        logger.error(f"帧长度异常: {length}")
        return None, None
    data = recv_exact(sock, length) if length > 0 else b""
    return frame_type, data


def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 4096))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def handle_client(conn, addr):
    logger.info(f"[TCP] 新连接: {addr}")

    audio_chunks = []
    sensor_info = {}

    try:
        while True:
            ftype, data = recv_frame(conn)
            if ftype is None:
                break

            if ftype == MSG_AUDIO:
                audio_chunks.append(data)

            elif ftype == MSG_CMD:
                cmd = json.loads(data.decode())
                action = cmd.get("action", "")

                if action == "start_recording":
                    logger.info("[TCP] 开始接收音频...")
                    audio_chunks = []

                elif action == "stop_recording":
                    logger.info(
                        f"[TCP] 音频接收完毕，共 {sum(len(c) for c in audio_chunks)} 字节")
                    sensor_info = cmd

                    if not audio_chunks:
                        logger.warning("[TCP] 无音频数据")
                        break

                    pcm_data = b"".join(audio_chunks)
                    wav_bytes = pcm_to_wav_bytes(pcm_data)

                    logger.info("[ASR] 识别中...")
                    user_text = speech_to_text(wav_bytes)
                    logger.info(f"[ASR] 用户说: {user_text}")

                    if not user_text:
                        send_frame(conn, MSG_CMD, json.dumps(
                            {"action": "info", "msg": "未识别到内容"}).encode())
                        send_frame(conn, MSG_DONE, b"")
                        break

                    if user_text.lower() in ("退出", "quit", "结束"):
                        send_frame(conn, MSG_CMD, json.dumps(
                            {"action": "info", "msg": "再见"}).encode())
                        send_frame(conn, MSG_DONE, b"")
                        break

                    logger.info("[LLM] 思考中...")
                    clean_text, actions = chat_shared(user_text, sensor_info)
                    logger.info(f"[LLM] 回复: {clean_text}")
                    if actions:
                        logger.info(f"[LLM] 指令: {actions}")

                    logger.info("[TTS] 合成语音...")
                    reply_wav = text_to_speech(clean_text)
                    reply_pcm = wav_to_pcm(reply_wav)

                    logger.info(f"[TCP] 回传音频 {len(reply_pcm)} 字节...")
                    offset = 0
                    while offset < len(reply_pcm):
                        end = min(offset + CHUNK_SIZE, len(reply_pcm))
                        send_frame(conn, MSG_AUDIO, reply_pcm[offset:end])
                        offset = end

                    for act in actions:
                        send_frame(conn, MSG_CMD, json.dumps(act).encode())

                    # MQTT 桥接: 同步 AI 回复和动作到多端
                    mqtt_publish_voice(clean_text)
                    mqtt_publish_actions(actions, source="esp32_tcp")

                    send_frame(conn, MSG_DONE, b"")
                    logger.info("[TCP] 回复完成 ✓")
                    break

                elif action == "web_text":
                    user_text = cmd.get("text", "").strip()
                    logger.info(f"[TCP-WEB] 收到文本: {user_text}")

                    if not user_text:
                        send_frame(conn, MSG_CMD, json.dumps(
                            {"action": "info", "msg": "空文本"}).encode())
                        send_frame(conn, MSG_DONE, b"")
                        break

                    logger.info("[LLM] 思考中...")
                    clean_text, actions = chat_shared(user_text, sensor_info)
                    logger.info(f"[LLM] 回复: {clean_text}")

                    logger.info("[TTS] 合成语音...")
                    reply_wav = text_to_speech(clean_text)
                    reply_pcm = wav_to_pcm(reply_wav)

                    logger.info(f"[TCP] 回传音频 {len(reply_pcm)} 字节...")
                    offset = 0
                    while offset < len(reply_pcm):
                        end = min(offset + CHUNK_SIZE, len(reply_pcm))
                        send_frame(conn, MSG_AUDIO, reply_pcm[offset:end])
                        offset = end

                    for act in actions:
                        send_frame(conn, MSG_CMD, json.dumps(act).encode())

                    send_frame(conn, MSG_CMD, json.dumps(
                        {"action": "reply", "text": clean_text}).encode())

                    # MQTT 桥接
                    mqtt_publish_voice(clean_text)
                    mqtt_publish_actions(actions, source="esp32_tcp")

                    send_frame(conn, MSG_DONE, b"")
                    logger.info("[TCP] 网页回复完成 ✓")
                    break

    except Exception as e:
        logger.error(f"[TCP ERR] {e}", exc_info=True)
    finally:
        conn.close()
        logger.info(f"[TCP] 断开连接: {addr}")


def run_tcp_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((TCP_HOST, TCP_PORT))
    srv.listen(5)
    logger.info(f"[TCP] 监听 {TCP_HOST}:{TCP_PORT}，等待 ESP32 连接...")

    while True:
        conn, addr = srv.accept()
        conn.settimeout(180)
        t = threading.Thread(target=handle_client,
                             args=(conn, addr), daemon=True)
        t.start()


# ############################################################
#  HTTP 服务器（网页端）
# ############################################################
def get_resource_path(relative_path):
    """获取资源的绝对路径（兼容开发环境与 PyInstaller 打包环境）"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, relative_path)


class WebHandler(SimpleHTTPRequestHandler):
    """处理静态文件 + API 请求"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=get_resource_path("web"), **kwargs)

    def end_headers(self):
        if hasattr(self, '_headers_buffer'):
            for i, h in enumerate(self._headers_buffer):
                if h.startswith(b'Content-Type') and (
                    b'javascript' in h or b'css' in h or b'html' in h
                ):
                    self.send_header(
                        "Cache-Control", "no-cache, no-store, must-revalidate")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                    break
        super().end_headers()

    def do_GET(self):
        if self.path == "/api/status":
            self._handle_status()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/chat":
            self._handle_chat()
        elif self.path == "/api/tts":
            self._handle_tts()
        elif self.path == "/api/asr":
            self._handle_asr()
        elif self.path == "/api/status":
            self._handle_status()
        elif self.path == "/api/shutdown":
            self._handle_shutdown()
        else:
            self.send_error(404)

    def _handle_shutdown(self):
        logger.info("[SYS] 收到网页端关机指令，程序退出")
        self._json_response({"success": True})

        def exit_process():
            time.sleep(0.5)
            os._exit(0)
        threading.Thread(target=exit_process, daemon=True).start()

    def _handle_status(self):
        """返回当前设备状态快照 (从 MQTT retained 缓存读取)"""
        snapshot = get_device_snapshot()
        self._json_response(snapshot)

    def _handle_chat(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            text = body.get("text", "").strip()
            sensor = body.get("sensor")

            if not text:
                self._json_response({"error": "空文本"})
                return

            logger.info(f"[HTTP-CHAT] 用户: {text}")
            reply_text, actions = chat_shared(text, sensor)
            logger.info(f"[HTTP-CHAT] AI: {reply_text}")
            if actions:
                logger.info(f"[HTTP-CHAT] 指令: {actions}")

            # MQTT 桥接: 同步 AI 回复和动作到多端
            mqtt_publish_voice(reply_text)
            mqtt_publish_actions(actions, source="web_http")

            self._json_response({"reply": reply_text, "actions": actions})
        except Exception as e:
            logger.error(f"[HTTP ERR] {e}", exc_info=True)
            self._json_response({"error": str(e)}, 500)

    def _handle_asr(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            audio_bytes = self.rfile.read(length)
            mime = self.headers.get("Content-Type", "audio/webm")
            src_fmt = mime.split(";")[0].strip().split("/")[-1]
            if src_fmt == "mpeg":
                src_fmt = "mp3"

            if not audio_bytes:
                self._json_response({"error": "无音频数据"})
                return

            if src_fmt == "wav":
                wav_bytes = audio_bytes
                logger.info(
                    f"[HTTP-ASR] 收到原生 WAV 音频 {len(audio_bytes)} 字节，无需转码")
            else:
                logger.info(
                    f"[HTTP-ASR] 收到 {src_fmt} 音频 {len(audio_bytes)} 字节，转码中...")
                wav_bytes = audio_to_wav_bytes(audio_bytes, src_fmt)
                logger.info(f"[HTTP-ASR] WAV 转换完成 {len(wav_bytes)} bytes")
            logger.info("[HTTP-ASR] 识别中...")
            text = speech_to_text(wav_bytes)
            logger.info(f"[HTTP-ASR] 识别结果: {text}")

            if not text:
                self._json_response({"error": "未识别到内容"})
                return

            self._json_response({"text": text})
        except Exception as e:
            logger.error(f"[HTTP-ASR ERR] {e}", exc_info=True)
            self._json_response({"error": str(e)}, 500)

    def _handle_tts(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            text = body.get("text", "").strip()

            if not text:
                self._json_response({"error": "空文本"})
                return

            logger.info(f"[HTTP-TTS] 合成: {text[:40]}...")
            wav = text_to_speech(text)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(wav)
        except Exception as e:
            logger.error(f"[HTTP-TTS ERR] {e}")
            self._json_response({"error": str(e)}, 500)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _json_response(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        if "/api/" in str(args[0]):
            logger.info(f"[HTTP] {args[0]}")


def run_http_server():
    srv = HTTPServer(("0.0.0.0", HTTP_PORT), WebHandler)
    logger.info(f"[HTTP] 监听 0.0.0.0:{HTTP_PORT}，网页服务已就绪")
    if IS_GUI:
        try:
            import webbrowser
            threading.Timer(0.5, lambda: webbrowser.open(
                f"http://localhost:{HTTP_PORT}")).start()
        except Exception:
            pass
    srv.serve_forever()


# ############################################################
#  主入口
# ############################################################
def main():
    global IS_GUI

    if "--no-gui" in sys.argv:
        IS_GUI = False
    elif "--gui" in sys.argv:
        IS_GUI = True
    else:
        # 默认: 检测是否有图形环境
        IS_GUI = "DISPLAY" in os.environ or sys.platform == "win32"

    logger.info("=" * 50)
    logger.info("  [SYS] 小米 MiMo 语音助手 — 统一服务器 (云端版)")
    logger.info(f"  TCP  → {TCP_HOST}:{TCP_PORT}  (ESP32)")
    logger.info(f"  HTTP → http://localhost:{HTTP_PORT}  (网页)")
    logger.info(f"  MQTT → {MQTT_BRIDGE_HOST}:{MQTT_BRIDGE_PORT}  (桥接)")
    logger.info(f"  GUI  → {'开启' if IS_GUI else '关闭'}")
    logger.info("=" * 50)

    # 初始化 MQTT 桥接
    mqtt_bridge_init()

    # 启动 TCP 服务 (后台线程)
    tcp_thread = threading.Thread(target=run_tcp_server, daemon=True)
    tcp_thread.start()

    if IS_GUI:
        # 本地开发模式: 启动 HTTP + pywebview 桌面窗口
        http_thread = threading.Thread(target=run_http_server, daemon=True)
        http_thread.start()

        try:
            import webview
            logger.info("[SYS] 启动原生窗口...")
            webview.create_window(
                title="ESP32 IoT 智能语音终端",
                url=f"http://localhost:{HTTP_PORT}",
                width=1280,
                height=820,
                resizable=True
            )
            webview.start()
        except ImportError:
            logger.warning("webview 未安装，仅启动 HTTP 服务")
            http_thread.join()
    else:
        # 云端模式: HTTP 服务在主线程运行 (配合 systemd)
        run_http_server()


if __name__ == "__main__":
    main()
