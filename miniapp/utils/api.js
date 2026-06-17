// utils/api.js — 小米 MiMo 接口直接集成 (去本地 python 服务端化)

// API Key 从 secrets.js 读取（已 gitignore，不入库）；
// 复制 secrets.example.js 为 secrets.js 并填入真实 Key
const { XIAOMI_API_KEY } = require('./secrets.js');
const XIAOMI_BASE_URL = "https://api.xiaomimimo.com/v1";

const SYSTEM_PROMPT = `你是一个友好、自然的智能家居+环境监测助手，用简洁口语回复，每次不超过3句话。
你可以控制: 风扇(电机)、LED灯、RGB彩灯/灯带、蜂鸣器。
当用户要求开关风扇、调节亮度、设置阈值、报警等，请在回复末尾用 [ACTION] 标记添加指令。
支持的指令格式:
[ACTION]{"action":"fan","state":"on"/"off","speed":0~1023}[/ACTION]
[ACTION]{"action":"led","state":"on"/"off"}[/ACTION]
[ACTION]{"action":"buzzer","state":"on"/"off","times":3}[/ACTION]
[ACTION]{"action":"rgb","color":"red/green/blue/yellow/purple/white/off"}[/ACTION]
[ACTION]{"action":"rgb","color":"rainbow/welcome"}[/ACTION]
[ACTION]{"action":"rgb","color":"breath/pulse/alarm","r":0~255,"g":0~255,"b":0~255}[/ACTION]
[ACTION]{"action":"rgb","r":0~255,"g":0~255,"b":0~255}[/ACTION]
如果温度过高，可以主动建议开风扇或自动触发。`;

const MAX_HISTORY_ROUNDS = 10;

/**
 * 获取或初始化会话历史
 */
function getChatHistory() {
  const app = getApp();
  if (!app.globalData.chatHistory) {
    app.globalData.chatHistory = [];
  }
  if (app.globalData.chatHistory.length === 0) {
    app.globalData.chatHistory.push({ role: 'system', content: SYSTEM_PROMPT });
  }
  return app.globalData.chatHistory;
}

/**
 * 从回复中提取 [ACTION] 块
 */
function parseActions(reply) {
  const actions = [];
  const regex = /\[ACTION\](.*?)\[\/ACTION\]/gs;
  let match;
  while ((match = regex.exec(reply)) !== null) {
    try {
      actions.push(JSON.parse(match[1].trim()));
    } catch (e) {
      console.error('解析 Action 失败:', e);
    }
  }
  const cleanReply = reply.replace(/\[ACTION\].*?\[\/ACTION\]/gs, '').trim();
  return { reply: cleanReply, actions };
}

/**
 * 直接发送聊天消息到小米 MiMo LLM
 */
