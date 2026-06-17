// pages/index/index.js — ESP32 IoT 仪表盘控制面板
const app = getApp()

Page({
  data: {
    // 连接状态
    mqttConnected: false,
    mqttStatus: '未连接',
    espOnline: false,
    aiOnline: false,

    // 传感器数值
    temperature: '--',
    humidity: '--',
    light: '--',
    dhtOnline: false,
    lightOnline: false,
    tempAlert: false,

    // 设备开关及值
    fan: false,
    fanSpeed: 50,
    buzzer: false,
    led: false,
    rgb: false,
    threshold: 30,
    rgbBrightness: 100,


    refresherTriggered: false
  },

  mqttListener: null,

  onShow() {
    // 注册全局 MQTT 消息订阅监听
    this.mqttListener = (type, data) => {
      if (type === 'connect' || type === 'close' || type === 'error' || type === 'statusChange') {
        this.setData({
          mqttConnected: app.globalData.mqttConnected,
          mqttStatus: app.globalData.mqttConnected ? '已连接' : '已断开',
          espOnline: app.globalData.espOnline,
          aiOnline: app.globalData.aiOnline
        })
      } else if (type === 'sensorUpdate') {
        this.setData({
          temperature: data.sensor.temperature,
          humidity: data.sensor.humidity,
          light: data.sensor.light,
          dhtOnline: data.devices.dht,
          lightOnline: data.devices.light,
          tempAlert: data.devices.dht && Number(data.sensor.temperature) >= this.data.threshold,
          espOnline: data.espOnline
        })
      } else if (type === 'aiStatus') {
        this.setData({ aiOnline: data })
      } else if (type === 'stateSync') {
        // 多端同步: 其他端操作后实时更新本端 UI
        const gs = app.globalData.deviceState
        this.setData({
          fan: gs.fan,
          fanSpeed: app.globalData.lastFanSpeed || 50,
          buzzer: gs.buzzer,
          led: gs.led,
          rgb: gs.rgb,
          threshold: gs.threshold,
          rgbBrightness: app.globalData.lastRgbBrightness || 100
        })
      }
    }
    app.registerMqttListener(this.mqttListener)

    // 从全局数据同步初始状态
    this.setData({
      mqttConnected: app.globalData.mqttConnected,
      mqttStatus: app.globalData.mqttConnected ? '已连接' : '已断开',
      espOnline: app.globalData.espOnline,
      aiOnline: app.globalData.aiOnline,
      temperature: app.globalData.sensor.temperature,
      humidity: app.globalData.sensor.humidity,
      light: app.globalData.sensor.light,
      dhtOnline: app.globalData.devices.dht,
      lightOnline: app.globalData.devices.light,
      tempAlert: app.globalData.devices.dht && Number(app.globalData.sensor.temperature) >= app.globalData.deviceState.threshold,
      fan: app.globalData.deviceState.fan,
      fanSpeed: app.globalData.lastFanSpeed || 50,
      buzzer: app.globalData.deviceState.buzzer,
      led: app.globalData.deviceState.led,
      rgb: app.globalData.deviceState.rgb,
      threshold: app.globalData.deviceState.threshold,
      rgbBrightness: app.globalData.lastRgbBrightness || 100
    })

  },

  onHide() {
    if (this.mqttListener) {
      app.unregisterMqttListener(this.mqttListener)
    }
  },

  onUnload() {
    if (this.mqttListener) {
      app.unregisterMqttListener(this.mqttListener)
    }
  },

  // ============================================================
  //  设备控制事件
  // ============================================================
  onFanToggle(e) {
    const val = e.detail.value
    let speed = this.data.fanSpeed
    if (val) {
      // 开启时，如果记录的转速小于等于 0，则默认使用 50
      if (speed <= 0) speed = 50
      const espSpeed = Math.round(speed * 10.23)
      app.sendCmd('fan', String(espSpeed))
      app.globalData.deviceState.fan = true
      app.globalData.deviceState.fan_speed = espSpeed
      app.globalData.lastFanSpeed = speed
      this.setData({ fan: true, fanSpeed: speed })
    } else {
      // 关闭时发送 off，关闭风扇
      app.sendCmd('fan', 'off')
      app.globalData.deviceState.fan = false
      this.setData({ fan: false })
    }
  },
  
  onFanSpeedChange(e) {
    const val = e.detail.value
    if (val > 0) {
      const espSpeed = Math.round(val * 10.23)
      app.sendCmd('fan', String(espSpeed))
      app.globalData.deviceState.fan = true
      app.globalData.deviceState.fan_speed = espSpeed
      app.globalData.lastFanSpeed = val
      this.setData({ fan: true, fanSpeed: val })
    } else {
      app.sendCmd('fan', 'off')
      app.globalData.deviceState.fan = false
      app.globalData.deviceState.fan_speed = 0
      this.setData({ fan: false })
    }
  },

  onBuzzerToggle(e) {
    const val = e.detail.value
    app.sendCmd('buzzer', val ? 'on' : 'off')
    app.globalData.deviceState.buzzer = val
    this.setData({ buzzer: val })
  },

  onLedToggle(e) {
    const val = e.detail.value
    app.sendCmd('led', val ? '100' : '0')
    app.globalData.deviceState.led = val
    this.setData({ led: val })
  },

  onRgbToggle(e) {
    const val = e.detail.value
    let brightness = this.data.rgbBrightness
    if (val) {
      if (brightness <= 0) brightness = 100
      app.sendCmd('rgb_brightness', String(brightness))
      app.sendCmd('rgb', { action: 'rgb', color: 'welcome' })
      app.globalData.deviceState.rgb = true
      app.globalData.deviceState.rgb_brightness = brightness
      app.globalData.lastRgbBrightness = brightness
      this.setData({ rgb: true, rgbBrightness: brightness })
    } else {
      app.sendCmd('rgb_brightness', '0')
      app.sendCmd('rgb', { action: 'rgb', color: 'off' })
      app.globalData.deviceState.rgb = false
      app.globalData.deviceState.rgb_brightness = 0
      this.setData({ rgb: false })
    }
  },

  onRgbPreset(e) {
    const color = e.currentTarget.dataset.color
    app.sendCmd('rgb', { action: 'rgb', color })
    app.addLog('ACT', `RGB 预设 → ${color}`, false)
  },

  onRgbAnim(e) {
    const anim = e.currentTarget.dataset.anim
    const r = Number(e.currentTarget.dataset.r || 255)
    const g = Number(e.currentTarget.dataset.g || 255)
    const b = Number(e.currentTarget.dataset.b || 255)
    const payload = { action: 'rgb', color: anim }
    if (anim !== 'rainbow' && anim !== 'welcome') {
      payload.r = r
      payload.g = g
      payload.b = b
    }
    app.sendCmd('rgb', payload)
    app.addLog('ACT', `RGB 动画 → ${anim}`, false)
  },

  onRgbBrightnessChange(e) {
    const val = e.detail.value
    if (val > 0) {
      app.sendCmd('rgb_brightness', String(val))
      if (!this.data.rgb) {
        app.sendCmd('rgb', { action: 'rgb', color: 'welcome' })
        app.globalData.deviceState.rgb = true
      }
      app.globalData.deviceState.rgb_brightness = val
      app.globalData.lastRgbBrightness = val
      this.setData({ rgb: true, rgbBrightness: val })
    } else {
      app.sendCmd('rgb_brightness', '0')
      app.sendCmd('rgb', { action: 'rgb', color: 'off' })
      app.globalData.deviceState.rgb = false
      app.globalData.deviceState.rgb_brightness = 0
      this.setData({ rgb: false })
    }
  },

  onRgbBrightnessChanging(e) {
    const val = e.detail.value
    if (val > 0) {
      this.setData({ rgb: true, rgbBrightness: val })
    } else {
      this.setData({ rgb: false })
    }
  },

  onFanSpeedChanging(e) {
    const val = e.detail.value
    if (val > 0) {
      this.setData({ fan: true, fanSpeed: val })
    } else {
      this.setData({ fan: false })
    }
  },

  onPollData() {
    app.sendCmd('poll', '')
  },

  onCardTap(e) {
    if (!this.data.mqttConnected) {
      wx.showToast({
        title: '请先连接 MQTT',
        icon: 'error',
        duration: 2000
      })
    }
  },

  // 下拉刷新
  onRefresherRefresh() {
    this.onPollData()
    this.setData({ refresherTriggered: true })
    setTimeout(() => {
      this.setData({ refresherTriggered: false })
      wx.showToast({
        title: '已请求刷新数据',
        icon: 'success',
        duration: 1000
      })
    }, 1000)
  }
})
