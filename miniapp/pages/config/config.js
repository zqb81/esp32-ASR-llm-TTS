// pages/config/config.js — 系统配置与终端日志
const app = getApp()

Page({
  data: {
    // MQTT 连接配置
    mqttConnected: false,
    mqttHost: 'iot.lzjqpb.icu',
    mqttPort: '443',
    brokerOptions: [
      { label: '☁️ 私有 Mosquitto (云端)', value: 'iot.lzjqpb.icu:443' },
      { label: '🇨🇳 EMQX 国内节点', value: 'broker-cn.emqx.io:8084' },
      { label: '🌐 EMQX 国际节点', value: 'broker.emqx.io:8084' },
      { label: 'Mosquitto 测试', value: 'test.mosquitto.org:8081' },
    ],
    brokerIndex: 0,

    // 报警配置
    threshold: 30,
    threshold_humi: 80,
    threshold_light: 500,

    // 外设模组状态
    devices: { dht: false, light: false, motor: false, rgb: false, oled: false, buzzer: false },

    // 遥测快照
    publishStatus: '等待连接...',
    publishSensor: '暂无',
    publishAlert: '无',
    publishVoice: '暂无',
    publishTime: '--:--:--',

    // 日志控制台
    logs: [],
    showLogs: true
  },

  mqttListener: null,

  onShow() {
    // 监听全局 MQTT 消息以同步配置页面 UI
    this.mqttListener = (type, data) => {
      if (type === 'connect' || type === 'close' || type === 'error' || type === 'statusChange') {
        this.setData({
          mqttConnected: app.globalData.mqttConnected,
          devices: app.globalData.devices
        })
      } else if (type === 'log') {
        this.setData({ logs: app.globalData.logs })
      } else if (type === 'publish') {
        this.setData({
          publishStatus: app.globalData.publishStatus,
          publishSensor: app.globalData.publishSensor,
          publishAlert: app.globalData.publishAlert,
          publishVoice: app.globalData.publishVoice,
          publishTime: app.globalData.publishTime
        })
      } else if (type === 'sensorUpdate') {
        this.setData({
          devices: data.devices
        })
      } else if (type === 'stateSync') {
        const gs = app.globalData.deviceState
        this.setData({
          threshold: gs.threshold,
          threshold_humi: gs.threshold_humi,
          threshold_light: gs.threshold_light
        })
      }
    }
    app.registerMqttListener(this.mqttListener)

    // 从全局同步当前最新状态
    this.setData({
      mqttConnected: app.globalData.mqttConnected,
      mqttHost: app.globalData.mqttHost,
      mqttPort: String(app.globalData.mqttPort),
      threshold: app.globalData.deviceState.threshold,
      threshold_humi: app.globalData.deviceState.threshold_humi || 80,
      threshold_light: app.globalData.deviceState.threshold_light || 500,
      devices: app.globalData.devices,
      publishStatus: app.globalData.publishStatus,
      publishSensor: app.globalData.publishSensor,
      publishAlert: app.globalData.publishAlert,
      publishVoice: app.globalData.publishVoice,
      publishTime: app.globalData.publishTime,
      logs: app.globalData.logs
    })

    // 根据当前的 host 和 port 匹配对应的 brokerIndex
    const currentBroker = `${this.data.mqttHost}:${this.data.mqttPort}`
    const idx = this.data.brokerOptions.findIndex(opt => opt.value === currentBroker)
    if (idx !== -1) {
      this.setData({ brokerIndex: idx })
    }
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
  //  配置交互事件
  // ============================================================
  onBrokerChange(e) {
    const idx = e.detail.value
    const opt = this.data.brokerOptions[idx]
    const [host, port] = opt.value.split(':')
    this.setData({ brokerIndex: idx, mqttHost: host, mqttPort: port })
  },

  toggleConnect() {
    if (this.data.mqttConnected) {
      app.disconnectMqtt()
      this.setData({ mqttConnected: false })
    } else {
      wx.showLoading({ title: '正在连接...' })
      app.connectMqtt(this.data.mqttHost, Number(this.data.mqttPort))

      // 延时检测是否连接成功，提示用户
      setTimeout(() => {
        wx.hideLoading()
        this.setData({ mqttConnected: app.globalData.mqttConnected })
      }, 1500)
    }
  },

  onTempSliderChanging(e) {
    this.setData({ threshold: e.detail.value })
  },
  onTempSliderChange(e) {
    const val = e.detail.value
    app.sendCmd('threshold', String(val))
    app.globalData.deviceState.threshold = val
    this.setData({ threshold: val })
  },

  onHumiSliderChanging(e) {
    this.setData({ threshold_humi: e.detail.value })
  },
  onHumiSliderChange(e) {
    const val = e.detail.value
    app.sendCmd('threshold_humi', String(val))
    app.globalData.deviceState.threshold_humi = val
    this.setData({ threshold_humi: val })
  },

  onLightSliderChanging(e) {
    this.setData({ threshold_light: e.detail.value })
  },
  onLightSliderChange(e) {
    const val = e.detail.value
    app.sendCmd('threshold_light', String(val))
    app.globalData.deviceState.threshold_light = val
    this.setData({ threshold_light: val })
  },

  toggleLogs() {
    this.setData({ showLogs: !this.data.showLogs })
  },

  clearLogs() {
    app.globalData.logs = []
    this.setData({ logs: [] })
  }
})
