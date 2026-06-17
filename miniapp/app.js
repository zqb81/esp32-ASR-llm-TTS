// app.js — ESP32 IoT 智能语音控制小程序 (全局连接与数据分发管理)
const mqttLib = require('./utils/mqtt')

App({
  onLaunch() {
    // 检测 AI 服务器是否就绪
    this.checkServer()
  },

  globalData: {
    serverUrl: 'https://iot.lzjqpb.icu',   // server.py 地址
    mqttHost: 'iot.lzjqpb.icu',
    mqttPort: 443,    // WSS over HTTPS (Nginx 代理)
    aiOnline: false,
    mqttConnected: false,
    espOnline: false,

    // 传感器物理遥测
    sensor: { temperature: '--', humidity: '--', light: '--' },

    // ESP32 外设物理就绪状态
    devices: { dht: false, light: false, motor: false, rgb: false, oled: false, buzzer: false },

    // 设备开关及阈值控制值
    deviceState: { fan: false, fan_speed: 512, buzzer: false, led: false, rgb: false, threshold: 30, threshold_humi: 80, threshold_light: 500, rgb_brightness: 100 },
    lastFanSpeed: 50,
    lastRgbBrightness: 100,


    // 对话历史
    chatHistory: [],

    // 全局 MQTT 日志
    logs: [],

    // 遥测数据上报快照
    publishStatus: '等待连接...',
    publishSensor: '暂无',
    publishAlert: '无',
    publishVoice: '暂无',
    publishTime: '--:--:--',

    // 活跃页面的回调监听列表
    listeners: []
  },

  _mqttClient: null,
  _lastSensorTime: 0,
  _offlineTimer: null,

  // ============================================================
  //  MQTT 全局连接管理
  // ============================================================
  connectMqtt(host, port) {
    if (this._mqttClient && this.globalData.mqttConnected) return

    this.globalData.mqttHost = host || this.globalData.mqttHost
    this.globalData.mqttPort = port || this.globalData.mqttPort

    const client = mqttLib.createClient(this.globalData.mqttHost, this.globalData.mqttPort)

    client.onConnect = () => {
      this.globalData.mqttConnected = true
      client.subscribe('esp32/iot/#')
      this.addLog('SYS', 'MQTT 连接成功 ✓', false)
      this.notifyListeners('connect', null)

      // 启动 ESP32 在线判定定时器
      if (this._offlineTimer) clearInterval(this._offlineTimer)
      this._offlineTimer = setInterval(() => {
        if (this.globalData.espOnline && this._lastSensorTime > 0 &&
          Date.now() - this._lastSensorTime > 35000) {
          this.globalData.espOnline = false
          this.addLog('SYS', 'ESP32 超过35s无数据，判定离线', true)
          this.notifyListeners('statusChange', { espOnline: false })
        }
      }, 5000)
    }

    client.onMessage = (topic, msg) => {
      this.handleMqttMessage(topic, msg)
    }

    client.onClose = () => {
      this.globalData.mqttConnected = false
      this.globalData.espOnline = false
      this.addLog('SYS', 'MQTT 连接已断开', false)
      this.notifyListeners('close', null)
      if (this._offlineTimer) {
        clearInterval(this._offlineTimer)
        this._offlineTimer = null
      }
    }

    client.onError = (err) => {
      this.globalData.mqttConnected = false
      this.addLog('ERR', err.errMsg || '连接错误', true)
      this.notifyListeners('error', err)
    }

    client.onReconnect = () => {
      this.notifyListeners('reconnect', null)
    }

    client.onOffline = () => {
      this.globalData.mqttConnected = false
      this.notifyListeners('offline', null)
    }

    client.connect()
    this._mqttClient = client
  },

  disconnectMqtt() {
    if (this._mqttClient) {
      this._mqttClient.disconnect()
      this._mqttClient = null
    }
    this.globalData.mqttConnected = false
    this.globalData.espOnline = false
    if (this._offlineTimer) {
      clearInterval(this._offlineTimer)
      this._offlineTimer = null
    }
  },

  // 全局发送 MQTT 指令方法
  sendCmd(cmd, payload) {
    if (!this._mqttClient || !this.globalData.mqttConnected) {
      wx.showToast({ title: '未连接 MQTT', icon: 'none' })
      return
    }
    const text = typeof payload === 'string' ? payload : JSON.stringify(payload)
    this._mqttClient.publish('esp32/iot/cmd/' + cmd, text)
    this.addLog('→ esp32/iot/cmd/' + cmd, text || '(空)', false)
  },

  // 接收消息集中处理
  handleMqttMessage(topic, msg) {
    const isAlert = topic.includes('alert')
    this.addLog(topic, msg, isAlert)

    // 传感器遥测数据处理
    if (topic.endsWith('/sensor')) {
      try {
        const d = JSON.parse(msg)
        this._lastSensorTime = Date.now()
        this.globalData.espOnline = true

        this.globalData.sensor = {
          temperature: d.temperature != null ? Number(d.temperature).toFixed(1) : '--',
          humidity: d.humidity != null ? Number(d.humidity).toFixed(1) : '--',
          light: d.light != null ? Number(d.light).toFixed(0) : '--'
        }

        if (d.devices) {
          this.globalData.devices = d.devices
        }

        const sensorText = `温度 ${this.globalData.sensor.temperature}°C · 湿度 ${this.globalData.sensor.humidity}% · 光照 ${this.globalData.sensor.light}`
        this.updatePublish('sensor', sensorText)

        // 分发数据包给活跃页面
        this.notifyListeners('sensorUpdate', {
          sensor: this.globalData.sensor,
          devices: this.globalData.devices,
          espOnline: true
        })
      } catch (e) {
        console.error('解析传感器数据错误:', e)
      }
    }

    // 异常警报数据处理
    if (topic.endsWith('/alert')) {
      try {
        const d = JSON.parse(msg)
        const alertText = `${d.type || 'alert'}: ${d.value}（阈值 ${d.threshold || '--'}）`
        this.updatePublish('alert', alertText)
      } catch (e) { }
    }

    // 在线离线状态处理
    if (topic.endsWith('/status')) {
      try {
        const d = JSON.parse(msg)
        const isOnline = d.state === 'online'
        this.globalData.espOnline = isOnline
        this.updatePublish('status', isOnline ? '在线' : '离线')
        this.notifyListeners('statusChange', { espOnline: isOnline })
      } catch (e) { }
    }

    // AI 语音输出话题
    if (topic === 'esp32/iot/voice/output') {
      const text = msg.trim()
      if (text) {
        this.updatePublish('voice', text)
      }
    }

    // 多端同步: 监听设备状态回执 (ESP32 执行指令后发布的 retained 消息)
    if (topic.startsWith('esp32/iot/state/')) {
      try {
        const device = topic.split('/').pop()
        const d = JSON.parse(msg)
        this.syncDeviceState(device, d)
      } catch (e) {
        console.error('状态同步解析失败:', e)
      }
    }
  },

  // ============================================================
  //  多端同步: 状态回执同步
  // ============================================================
  syncDeviceState(device, state) {
    const gs = this.globalData.deviceState
    switch (device) {
      case 'fan':
      case 'motor':
        gs.fan = state.state === 'on' || (state.speed !== undefined && state.speed > 1)
        if (state.speed !== undefined) {
          gs.fan_speed = state.speed
          const speedPercent = Math.round(state.speed / 10.23)
          if (speedPercent > 0) {
            this.globalData.lastFanSpeed = speedPercent
          }
        }
        break
      case 'buzzer':
        gs.buzzer = state.state === 'on'
        break
      case 'led':
        gs.led = state.state === 'on' || (state.brightness && state.brightness > 0)
        break
      case 'threshold':
        gs.threshold = state.value ?? state.threshold
        break
      case 'threshold_humi':
        gs.threshold_humi = state.value ?? state.threshold
        break
      case 'threshold_light':
        gs.threshold_light = state.value ?? state.threshold
        break
      case 'rgb':
        gs.rgb = state.color !== 'off' && state.state !== 'off'
        if (!gs.rgb) {
          gs.rgb_brightness = 0
        }
        break
      case 'rgb_brightness':
        gs.rgb_brightness = state.value ?? state.threshold ?? 100
        if (gs.rgb_brightness > 0) {
          this.globalData.lastRgbBrightness = gs.rgb_brightness
        }
        gs.rgb = gs.rgb_brightness > 0
        break
    }
    this.notifyListeners('stateSync', { device, state })
    this.addLog('STATE', `${device}: ${JSON.stringify(state)}`, false)
  },

  // ============================================================
  //  多页面数据派发机制 (Listeners)
  // ============================================================
  registerMqttListener(callback) {
    if (typeof callback === 'function' && !this.globalData.listeners.includes(callback)) {
      this.globalData.listeners.push(callback)
    }
  },

  unregisterMqttListener(callback) {
    this.globalData.listeners = this.globalData.listeners.filter(l => l !== callback)
  },

  notifyListeners(type, data) {
    this.globalData.listeners.forEach(cb => {
      try {
        cb(type, data)
      } catch (e) {
        console.error('派发消息异常:', e)
      }
    })
  },

  addLog(topic, msg, isAlert) {
    const time = this.formatTime()
    const logItem = { time, topic, msg, isAlert }
    this.globalData.logs = [logItem, ...this.globalData.logs].slice(0, 50)
    this.notifyListeners('log', logItem)
  },

  updatePublish(kind, value) {
    const time = this.formatTime()
    this.globalData.publishTime = time
    if (kind === 'status') this.globalData.publishStatus = value
    if (kind === 'sensor') this.globalData.publishSensor = value
    if (kind === 'alert') this.globalData.publishAlert = value
    if (kind === 'voice') this.globalData.publishVoice = value
    this.notifyListeners('publish', { kind, value, time })
  },

  checkServer() {
    wx.request({
      url: `${this.globalData.serverUrl}/api/chat`,
      method: 'OPTIONS',
      success: () => {
        this.globalData.aiOnline = true
        this.notifyListeners('aiStatus', true)
      },
      fail: () => {
        this.globalData.aiOnline = false
        this.notifyListeners('aiStatus', false)
      },
    })
  },

  formatTime() {
    const d = new Date()
    const pad = n => String(n).padStart(2, '0')
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  }
})
