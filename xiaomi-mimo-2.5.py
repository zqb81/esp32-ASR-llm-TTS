import os
import io
import wave
import base64
import threading
import numpy as np
import sounddevice as sd
from openai import OpenAI

# 尝试加载 .env（本地调试用）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

XIAOMI_API_KEY = os.environ.get("XIAOMI_API_KEY", "").strip()
if not XIAOMI_API_KEY:
    raise SystemExit("请先设置环境变量 XIAOMI_API_KEY（参考 .env.example）")
SAMPLE_RATE = 16000  # 16kHz 采样率，适合语音识别
CHANNELS = 1         # 单声道

client = OpenAI(
    api_key=XIAOMI_API_KEY,
    base_url="https://api.xiaomimimo.com/v1"
)

# 对话历史（保持上下文）
chat_history = [
    {"role": "system", "content": "你是一个友好、自然、有亲和力的助手，用简洁口语回复，每次回复不超过3句话。"}
]


# ============================================================
# 录音工具函数
# ============================================================
def record_audio():
    """按 Enter 开始/停止录音，返回 PCM 字节数据"""
    print("\n⏎  按 Enter 开始说话...")
    input()
    print("🔴 正在录音，说完后按 Enter 停止...")

    audio_frames = []
    stop_event = threading.Event()

    def callback(indata, frames, time_info, status):
        if not stop_event.is_set():
            audio_frames.append(indata.copy())

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
        callback=callback
    ):
        input()  # 等待用户按 Enter 停止
        stop_event.set()

    print("⏹  录音结束")
    audio_data = np.concatenate(audio_frames, axis=0)
    return audio_data.tobytes()


def pcm_to_wav_bytes(pcm_data: bytes) -> bytes:
    """将原始 PCM 字节转为 WAV 格式字节"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # int16 = 2 bytes
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def play_wav_bytes(wav_data: bytes):
    """播放 WAV 音频"""
    buf = io.BytesIO(wav_data)
    with wave.open(buf, "rb") as wf:
        fs = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype="int16")
    sd.play(audio, fs)
    sd.wait()


# ============================================================
# ASR / LLM / TTS 核心函数
# ============================================================
def speech_to_text(wav_bytes: bytes) -> str:
    """语音识别：WAV → 文字"""
    audio_base64 = base64.b64encode(wav_bytes).decode("utf-8")
    response = client.chat.completions.create(
        model="mimo-v2.5-asr",
        messages=[{
            "role": "user",
            "content": [{
                "type": "input_audio",
                "input_audio": {"data": f"data:audio/wav;base64,{audio_base64}"}
            }]
        }],
        extra_body={"asr_options": {"language": "zh"}}
    )
    return response.choices[0].message.content or ""


def chat(text: str) -> str:
    """对话生成：文字 → 回复"""
    chat_history.append({"role": "user", "content": text})
    response = client.chat.completions.create(
        model="mimo-v2.5",
        messages=chat_history
    )
    reply = response.choices[0].message.content
    chat_history.append({"role": "assistant", "content": reply})
    return reply


def text_to_speech(text: str) -> bytes:
    """语音合成：文字 → WAV 字节"""
    response = client.chat.completions.create(
        model="mimo-v2.5-tts",
        messages=[
            {
                "role": "user",
                "content": "Calm, natural, and relaxed tone — like chatting casually with a close friend. Moderate pace, gentle and steady voice, no exaggeration."
            },
            {
                "role": "assistant",
                "content": text
            }
        ],
        audio={"format": "wav", "voice": "Chloe"}
    )
    return base64.b64decode(response.choices[0].message.audio.data)


# ============================================================
# 主循环
# ============================================================
if __name__ == "__main__":
    print("=" * 40)
    print("  🗣️  小米 MiMo 实时语音对话")
    print("  输入 quit 退出程序")
    print("=" * 40)

    while True:
        # 1. 录音
        pcm_data = record_audio()
        wav_bytes = pcm_to_wav_bytes(pcm_data)

        # 2. 语音识别
        print("⏳ 识别中...")
        user_text = speech_to_text(wav_bytes)
        print(f"🎙️  你说：{user_text}")

        if not user_text.strip():
            print("（未识别到内容，请重新说话）")
            continue

        if user_text.strip().lower() in ("退出", "quit", "结束"):
            print("👋 对话结束，再见！")
            break

        # 3. 生成回复
        print("⏳ 思考中...")
        reply_text = chat(user_text)
        print(f"🤖 回复：{reply_text}")

        # 4. 语音合成 + 播放
        print("⏳ 合成语音...")
        reply_wav = text_to_speech(reply_text)
        print("🔊 播放中...")
        play_wav_bytes(reply_wav)
