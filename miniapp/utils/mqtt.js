// utils/mqtt.js — 微信小程序 MQTT over WebSocket 简易客户端
// 支持: connect, subscribe, publish, onMessage, 自动重连

// TextEncoder / TextDecoder 兼容层 (微信小程序环境无全局 TextEncoder)
const _utf8Encode = (str) => {
    const utf8 = unescape(encodeURIComponent(str))
    const arr = new Uint8Array(utf8.length)
    for (let i = 0; i < utf8.length; i++) arr[i] = utf8.charCodeAt(i)
    return arr
}
const _utf8Decode = (bytes) => {
    let str = ''
    for (let i = 0; i < bytes.length; i++) str += String.fromCharCode(bytes[i])
    return decodeURIComponent(escape(str))
}

class MqttClient {
    constructor(options) {
        this.url = options.url           // ws://host:port/mqtt
        this.clientId = options.clientId || ('wx_' + Math.random().toString(16).slice(2, 8))
        this.keepalive = (options.keepalive || 30) * 1000
        this.reconnectPeriod = options.reconnectPeriod || 3000
        this.connectTimeout = options.connectTimeout || 8000

        this.socketTask = null
        this.connected = false
        this.connecting = false
        this.subscriptions = {}          // topic -> qos
        this._keepaliveTimer = null
        this._reconnectTimer = null
        this._msgId = 0
        this._recvBuf = new Uint8Array(0)

        // 回调
        this.onConnect = null
        this.onMessage = null            // (topic, payload) => {}
        this.onClose = null
        this.onError = null
        this.onReconnect = null
        this.onOffline = null
    }

    connect() {
        if (this.connected || this.connecting) return
        this.connecting = true

        this.socketTask = wx.connectSocket({
            url: this.url,
            protocols: ['mqtt'],
            success: () => { },
            fail: (err) => {
                this.connecting = false
                this.onError && this.onError(err)
                this._scheduleReconnect()
            },
        })

        // 超时检测
        const timeoutId = setTimeout(() => {
            if (!this.connected) {
                this.socketTask && this.socketTask.close()
            }
        }, this.connectTimeout)

        this.socketTask.onOpen(() => {
            clearTimeout(timeoutId)
            // 发送 CONNECT 报文
            this._sendConnect()
        })

        this.socketTask.onMessage((res) => {
            this._onData(res.data)
        })

        this.socketTask.onClose(() => {
            clearTimeout(timeoutId)
            this.connected = false
            this.connecting = false
            this._stopKeepalive()
            this.onClose && this.onClose()
            this._scheduleReconnect()
        })

        this.socketTask.onError((err) => {
            clearTimeout(timeoutId)
            this.connecting = false
            this.onError && this.onError(err)
        })
    }

    disconnect() {
        this._stopKeepalive()
        clearTimeout(this._reconnectTimer)
        this.reconnectPeriod = 0  // 阻止重连
        if (this.socketTask) {
            this._sendPacket(this._buildDisconnect())
            setTimeout(() => {
                this.socketTask && this.socketTask.close()
                this.socketTask = null
            }, 200)
        }
        this.connected = false
    }

    subscribe(topic, qos = 0) {
        this.subscriptions[topic] = qos
        if (!this.connected) return
        const id = ++this._msgId
        const packet = this._buildSubscribe(topic, qos, id)
        this._sendPacket(packet)
    }

    publish(topic, payload) {
        if (!this.connected) return
        const id = ++this._msgId
        const packet = this._buildPublish(topic, payload, id, 0)
        this._sendPacket(packet)
    }

    // ============ 内部方法 ============

    _sendConnect() {
        const clientId = this._encodeString(this.clientId)
        // Variable Header: Protocol Name (MQTT), Level 4, Flags (Clean Session), Keep Alive
        const protoName = this._encodeString('MQTT')
        const protoLevel = new Uint8Array([0x04]) // MQTT 3.1.1
        const flags = new Uint8Array([0x02])       // Clean Session
        const keepAlive = new Uint8Array([(this.keepalive / 1000) >> 8, (this.keepalive / 1000) & 0xFF])

        const varHeader = this._concat([protoName, protoLevel, flags, keepAlive])
        const payload = clientId
        const remaining = this._concat([varHeader, payload])
        const header = this._buildFixedHeader(0x10, remaining.length)
        const packet = this._concat([header, remaining])

        this._sendPacket(packet)
        this._startKeepalive()
    }

    _buildSubscribe(topic, qos, msgId) {
        const topicBytes = this._encodeString(topic)
        const idBytes = new Uint8Array([msgId >> 8, msgId & 0xFF])
        const qosBytes = new Uint8Array([qos])
        const varHeader = idBytes
        const payload = this._concat([topicBytes, qosBytes])
        const remaining = this._concat([varHeader, payload])
        const header = this._buildFixedHeader(0x82, remaining.length)
        return this._concat([header, remaining])
    }