function chat(text, deviceState) {
  return new Promise((resolve, reject) => {
    const history = getChatHistory();

    // 拼接用户输入与实时传感器指标及设备运行状态
    let userMsg = text;
    if (deviceState) {
      const extras = [];
      // 传感器物理量
      if (deviceState.temperature !== null && deviceState.temperature !== undefined && deviceState.temperature !== '--') {
        extras.push(`室温 ${deviceState.temperature}°C`);
      }
      if (deviceState.humidity !== null && deviceState.humidity !== undefined && deviceState.humidity !== '--') {
        extras.push(`湿度 ${deviceState.humidity}%`);
      }
      if (deviceState.light !== null && deviceState.light !== undefined && deviceState.light !== '--') {
        extras.push(`光照 ${deviceState.light}lux`);
      }
      // 执行器物理状态
      extras.push(`智能风扇: ${deviceState.fan ? '开启中' : '已关闭'}`);
      extras.push(`警报蜂鸣器: ${deviceState.buzzer ? '报警中' : '已关闭'}`);
      extras.push(`LED面板灯: ${deviceState.led ? '开启中' : '已关闭'}`);
      extras.push(`温控报警阈值: ${deviceState.threshold}°C`);
      // 设备连接通信状态
      extras.push(`ESP32控制板通信: ${deviceState.espOnline ? '在线' : '离线'}`);
      extras.push(`MQTT代理服务端: ${deviceState.mqttConnected ? '已连接' : '未连接'}`);

      userMsg += `\n[系统设备当前状态: ${extras.join(', ')}]`;
    }

    history.push({ role: 'user', content: userMsg });

    wx.request({
      url: `${XIAOMI_BASE_URL}/chat/completions`,
      method: 'POST',
      header: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${XIAOMI_API_KEY}`
      },
      data: {
        model: 'mimo-v2.5',
        messages: history
      },
      success(res) {
        if (res.statusCode === 200 && res.data && res.data.choices && res.data.choices[0]) {
          const reply = res.data.choices[0].message.content || "";

          // 将 AI 回复保存进历史上下文
          history.push({ role: 'system', content: reply });

          // 限制历史上下文长度，保留 system 提示词 + 最近 N 轮对话
          const app = getApp();
          if (history.length > 1 + MAX_HISTORY_ROUNDS * 2) {
            history.splice(1, history.length - 1 - MAX_HISTORY_ROUNDS * 2);
          }
          app.globalData.chatHistory = history;

          // 解析回复里的控制指令
          const parsed = parseActions(reply);
          resolve(parsed);
        } else {
          reject(new Error(`Chat HTTP ${res.statusCode}`));
        }
      },
      fail(err) {
        reject(err);
      }
    });
  });
}

/**
 * 直接调用小米 MiMo ASR 语音识别 (WAV)
 */
function asr(tempFilePath) {
  return new Promise((resolve, reject) => {
    const fs = wx.getFileSystemManager();

    // 将录制完成的本地 WAV 文件读取为 Base64
    fs.readFile({
      filePath: tempFilePath,
      encoding: 'base64',
      success(fileRes) {
        const audioBase64 = fileRes.data;

        wx.request({
          url: `${XIAOMI_BASE_URL}/chat/completions`,
          method: 'POST',
          header: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${XIAOMI_API_KEY}`
          },
          data: {
            model: 'mimo-v2.5-asr',
            messages: [{
              role: 'user',
              content: [{
                type: 'input_audio',
                input_audio: {
                  data: `data:audio/wav;base64,${audioBase64}`
                }
              }]
            }],
            extra_body: {
              asr_options: {
                language: 'zh'
              }
            }
          },
          success(res) {
            if (res.statusCode === 200 && res.data && res.data.choices && res.data.choices[0]) {
              const text = res.data.choices[0].message.content || "";
              resolve({ text: text.trim() });
            } else {
              reject(new Error(`ASR HTTP ${res.statusCode}`));
            }
          },
          fail(err) {
            reject(err);
          }
        });
      },
      fail(err) {
        reject(new Error('读取本地 WAV 失败: ' + err.errMsg));
      }
    });
  });
}

/**
 * 直接调用小米 MiMo TTS 语音合成 (WAV)，保存为本地临时文件
 */
function tts(text) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${XIAOMI_BASE_URL}/chat/completions`,
      method: 'POST',
      header: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${XIAOMI_API_KEY}`
      },
      data: {
        model: 'mimo-v2.5-tts',
        messages: [
          {
            role: 'user',
            content: 'Calm, natural, and relaxed tone — like chatting casually with a close friend.'
          },
          {
            role: 'assistant',
            content: text
          }
        ],
        audio: {
          format: 'wav',
          voice: 'Chloe'
        }
      },
      success(res) {
        if (res.statusCode === 200 && res.data && res.data.choices && res.data.choices[0] && res.data.choices[0].message.audio) {
          const audioBase64 = res.data.choices[0].message.audio.data;

          // 将 Base64 解码写入本地临时音频文件
          const fs = wx.getFileSystemManager();
          const filePath = `${wx.env.USER_DATA_PATH}/mimo_tts_${Date.now()}.wav`;

          fs.writeFile({
            filePath,
            data: audioBase64,
            encoding: 'base64',
            success: () => resolve(filePath),
            fail: (err) => reject(new Error('写入本地音频失败: ' + err.errMsg))
          });
        } else {
          reject(new Error(`TTS HTTP ${res.statusCode}`));
        }
      },
      fail(err) {
        reject(err);
      }
    });
  });
}

module.exports = { chat, asr, tts };
