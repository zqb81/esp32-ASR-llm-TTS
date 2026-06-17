// pages/ai/ai.js — AI 语音管家
const app = getApp()
const api = require('../../utils/api')

Page({
  data: {
    // 聊天历史气泡
    chatMessages: [],
    inputText: '',
    isChatting: false,
    chatStatus: '按住说话',
    inputMode: 'text', // 'text' 或 'voice'

    // 录音状态
    isRecording: false,
    voiceStatus: '按住 说话',

    // 自动播报
    autoSpeak: true,
    scrollToMsg: '',

    // 连接状态
    aiOnline: false,
    mqttConnected: false
  },

  _recorderManager: null,
  _recordStartTime: 0,
  _mqttCallback: null,

  onLoad() {
    this._recorderManager = wx.getRecorderManager()
    this._setupRecorder()

    // 监听 MQTT 连接及 AI 在线状态变更
    this._mqttCallback = (type, data) => {
      if (type === 'aiStatus') {
        this.setData({ aiOnline: data })
      } else if (type === 'connect' || type === 'close' || type === 'error' || type === 'offline') {
        this.setData({ mqttConnected: app.globalData.mqttConnected })
      }
    }
  },

  onShow() {
    // 注册全局 MQTT/AI 状态监听器
    app.registerMqttListener(this._mqttCallback)
    app.checkServer()

    // 同步全局聊天历史与自动播报状态
    this.setData({
      chatMessages: app.globalData.chatMessages || [],
      autoSpeak: app.globalData.autoSpeak !== undefined ? app.globalData.autoSpeak : true,
      aiOnline: app.globalData.aiOnline,
      mqttConnected: app.globalData.mqttConnected
    })

    if (this.data.chatMessages.length > 0) {
      setTimeout(() => {
        this.setData({ scrollToMsg: `msg-${this.data.chatMessages.length - 1}` })
      }, 150)
    }
  },

  onHide() {
    app.unregisterMqttListener(this._mqttCallback)
  },

  onUnload() {
    app.unregisterMqttListener(this._mqttCallback)
  },

  // ============================================================
  //  AI 对话与指令下发
  // ============================================================
  onInputChange(e) {
    this.setData({ inputText: e.detail.value })
  },

  switchInputMode(e) {
    const nextMode = this.data.inputMode === 'text' ? 'voice' : 'text'
    wx.vibrateShort({ type: 'light' })
    if (nextMode === 'voice') {
      wx.hideKeyboard()
    }
    this.setData({ inputMode: nextMode })
  },

  // 获取当前的系统物理状态传给 AI
  _getDeviceState() {
    return {
      fan: app.globalData.deviceState.fan,
      buzzer: app.globalData.deviceState.buzzer,
      led: app.globalData.deviceState.led,
      threshold: app.globalData.deviceState.threshold,
      temperature: app.globalData.sensor.temperature,
      humidity: app.globalData.sensor.humidity,
      light: app.globalData.sensor.light,
      mqttConnected: app.globalData.mqttConnected,
      espOnline: app.globalData.espOnline,
      aiOnline: app.globalData.aiOnline
    }
  },

  async onSendText() {
    const text = this.data.inputText.trim()
    if (!text) return
    this.setData({ inputText: '', isChatting: true, chatStatus: 'AI 思考中...' })
    this._addChatMsg('user', text)

    try {
      const data = await api.chat(text, this._getDeviceState())

      if (data.error) {
        this._addChatMsg('system', '错误: ' + data.error)
      } else {
        this._addChatMsg('assistant', data.reply)
        if (this.data.autoSpeak) this._speakText(data.reply)
        if (data.actions && data.actions.length > 0) {
          this._executeActions(data.actions)
          this._addChatMsg('system', `已执行 ${data.actions.length} 条指令`)
        }
      }
    } catch (e) {
      this._addChatMsg('system', 'AI 服务未就绪，请检查 API Key')
    } finally {
      this.setData({ isChatting: false, chatStatus: '按住说话' })
    }
  },

  _executeActions(actions) {
    for (const act of actions) {
      const a = act.action
      if (a === 'fan' || a === 'motor') {
        const state = act.state || 'on'
        app.sendCmd('fan', state)
        app.globalData.deviceState.fan = (state === 'on')
      } else if (a === 'led') {
        const val = act.state === 'on' ? 100 : 0
        app.sendCmd('led', String(val))
        app.globalData.deviceState.led = (act.state === 'on')
      } else if (a === 'buzzer') {
        const state = act.state || 'on'
        app.sendCmd('buzzer', state)
        app.globalData.deviceState.buzzer = (state === 'on')
      } else if (a === 'rgb') {
        const payload = act.color
          ? { action: 'rgb', color: act.color }
          : { action: 'rgb', r: Number(act.r || 0), g: Number(act.g || 0), b: Number(act.b || 0) }
        app.sendCmd('rgb', payload)
      }
    }
  },

  // ============================================================
  //  微信同款“按住说话”录音处理
  // ============================================================
  _setupRecorder() {
    const rm = this._recorderManager
    rm.onStart(() => {
      this.setData({ isRecording: true, voiceStatus: '正在录音...' })
      this._addChatMsg('user', '(正在录音...)')
    })

    rm.onStop((res) => {
      this.setData({ isRecording: false, voiceStatus: '识别中...' })

      const duration = Date.now() - this._recordStartTime
      if (duration < 1000) {
        const msgs = this.data.chatMessages.filter(m => !(m.role === 'user' && m.text === '(正在录音...)'))
        this.setData({ chatMessages: msgs, voiceStatus: '按住 说话' })
        app.globalData.chatMessages = msgs
        wx.showToast({
          title: '说话时间太短',
          icon: 'none',
          duration: 1000
        })
        return
      }

      if (res.tempFilePath) {
        this._sendToASR(res.tempFilePath)
      } else {
        this._addChatMsg('system', '录音失败')
        this.setData({ voiceStatus: '按住 说话' })
      }
    })

    rm.onError((err) => {
      this.setData({ isRecording: false, voiceStatus: '按住 说话' })
      const msgs = this.data.chatMessages.filter(m => !(m.role === 'user' && m.text === '(正在录音...)'))
      this.setData({ chatMessages: msgs })
      app.globalData.chatMessages = msgs
      this._addChatMsg('system', '录音错误: ' + (err.errMsg || '未知'))
    })
  },

  onVoiceStart() {
    this._recordStartTime = Date.now()
    wx.vibrateShort({ type: 'medium' })
    this._recorderManager.start({
      duration: 15000,
      sampleRate: 16000,
      numberOfChannels: 1,
      format: 'wav'
    })
  },

  onVoiceEnd() {
    this._recorderManager.stop()
  },

  onVoiceCancel() {
    this._recorderManager.stop()
  },

  async _sendToASR(filePath) {
    this.setData({ isChatting: true, chatStatus: '识别中...' })
    try {
      const data = await api.asr(filePath)
      const msgs = this.data.chatMessages.filter(m => !(m.role === 'user' && m.text === '(正在录音...)'))
      this.setData({ chatMessages: msgs })
      app.globalData.chatMessages = msgs

      this.setData({ chatStatus: 'AI 思考中...' })
      this._addChatMsg('user', data.text)

      const res = await api.chat(data.text, this._getDeviceState())
      if (res.error) {
        this._addChatMsg('system', '错误: ' + res.error)
      } else {
        this._addChatMsg('assistant', res.reply)
        if (this.data.autoSpeak) this._speakText(res.reply)
        if (res.actions && res.actions.length > 0) {
          this._executeActions(res.actions)
          this._addChatMsg('system', `已执行 ${res.actions.length} 条指令`)
        }
      }
    } catch (e) {
      this._addChatMsg('system', '语音识别/对话接口响应失败')
    } finally {
      this.setData({ isChatting: false, voiceStatus: '按住 说话', chatStatus: '按住说话' })
    }
  },

  // TTS 播报
  async _speakText(text, forcePlay = false) {
    if (!this.data.autoSpeak && !forcePlay) return
    try {
      const filePath = await api.tts(text)
      const audio = wx.createInnerAudioContext()
      audio.src = filePath
      audio.play()
      audio.onEnded(() => { audio.destroy() })
      audio.onError(() => { audio.destroy() })
    } catch (e) {
      console.warn('TTS 失败:', e)
    }
  },

  onReplayVoice(e) {
    const text = e.currentTarget.dataset.text
    const role = e.currentTarget.dataset.role
    if (role !== 'assistant') return
    wx.showToast({
      title: '语音播报中...',
      icon: 'none',
      duration: 1000
    })
    this._speakText(text, true)
  },

  toggleAutoSpeak() {
    const speak = !this.data.autoSpeak
    this.setData({ autoSpeak: speak })
    app.globalData.autoSpeak = speak
  },

  _addChatMsg(role, text) {
    const msgs = this.data.chatMessages.concat([{
      role,
      text,
      time: this._formatTime(),
    }])
    this.setData({ chatMessages: msgs })
    app.globalData.chatMessages = msgs
    this.setData({ scrollToMsg: `msg-${msgs.length - 1}` })
  },

  _formatTime() {
    const d = new Date()
    const pad = n => String(n).padStart(2, '0')
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  }
})