    _buildPublish(topic, payload, msgId, qos) {
        const topicBytes = this._encodeString(topic)
        const payloadBytes = typeof payload === 'string'
            ? _utf8Encode(payload)
            : new Uint8Array(payload)
        let varHeader = topicBytes
        if (qos > 0) {
            varHeader = this._concat([topicBytes, new Uint8Array([msgId >> 8, msgId & 0xFF])])
        }
        const remaining = this._concat([varHeader, payloadBytes])
        const typeByte = 0x30 | (qos << 1)
        const header = this._buildFixedHeader(typeByte, remaining.length)
        return this._concat([header, remaining])
    }

    _buildDisconnect() {
        return new Uint8Array([0xE0, 0x00])
    }

    _buildFixedHeader(type, length) {
        const bytes = []
        bytes.push(type)
        do {
            let encodedByte = length % 128
            length = Math.floor(length / 128)
            if (length > 0) encodedByte |= 0x80
            bytes.push(encodedByte)
        } while (length > 0)
        return new Uint8Array(bytes)
    }

    _encodeString(str) {
        const encoded = _utf8Encode(str)
        const len = encoded.length
        const result = new Uint8Array(2 + len)
        result[0] = len >> 8
        result[1] = len & 0xFF
        result.set(encoded, 2)
        return result
    }

    _concat(arrays) {
        let totalLen = 0
        for (const arr of arrays) totalLen += arr.length
        const result = new Uint8Array(totalLen)
        let offset = 0
        for (const arr of arrays) {
            result.set(arr, offset)
            offset += arr.length
        }
        return result
    }

    _sendPacket(data) {
        if (!this.socketTask) return
        // 转为 ArrayBuffer
        const buffer = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)
        this.socketTask.send({ data: buffer })
    }

    _startKeepalive() {
        this._stopKeepalive()
        this._keepaliveTimer = setInterval(() => {
            if (this.connected) {
                // PINGREQ
                this._sendPacket(new Uint8Array([0xC0, 0x00]))
            }
        }, this.keepalive)
    }

    _stopKeepalive() {
        clearInterval(this._keepaliveTimer)
        this._keepaliveTimer = null
    }

    _scheduleReconnect() {
        if (this.reconnectPeriod <= 0) return
        this.onOffline && this.onOffline()
        this._reconnectTimer = setTimeout(() => {
            this.connecting = false
            this.onReconnect && this.onReconnect()
            this.connect()
        }, this.reconnectPeriod)
    }

    // ============ 数据解析 ============

    _onData(data) {
        // data 是 ArrayBuffer
        const newBytes = new Uint8Array(data)
        // 追加到缓冲区
        const merged = new Uint8Array(this._recvBuf.length + newBytes.length)
        merged.set(this._recvBuf)
        merged.set(newBytes, this._recvBuf.length)
        this._recvBuf = merged

        while (this._recvBuf.length > 0) {
            const result = this._parsePacket(this._recvBuf)
            if (!result) break
            this._handlePacket(result.type, result.payload)
            this._recvBuf = this._recvBuf.slice(result.consumed)
        }
    }

    _parsePacket(buf) {
        if (buf.length < 2) return null
        const type = (buf[0] >> 4) & 0x0F
        let multiplier = 1
        let length = 0
        let idx = 1
        let encodedByte
        do {
            if (idx >= buf.length) return null
            encodedByte = buf[idx++]
            length += (encodedByte & 0x7F) * multiplier
            multiplier *= 128
        } while (encodedByte & 0x80)

        if (buf.length < idx + length) return null

        const payload = buf.slice(idx, idx + length)
        return { type, payload, consumed: idx + length }
    }

    _handlePacket(type, payload) {
        switch (type) {
            case 2: // CONNACK
                this.connected = true
                this.connecting = false
                this.onConnect && this.onConnect()
                // 重新订阅
                for (const [topic, qos] of Object.entries(this.subscriptions)) {
                    const id = ++this._msgId
                    this._sendPacket(this._buildSubscribe(topic, qos, id))
                }
                break

            case 3: // PUBLISH
                this._handlePublish(payload)
                break

            case 4: // PUBACK
                break

            case 9: // SUBACK
                break

            case 13: // PINGRESP
                break

            default:
                break
        }
    }

    _handlePublish(payload) {
        if (payload.length < 2) return
        const topicLen = (payload[0] << 8) | payload[1]
        if (payload.length < 2 + topicLen) return
        const topicBytes = payload.slice(2, 2 + topicLen)
        const topic = _utf8Decode(topicBytes)
        const msgPayload = payload.slice(2 + topicLen)
        const msg = _utf8Decode(msgPayload)
        this.onMessage && this.onMessage(topic, msg)
    }
}

/**
 * 创建 MQTT 客户端
 */
function createClient(host, port) {
    // WSS: 443 (Nginx HTTPS), 8084 (EMQX WSS)
    // WS:  8083 (EMQX WS), 8080 (Mosquitto WS), 9001 (本地 Mosquitto WS)
    const isSecure = (port === 443 || port === 8084)
    const protocol = isSecure ? 'wss' : 'ws'
    const url = `${protocol}://${host}:${port}/mqtt`
    return new MqttClient({
        url,
        clientId: 'wx_' + Math.random().toString(16).slice(2, 8),
        keepalive: 30,
        connectTimeout: 8000,
        reconnectPeriod: 3000,
    })
}

module.exports = { createClient, MqttClient }
