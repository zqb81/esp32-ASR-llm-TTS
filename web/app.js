// ============================================================
//  app.js — ESP32 IoT 控制面板
//  MQTT: IoT 设备控制 + 传感器显示
//  本地API: AI 对话 (统一 server.py)
// ============================================================

let client = null;
let espOnline = false;
let lastSensorTime = 0;

const TOPIC_SUB = 'esp32/iot/#';
const TOPIC_VOICE_OUT = 'esp32/iot/voice/output';
const CMD_PREFIX = 'esp32/iot/cmd/';
// 动态检测 API 基础地址（支持云部署与本地 EXE 运行）
const API_BASE = (window.location.hostname === 'iot.lzjqpb.icu' || (window.location.hostname === 'localhost' && window.location.port === '8080') || (window.location.hostname === '127.0.0.1' && window.location.port === '8080')) 
    ? '' 
    : 'http://iot.lzjqpb.icu';

const API_CHAT = API_BASE + '/api/chat';
const API_TTS = API_BASE + '/api/tts';
const API_SHUTDOWN = API_BASE + '/api/shutdown';

let deviceState = { fan: false, buzzer: false, led: false, rgb: false, threshold: 30, threshold_humi: 80, threshold_light: 500, rgb_brightness: 100 };
let lastFanSpeed = 50;
let lastRgbBrightness = 100;
let latestSensor = { temperature: null, humidity: null, light: null };
let latestPublish = {
    status: '未连接',
    sensor: '暂无',
    alert: '无',
    voice: '暂无',
    time: '--:--:--',
};

// 启动时检测 AI 服务器是否可用
fetch(API_CHAT, { method: 'OPTIONS' })
    .then(() => setAIStatus(true))
    .catch(() => setAIStatus(false));

function setAIStatus(online) {
    document.getElementById('aiDot').className = 'dot ' + (online ? 'on' : 'off');
    document.getElementById('aiStatusText').textContent = online ? 'AI 已就绪' : 'AI 未连接';
}

// 缓存最新传感器数据（传给 AI）
const DEVICE_NAMES = {
    dht: 'DHT 温湿度', light: '光敏传感器', motor: '电机/风扇',
    rgb: 'RGB LED', oled: 'OLED 屏幕', buzzer: '蜂鸣器',
};

// ============================================================
//  MQTT 连接
// ============================================================
function selectBroker(val) {
    const [host, port] = val.split(':');
    document.getElementById('brokerUrl').value = host;
    document.getElementById('brokerPort').value = port;
}

function toggleConnect() {
    console.log('[DEBUG] toggleConnect called, client:', !!client);
    // 断开
    if (client) {
        console.log('[DEBUG] Disconnecting...');
        const c = client;
        client = null; // 先清空引用
        try { c.end(true); } catch (e) { console.warn('end error:', e); }
        updateMqttStatus(false);
        const btn = document.getElementById('connBtn');
        btn.textContent = '连接';
        btn.disabled = false;
        document.getElementById('statusDot').style.background = '';
        console.log('[DEBUG] Button text set to:', btn.textContent);
        return;
    }

    // 智能选择 Broker（国内用 cn 更快）
    let host = document.getElementById('brokerUrl').value;
    let port = parseInt(document.getElementById('brokerPort').value);

    // 如果是 emqx.io 公共 Broker，自动切换到国内节点
    if (host.includes('broker.emqx.io')) {
        host = 'broker-cn.emqx.io';
        port = 8083;
        document.getElementById('brokerUrl').value = host;
        document.getElementById('brokerPort').value = port;
    }

    // HTTPS 下自动使用 WSS，否则用 WS
    const isSecure = window.location.protocol === 'https:';
    const wsProto = isSecure ? 'wss' : 'ws';
    const url = `${wsProto}://${host}:${port}/mqtt`;
    addLog('SYS', `连接 ${url} ...`, false);

    // 显示“连接中”状态
    document.getElementById('statusText').textContent = '连接中...';
    document.getElementById('statusDot').className = 'dot';
    document.getElementById('statusDot').style.background = 'var(--yellow)';
    document.getElementById('connBtn').disabled = true;
    const startTime = Date.now();

    client = mqtt.connect(url, {
        clientId: 'web_ctrl_' + Math.random().toString(16).slice(2, 8),
        keepalive: 30,        // 30s ping，更快检测断线
        clean: true,
        connectTimeout: 8000, // 8s 超时，不无限等
        reconnectPeriod: 3000, // 3s 重连
    });

    client.on('connect', () => {
        const elapsed = Date.now() - startTime;
        updateMqttStatus(true);
        document.getElementById('connBtn').textContent = '断开';
        document.getElementById('connBtn').disabled = false;
        document.getElementById('statusDot').style.background = '';
        addLog('SYS', `MQTT 连接成功 ✓ (${elapsed}ms)`, false);
        client.subscribe(TOPIC_SUB);
    });

    client.on('reconnect', () => {
        document.getElementById('statusText').textContent = '重连中...';
    });

    client.on('offline', () => {
        document.getElementById('statusText').textContent = '已断开';
    });

    client.on('message', (topic, payload) => {
        const msg = payload.toString();
        const isAlert = topic.includes('alert');
        addLog(topic, msg, isAlert);

        if (topic.endsWith('/sensor')) {
            try {
                const d = JSON.parse(msg);
                lastSensorTime = Date.now();
                setEspOnline(true);
                const sensorText = `温度 ${Number(d.temperature ?? 0).toFixed(1)}°C · 湿度 ${Number(d.humidity ?? 0).toFixed(1)}% · 光照 ${Number(d.light ?? 0).toFixed(0)}`;
                latestSensor.temperature = d.temperature;
                latestSensor.humidity = d.humidity;
                latestSensor.light = d.light;
                renderExtraSensors(d);
                updatePublishSummary('sensor', sensorText);

                const dhtOnline = d.devices ? d.devices.dht : false;
                const lightOnline = d.devices ? d.devices.light : false;
                updateSensor('tempVal', d.temperature, 'tempCard', deviceState.threshold, dhtOnline, 'tempBadge');
                updateSensor('humiVal', d.humidity, 'humiCard', 80, dhtOnline, 'humiBadge');
                updateSensor('lightVal', d.light, 'lightCard', null, lightOnline, 'lightBadge');
                if (d.devices) updateDevicePanel(d.devices);
            } catch (e) { console.error(e); }
        }

        if (topic.endsWith('/alert')) {
            try {
                const d = JSON.parse(msg);
                const alertText = `${d.type || 'alert'}: ${d.value}${d.type === 'temperature' ? '°C' : d.type === 'humidity' ? '%' : ''}（阈值 ${d.threshold || '--'}）`;
                updatePublishSummary('alert', alertText);
                if (d.type === 'temperature') document.getElementById('tempCard').classList.add('alert');
            } catch (e) { }
        }

        if (topic.endsWith('/status')) {
            try {
                const status = JSON.parse(msg).state === 'online' ? '在线' : '离线';
                setEspOnline(JSON.parse(msg).state === 'online');
                updatePublishSummary('status', status);
            } catch (e) { }
        }

        if (topic === TOPIC_VOICE_OUT) {
            const voiceText = msg.trim();
            if (voiceText) {
                updatePublishSummary('voice', voiceText);
            }
        }

        // 多端同步: 监听设备状态回执 (ESP32 执行指令后发布的 retained 消息)
        if (topic.startsWith('esp32/iot/state/')) {
            try {
                const device = topic.split('/').pop();
                const d = JSON.parse(msg);
                syncDeviceState(device, d);
            } catch (e) { console.error('状态同步解析失败:', e); }
        }
    });

    client.on('close', () => {
        updateMqttStatus(false);
        document.getElementById('connBtn').disabled = false;
    });
    client.on('error', (err) => {
        document.getElementById('connBtn').disabled = false;
        updateMqttStatus(false);
        addLog('ERR', err.message, true);
    });
}

// ============================================================
//  IoT 设备控制 (通过 MQTT)
// ============================================================
function revertDeviceUI(cmd) {
    if (cmd === 'fan') {
        const toggle = document.getElementById('fanToggle');
        if (toggle) toggle.checked = deviceState.fan;
        updateDeviceUI('fan', deviceState.fan);
        const slider = document.getElementById('fanSpeedSlider');
        const valDisp = document.getElementById('fanSpeedVal');
        if (slider) slider.value = deviceState.fan ? lastFanSpeed : 0;
        if (valDisp) valDisp.textContent = deviceState.fan ? lastFanSpeed : 0;
        const info = document.getElementById('fanInfo');
        if (info) {
            info.textContent = deviceState.fan ? `运转中` : '已关闭';
            info.style.color = deviceState.fan ? 'var(--green)' : 'var(--muted)';
        }
    } else if (cmd === 'rgb' || cmd === 'rgb_brightness') {
        const toggle = document.getElementById('rgbToggle');
        if (toggle) toggle.checked = deviceState.rgb;
        updateDeviceUI('rgb', deviceState.rgb);
        const slider = document.getElementById('rgbBrightSlider');
        const valDisp = document.getElementById('rgbBrightVal');
        if (slider) slider.value = deviceState.rgb ? lastRgbBrightness : 0;
        if (valDisp) valDisp.textContent = deviceState.rgb ? lastRgbBrightness : 0;
        const info = document.getElementById('rgbInfo');
        if (info) {
            info.textContent = deviceState.rgb ? '已开启' : '已关闭';
            info.style.color = deviceState.rgb ? 'var(--green)' : 'var(--muted)';
        }
    } else if (cmd === 'buzzer') {
        const toggle = document.getElementById('buzzToggle');
        if (toggle) toggle.checked = deviceState.buzzer;
        updateDeviceUI('buzzer', deviceState.buzzer);
    } else if (cmd === 'led') {
        const toggle = document.getElementById('ledToggle');
        if (toggle) toggle.checked = deviceState.led;
        updateDeviceUI('led', deviceState.led);
    } else if (cmd === 'threshold') {
        const slider = document.getElementById('tempThreshSlider');
        const valDisp = document.getElementById('tempThreshVal');
        if (slider) slider.value = deviceState.threshold;
        if (valDisp) valDisp.textContent = deviceState.threshold;
    } else if (cmd === 'threshold_humi') {
        const slider = document.getElementById('humiThreshSlider');
        const valDisp = document.getElementById('humiThreshVal');
        if (slider) slider.value = deviceState.threshold_humi;
        if (valDisp) valDisp.textContent = deviceState.threshold_humi;
    } else if (cmd === 'threshold_light') {
        const slider = document.getElementById('lightThreshSlider');
        const valDisp = document.getElementById('lightThreshVal');
        if (slider) slider.value = deviceState.threshold_light;
        if (valDisp) valDisp.textContent = deviceState.threshold_light;
    }
}

function sendCmd(cmd, payload) {
    if (!client || !client.connected) {
        addLog('ERR', '未连接 MQTT', true);
        revertDeviceUI(cmd);
        return false;
    }

    const text = typeof payload === 'string' ? payload : JSON.stringify(payload);
    client.publish(CMD_PREFIX + cmd, text);
    addLog('→ ' + CMD_PREFIX + cmd, text || '(空)', false);

    if (cmd === 'fan') {
        const isOn = payload === 'on' || (parseInt(payload) > 0);
        updateDeviceUI('fan', isOn);
        const toggle = document.getElementById('fanToggle');
        if (toggle) toggle.checked = isOn;
    }
    if (cmd === 'buzzer') updateDeviceUI('buzzer', payload === 'on');
    if (cmd === 'led') updateDeviceUI('led', parseInt(payload) > 0);
    if (cmd === 'rgb') {
        const isOn = typeof payload === 'string' ? payload !== 'off' : (payload.color !== 'off' && payload.state !== 'off');
        updateDeviceUI('rgb', isOn);
        const toggle = document.getElementById('rgbToggle');
        if (toggle) toggle.checked = isOn;
    }
    if (cmd === 'threshold') deviceState.threshold = parseInt(payload);
    if (cmd === 'rgb_brightness') {
        const val = parseInt(payload);
        deviceState.rgb_brightness = val;
        const isOn = val > 0;
        if (isOn && !deviceState.rgb) {
            updateDeviceUI('rgb', true);
            const toggle = document.getElementById('rgbToggle');
            if (toggle) toggle.checked = true;
        } else if (!isOn) {
            updateDeviceUI('rgb', false);
            const toggle = document.getElementById('rgbToggle');
            if (toggle) toggle.checked = false;
        }
    }
    return true;
}

// 执行 AI 返回的动作指令 (通过 MQTT 发给 ESP32)
function executeActions(actions) {
    if (!client || !client.connected) return;
    for (const act of actions) {
        const a = act.action;
        if (a === 'fan' || a === 'motor') {
            const state = act.state || 'on';
            sendCmd('fan', state);
            updateDeviceUI('fan', state === 'on');
            document.getElementById('fanToggle').checked = state === 'on';
        } else if (a === 'led') {
            const state = act.state || 'on';
            const val = state === 'on' ? 100 : 0;
            sendCmd('led', String(val));
            updateDeviceUI('led', state === 'on');
            const toggle = document.getElementById('ledToggle');
            if (toggle) toggle.checked = state === 'on';
        } else if (a === 'buzzer') {
            const state = act.state || 'on';
            sendCmd('buzzer', state);
            updateDeviceUI('buzzer', state === 'on');
            document.getElementById('buzzToggle').checked = state === 'on';
        } else if (a === 'rgb') {
            const payload = act.color
                ? { action: 'rgb', color: act.color }
                : { action: 'rgb', r: Number(act.r || 0), g: Number(act.g || 0), b: Number(act.b || 0) };
            sendCmd('rgb', payload);
            addLog('ACT', `RGB → ${act.color || `${payload.r},${payload.g},${payload.b}`}`, false);
            const isOn = act.color !== 'off';
            updateDeviceUI('rgb', isOn);
            const toggle = document.getElementById('rgbToggle');
            if (toggle) toggle.checked = isOn;
        }
    }
}

function sendRgbPreset(color) {
    sendCmd('rgb', { action: 'rgb', color });
    addLog('ACT', `RGB 预设 → ${color}`, false);
}

function sendRgbAnim(color, r = 255, g = 255, b = 255) {
    const payload = { action: 'rgb', color: color };
    if (color !== 'rainbow' && color !== 'welcome') {
        payload.r = r;
        payload.g = g;
        payload.b = b;
    }
    sendCmd('rgb', payload);
    addLog('ACT', `RGB 动画 → ${color}`, false);
}

function updateFanUILocal(value) {
    const val = parseInt(value);
    const isOn = val > 0;
    const toggle = document.getElementById('fanToggle');
    if (toggle) toggle.checked = isOn;
    updateDeviceUI('fan', isOn);
}

function updateRgbUILocal(value) {
    const val = parseInt(value);
    const isOn = val > 0;
    const toggle = document.getElementById('rgbToggle');
    if (toggle) toggle.checked = isOn;
    updateDeviceUI('rgb', isOn);
}

function adjustFanSpeed(value) {
    const val = parseInt(value);
    const toggle = document.getElementById('fanToggle');
    if (val > 0) {
        lastFanSpeed = val;
        const espSpeed = Math.round(val * 10.23);
        if (sendCmd('fan', String(espSpeed))) {
            updateDeviceUI('fan', true);
            if (toggle) toggle.checked = true;
        }
    } else {
        if (sendCmd('fan', 'off')) {
            updateDeviceUI('fan', false);
            if (toggle) toggle.checked = false;
        }
    }
}

function toggleFan(checked) {
    const slider = document.getElementById('fanSpeedSlider');
    const valDisp = document.getElementById('fanSpeedVal');
    if (checked) {
        let speed = lastFanSpeed;
        if (speed <= 0) speed = 50;
        const espSpeed = Math.round(speed * 10.23);
        if (sendCmd('fan', String(espSpeed))) {
            updateDeviceUI('fan', true);
            if (slider) slider.value = speed;
            if (valDisp) valDisp.textContent = speed;
        }
    } else {
        if (sendCmd('fan', 'off')) {
            updateDeviceUI('fan', false);
            if (slider) slider.value = 0;
            if (valDisp) valDisp.textContent = 0;
        }
    }
}

function adjustRgbBrightness(value) {
    const val = parseInt(value);
    const toggle = document.getElementById('rgbToggle');
    if (val > 0) {
        lastRgbBrightness = val;
        if (sendCmd('rgb_brightness', String(val))) {
            if (!deviceState.rgb) {
                sendCmd('rgb', { action: 'rgb', color: 'welcome' });
                updateDeviceUI('rgb', true);
                if (toggle) toggle.checked = true;
            }
            document.getElementById('rgbBrightSlider').value = val;
            document.getElementById('rgbBrightVal').textContent = val;
        }
    } else {
        if (sendCmd('rgb_brightness', '0')) {
            sendCmd('rgb', { action: 'rgb', color: 'off' });
            updateDeviceUI('rgb', false);
            if (toggle) toggle.checked = false;
            document.getElementById('rgbBrightSlider').value = 0;
            document.getElementById('rgbBrightVal').textContent = 0;
        }
    }
}

function toggleRgb(checked) {
    const slider = document.getElementById('rgbBrightSlider');
    const valDisp = document.getElementById('rgbBrightVal');
    if (checked) {
        let brightness = lastRgbBrightness;
        if (brightness <= 1) brightness = 100;
        if (sendCmd('rgb_brightness', String(brightness))) {
            sendCmd('rgb', { action: 'rgb', color: 'welcome' });
            updateDeviceUI('rgb', true);
            if (slider) slider.value = brightness;
            if (valDisp) valDisp.textContent = brightness;
        }
    } else {
        if (sendCmd('rgb_brightness', '0')) {
            sendCmd('rgb', { action: 'rgb', color: 'off' });
            updateDeviceUI('rgb', false);
            if (slider) slider.value = 0;
            if (valDisp) valDisp.textContent = 0;
        }
    }
}

function updateDeviceUI(device, isOn) {
    deviceState[device] = isOn;
    const prefixMap = { fan: 'fan', buzzer: 'buzz', led: 'led', rgb: 'rgb' };
    const prefix = prefixMap[device] || device;
    const card = document.getElementById(prefix + 'Card');
    const status = document.getElementById(prefix + 'Status');
    const info = document.getElementById(prefix + 'Info');
    const labels = { fan: '运转中', buzzer: '报警中', led: '已开启', rgb: '已开启' };
    if (card && status && info) {
        if (isOn) {
            card.classList.add('active');
            status.className = 'dev-status on'; status.textContent = 'ON';
            info.textContent = labels[device] || '已开启';
            info.style.color = 'var(--green)';
        } else {
            card.classList.remove('active');
            status.className = 'dev-status off'; status.textContent = 'OFF';
            info.textContent = '已关闭'; info.style.color = 'var(--muted)';
        }
    }
}

// ============================================================
//  多端同步: 状态回执同步
// ============================================================
function syncDeviceState(device, state) {
    switch (device) {
        case 'fan':
        case 'motor': {
            const isOn = state.state === 'on' || (state.speed !== undefined && state.speed > 1);
            deviceState.fan = isOn;
            const toggle = document.getElementById('fanToggle');
            if (toggle) toggle.checked = isOn;
            updateDeviceUI('fan', isOn);
            
            // 同步转速显示
            const rawSpeed = state.speed !== undefined ? state.speed : (isOn ? 512 : 0);
            const speed = Math.round(rawSpeed / 10.23);
            if (speed > 0) {
                lastFanSpeed = speed;
            }
            const speedSlider = document.getElementById('fanSpeedSlider');
            const speedVal = document.getElementById('fanSpeedVal');
            if (isOn) {
                if (speedSlider) speedSlider.value = speed;
                if (speedVal) speedVal.textContent = speed;
            } else {
                if (speedSlider) speedSlider.value = 0;
                if (speedVal) speedVal.textContent = 0;
            }
            const info = document.getElementById('fanInfo');
            if (info) {
                if (isOn) {
                    info.textContent = `转速: ${speed}%`;
                    info.style.color = 'var(--green)';
                } else {
                    info.textContent = '已关闭';
                    info.style.color = 'var(--muted)';
                }
            }
            break;
        }
        case 'buzzer': {
            const isOn = state.state === 'on';
            deviceState.buzzer = isOn;
            const toggle = document.getElementById('buzzToggle');
            if (toggle) toggle.checked = isOn;
            updateDeviceUI('buzzer', isOn);
            break;
        }
        case 'led': {
            const isOn = state.state === 'on' || (state.brightness && state.brightness > 0);
            deviceState.led = isOn;
            const toggle = document.getElementById('ledToggle');
            if (toggle) toggle.checked = isOn;
            updateDeviceUI('led', isOn);
            break;
        }
        case 'threshold': {
            const val = state.value ?? state.threshold;
            if (val !== undefined) {
                deviceState.threshold = val;
                const slider = document.getElementById('tempThreshSlider');
                const valDisp = document.getElementById('tempThreshVal');
                if (slider) slider.value = val;
                if (valDisp) valDisp.textContent = val;
            }
            break;
        }
        case 'threshold_humi': {
            const val = state.value ?? state.threshold;
            if (val !== undefined) {
                deviceState.threshold_humi = val;
                const slider = document.getElementById('humiThreshSlider');
                const valDisp = document.getElementById('humiThreshVal');
                if (slider) slider.value = val;
                if (valDisp) valDisp.textContent = val;
            }
            break;
        }
        case 'threshold_light': {
            const val = state.value ?? state.threshold;
            if (val !== undefined) {
                deviceState.threshold_light = val;
                const slider = document.getElementById('lightThreshSlider');
                const valDisp = document.getElementById('lightThreshVal');
                if (slider) slider.value = val;
                if (valDisp) valDisp.textContent = val;
            }
            break;
        }
        case 'rgb': {
            const isOn = state.color !== 'off' && state.state !== 'off';
            deviceState.rgb = isOn;
            const toggle = document.getElementById('rgbToggle');
            if (toggle) toggle.checked = isOn;
            updateDeviceUI('rgb', isOn);
            addLog('STATE', `RGB 状态同步: ${JSON.stringify(state)}`, false);
            
            // 如果同步为 OFF，亮度滑块归 0
            if (!isOn) {
                const slider = document.getElementById('rgbBrightSlider');
                const valDisp = document.getElementById('rgbBrightVal');
                if (slider) slider.value = 0;
                if (valDisp) valDisp.textContent = 0;
            }
            break;
        }
        case 'rgb_brightness': {
            const val = state.value ?? state.threshold;
            if (val !== undefined) {
                deviceState.rgb_brightness = val;
                if (val > 0) {
                    lastRgbBrightness = val;
                }
                const slider = document.getElementById('rgbBrightSlider');
                const valDisp = document.getElementById('rgbBrightVal');
                if (deviceState.rgb) {
                    if (slider) slider.value = val;
                    if (valDisp) valDisp.textContent = val;
                } else {
                    if (slider) slider.value = 0;
                    if (valDisp) valDisp.textContent = 0;
                }
                
                // 如果亮度大于 0 且开关为 OFF，自动打开开关
                const isOn = val > 0;
                const toggle = document.getElementById('rgbToggle');
                if (toggle && toggle.checked !== isOn) {
                    toggle.checked = isOn;
                    updateDeviceUI('rgb', isOn);
                }
            }
            break;
        }
        default:
            console.log('[SYNC] 未知设备状态:', device, state);
    }
}

function updateSensor(valId, value, cardId, alertThresh, deviceOnline, badgeId) {
    const el = document.getElementById(valId);
    const card = document.getElementById(cardId);
    const badge = document.getElementById(badgeId);
    if (badge) {
        badge.className = 'sensor-badge ' + (deviceOnline ? 'online' : 'offline');
        badge.textContent = deviceOnline ? '在线' : '未连接';
    }
    card.classList.toggle('offline-sensor', !deviceOnline);
    card.classList.remove('alert');
    if (deviceOnline && value !== null && value !== undefined) {
        el.textContent = typeof value === 'number' ? value.toFixed(1) : value;
        if (alertThresh && value >= alertThresh) card.classList.add('alert');
    } else { el.textContent = '--'; }
}

function updateDevicePanel(devices) {
    for (const [key, online] of Object.entries(devices || {})) {
        const chip = document.getElementById('chip-' + key);
        if (!chip) continue;
        const name = DEVICE_NAMES[key] || key;
        chip.className = 'dev-chip ' + (online ? 'online' : 'offline');
        chip.innerHTML = `<div class="chip-dot"></div>${name} <span style="color:var(${online ? '--green' : '--red'});margin-left:4px">${online ? '●' : '✗'}</span>`;
    }
}

function renderExtraSensors(data) {
    const list = document.getElementById('extraSensorList');
    if (!list) return;

    const ignored = new Set(['temperature', 'humidity', 'light', 'devices', 'ts']);
    const extras = Object.entries(data || {})
        .filter(([key]) => !ignored.has(key))
        .map(([key, value]) => ({ key, value }));

    if (extras.length === 0) {
        list.innerHTML = '<div class="extra-empty">暂无额外传感器数据</div>';
        return;
    }

    list.innerHTML = extras.map(({ key, value }) => {
        const displayValue = typeof value === 'number' ? value.toFixed(2) : String(value);
        const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        return `<div class="extra-item"><span class="extra-key">${label}</span><strong>${displayValue}</strong></div>`;
    }).join('');
}

function updatePublishSummary(kind, value) {
    const now = new Date().toLocaleTimeString();
    latestPublish.time = now;

    if (kind === 'status') {
        latestPublish.status = value;
    } else if (kind === 'sensor') {
        latestPublish.sensor = value;
    } else if (kind === 'alert') {
        latestPublish.alert = value;
    } else if (kind === 'voice') {
        latestPublish.voice = value;
    }

    document.getElementById('publishStatus').textContent = latestPublish.status;
    document.getElementById('publishSensor').textContent = latestPublish.sensor;
    document.getElementById('publishAlert').textContent = latestPublish.alert;
    document.getElementById('publishVoice').textContent = latestPublish.voice;
    document.getElementById('publishTime').textContent = latestPublish.time;
}

function setEspOnline(online) {
    espOnline = online;
    const banner = document.getElementById('offlineBanner');
    if (banner) banner.classList.toggle('show', !online);
    const status = document.getElementById('espStatus');
    if (status) status.className = 'esp-status ' + (online ? 'online' : 'offline');
    const dot = document.getElementById('espDot');
    if (dot) dot.className = 'dot ' + (online ? 'on' : 'off');
    const text = document.getElementById('espText');
    if (text) text.textContent = online ? '设备在线' : '设备离线';
}

function updateMqttStatus(connected) {
    document.getElementById('statusDot').className = 'dot ' + (connected ? 'on' : 'off');
    document.getElementById('statusText').textContent = connected ? '已连接' : '未连接';
    if (!connected) setEspOnline(false);
    
    const grid = document.querySelector('.device-grid');
    if (grid) {
        if (connected) grid.classList.remove('offline-lock');
        else grid.classList.add('offline-lock');
    }
    const pollBtn = document.querySelector('.btn-poll');
    if (pollBtn) {
        if (connected) pollBtn.classList.remove('offline-lock');
        else pollBtn.classList.add('offline-lock');
    }
}

function addLog(topic, msg, isAlert) {
    const box = document.getElementById('logBox');
    if (box.querySelector('.empty')) box.innerHTML = '';
    const div = document.createElement('div');
    div.className = 'entry';
    div.innerHTML = `<span class="time">${new Date().toLocaleTimeString()}</span><span class="topic">${topic}</span><span class="payload${isAlert ? ' alert' : ''}">${msg}</span>`;
    box.prepend(div);
    while (box.children.length > 100) box.removeChild(box.lastChild);
}

function clearLog() {
    document.getElementById('logBox').innerHTML = '<div class="empty">日志已清空</div>';
}

setInterval(() => {
    if (espOnline && lastSensorTime > 0 && Date.now() - lastSensorTime > 35000) {
        setEspOnline(false);
        addLog('SYS', 'ESP32 超过 35s 无数据，判定离线', true);
    }
}, 5000);

// ============================================================
//  AI 对话 — 调用统一 server.py
// ============================================================
let isChatting = false;
let chatAbortCtrl = null; // 用于打断正在进行的 AI 回复

function getDeviceState() {
    return {
        fan: deviceState.fan ? "on" : "off",
        buzzer: deviceState.buzzer ? "on" : "off",
        ledValue: deviceState.led,
        threshold: deviceState.threshold,
        temperature: latestSensor.temperature,
        humidity: latestSensor.humidity,
        light: latestSensor.light
    };
}

async function chatWithAI(text) {
    if (isChatting) return;
    isChatting = true;
    chatAbortCtrl = new AbortController();
    setChatStatus('thinking', 'AI 思考中...');
    addChat('user', text);

    try {
        const resp = await fetch(API_CHAT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, sensor: getDeviceState() }),
            signal: chatAbortCtrl.signal,
        });

        let data;
        try {
            data = await resp.json();
        } catch (jsonErr) {
            throw new Error(`HTTP ${resp.status}`);
        }

        setAIStatus(resp.ok);

        if (data.error) {
            addChat('system', '错误: ' + data.error);
        } else {
            addChat('assistant', data.reply);
            speakText(data.reply);
            // 执行 AI 返回的动作指令
            if (data.actions && data.actions.length > 0) {
                executeActions(data.actions);
                addChat('system', `已执行 ${data.actions.length} 条指令`);
            }
        }
    } catch (e) {
        if (e.name === 'AbortError') {
            addChat('system', '(已打断)');
        } else {
            console.error(e);
            setAIStatus(false);
            // 区分网络未连接与接口返回错误
            if (e.message.startsWith('HTTP ')) {
                addChat('system', '接口错误: ' + e.message);
            } else {
                addChat('system', 'AI 服务器未启动，请运行 python server.py');
            }
        }
    } finally {
        isChatting = false;
        chatAbortCtrl = null;
        setChatStatus('idle', '点击说话');
    }
}

// ============================================================
//  语音输入 (AudioContext WAV 录音 + MiMo ASR)
// ============================================================
const API_ASR = API_BASE + '/api/asr';
let isListening = false;
let micStream = null;
let audioContext = null;
let scriptProcessor = null;
let mediaStreamSource = null;
let wavAudioChunks = [];

// WAV 头部写入辅助
function writeWavHeader(view, offset, sampleRate, numChannels, bitsPerSample, byteLength) {
    const writeString = (v, off, str) => {
        for (let i = 0; i < str.length; i++) {
            v.setUint8(off + i, str.charCodeAt(i));
        }
    };
    writeString(view, offset, 'RIFF'); offset += 4;
    view.setUint32(offset, 36 + byteLength, true); offset += 4;
    writeString(view, offset, 'WAVE'); offset += 4;
    writeString(view, offset, 'fmt '); offset += 4;
    view.setUint32(offset, 16, true); offset += 4;
    view.setUint16(offset, 1, true); offset += 2;
    view.setUint16(offset, numChannels, true); offset += 2;
    view.setUint32(offset, sampleRate, true); offset += 4;
    view.setUint32(offset, sampleRate * numChannels * (bitsPerSample / 8), true); offset += 4;
    view.setUint16(offset, numChannels * (bitsPerSample / 8), true); offset += 2;
    view.setUint16(offset, bitsPerSample, true); offset += 2;
    writeString(view, offset, 'data'); offset += 4;
    view.setUint32(offset, byteLength, true); offset += 4;
}

// 导出 16kHz mono 16bit signed PCM WAV 格式 Blob
function exportWAV(chunks, originalSampleRate, targetSampleRate = 16000) {
    let totalLength = chunks.reduce((acc, val) => acc + val.length, 0);
    let result = new Float32Array(totalLength);
    let offset = 0;
    for (let chunk of chunks) {
        result.set(chunk, offset);
        offset += chunk.length;
    }

    let pcmData = result;
    if (targetSampleRate !== originalSampleRate) {
        let compression = originalSampleRate / targetSampleRate;
        let newLength = Math.round(totalLength / compression);
        let downsampled = new Float32Array(newLength);
        for (let i = 0; i < newLength; i++) {
            downsampled[i] = result[Math.round(i * compression)];
        }
        pcmData = downsampled;
    }

    let buffer = new ArrayBuffer(44 + pcmData.length * 2);
    let view = new DataView(buffer);

    writeWavHeader(view, 0, targetSampleRate, 1, 16, pcmData.length * 2);

    let pcmOffset = 44;
    for (let i = 0; i < pcmData.length; i++, pcmOffset += 2) {
        let s = Math.max(-1, Math.min(1, pcmData[i]));
        view.setInt16(pcmOffset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    }

    return new Blob([view], { type: 'audio/wav' });
}

async function startRecording() {
    try {
        micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        wavAudioChunks = [];
        isListening = true;
        updateVoiceUI(true);
        addChat('user', '(正在听...)', true);

        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        audioContext = new AudioContextClass();

        const originalSampleRate = audioContext.sampleRate;
        mediaStreamSource = audioContext.createMediaStreamSource(micStream);

        scriptProcessor = audioContext.createScriptProcessor(4096, 1, 1);
        scriptProcessor.onaudioprocess = (e) => {
            if (!isListening) return;
            const inputBuffer = e.inputBuffer.getChannelData(0);
            wavAudioChunks.push(new Float32Array(inputBuffer));
        };

        mediaStreamSource.connect(scriptProcessor);
        scriptProcessor.connect(audioContext.destination);
    } catch (e) {
        console.error('麦克风错误:', e);
        if (e.name === 'NotAllowedError') {
            addChat('system', '浏览器禁止了麦克风权限');
        } else {
            addChat('system', '无法访问麦克风: ' + e.message);
        }
        updateVoiceUI(false);
    }
}

async function stopRecording() {
    isListening = false;
    updateVoiceUI(false);

    if (scriptProcessor) {
        scriptProcessor.disconnect();
        scriptProcessor = null;
    }
    if (mediaStreamSource) {
        mediaStreamSource.disconnect();
        mediaStreamSource = null;
    }

    if (audioContext) {
        const originalSampleRate = audioContext.sampleRate;
        audioContext.close();
        audioContext = null;

        if (micStream) {
            micStream.getTracks().forEach(t => t.stop());
            micStream = null;
        }

        if (wavAudioChunks.length === 0) {
            addChat('system', '未录到声音，请重试');
            return;
        }

        const wavBlob = exportWAV(wavAudioChunks, originalSampleRate, 16000);
        await sendToASR(wavBlob);
    }
}

async function sendToASR(blob) {
    setChatStatus('thinking', '识别中...');
    try {
        const resp = await fetch(API_ASR, {
            method: 'POST',
            headers: { 'Content-Type': 'audio/wav' },
            body: blob,
        });
        const data = await resp.json();
        if (data.error) {
            addChat('system', data.error);
            setChatStatus('idle', '点击说话');
            return;
        }
        const text = data.text;
        const bubbles = document.querySelectorAll('.chat-bubble.user.temp');
        bubbles.forEach(b => b.parentElement.remove());
        chatWithAI(text);
    } catch (e) {
        console.error(e);
        addChat('system', 'ASR 服务不可用，请确认 server.py 已启动');
        setChatStatus('idle', '点击说话');
    }
}

function toggleVoice() {
    // 打断正在进行的 AI 回复
    if (isChatting) {
        if (chatAbortCtrl) chatAbortCtrl.abort();
        stopAudio();
        isChatting = false;
        chatAbortCtrl = null;
        setChatStatus('idle', '点击说话');
        // 稍等一帧再启动录音，避免状态竞争
        setTimeout(() => {
            if (!isListening) startRecording();
        }, 100);
        return;
    }
    if (isListening) {
        stopRecording();
    } else {
        startRecording();
    }
}

function updateVoiceUI(listening) {
    const btn = document.getElementById('voiceBtn');
    const status = document.getElementById('voiceStatus');
    const visualizer = document.getElementById('visualizer');
    if (listening) {
        btn.classList.add('listening');
        status.textContent = '正在听...';
        status.style.color = 'var(--danger)';
        if (visualizer) visualizer.classList.add('active');
    } else {
        btn.classList.remove('listening');
        status.textContent = '点击说话';
        status.style.color = '';
        if (visualizer) visualizer.classList.remove('active');
    }
}

// ============================================================
//  文字输入
// ============================================================
function sendTextMessage() {
    const input = document.getElementById('textInput');
    const text = input.value.trim();
    if (!text) return;
    // 打断正在进行的 AI 回复
    if (isChatting) {
        if (chatAbortCtrl) chatAbortCtrl.abort();
        stopAudio();
        isChatting = false;
        chatAbortCtrl = null;
        setChatStatus('idle', '点击说话');
    }
    input.value = '';
    chatWithAI(text);
}

// ============================================================
//  语音播报 (MiMo TTS)
// ============================================================
let autoSpeak = true;
let currentAudio = null;  // 当前正在播放的音频
let ttsAbortCtrl = null;  // 用于取消 TTS 请求

async function speakText(text, forcePlay = false) {
    if (!autoSpeak && !forcePlay) return;
    stopAudio();  // 先停掉上一条
    ttsAbortCtrl = new AbortController();
    try {
        const resp = await fetch(API_TTS, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
            signal: ttsAbortCtrl.signal,
        });
        if (!resp.ok) throw new Error(`TTS HTTP ${resp.status}`);
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        currentAudio = new Audio(url);
        currentAudio.onended = () => {
            URL.revokeObjectURL(url);
            currentAudio = null;
        };
        currentAudio.play();
    } catch (e) {
        if (e.name === 'AbortError') return;  // 被打断，静默
        console.warn('MiMo TTS 失败，回退到浏览器 TTS:', e);
        // 回退：用浏览器内置 TTS
        if (window.speechSynthesis) {
            window.speechSynthesis.cancel();
            const u = new SpeechSynthesisUtterance(text);
            u.lang = 'zh-CN';
            window.speechSynthesis.speak(u);
        }
    }
}

function stopAudio() {
    if (ttsAbortCtrl) { ttsAbortCtrl.abort(); ttsAbortCtrl = null; }
    if (currentAudio) {
        currentAudio.pause();
        currentAudio.currentTime = 0;
        currentAudio = null;
    }
    if (window.speechSynthesis) window.speechSynthesis.cancel();
}

function toggleAutoSpeak() {
    autoSpeak = !autoSpeak;
    const btn = document.getElementById('autoSpeakBtn');
    btn.textContent = autoSpeak ? '🔊 自动播报: 开' : '🔇 自动播报: 关';
    btn.classList.toggle('active', autoSpeak);
}

// ============================================================
//  聊天 UI
// ============================================================
function addChat(role, text, isTemp) {
    const box = document.getElementById('chatBox');
    if (box.querySelector('.empty')) box.innerHTML = '';
    if (box.querySelector('.chat-welcome')) box.innerHTML = '';
    // 删除旧临时气泡
    if (!isTemp) {
        box.querySelectorAll('.chat-bubble.temp').forEach(b => b.parentElement.remove());
    }
    const div = document.createElement('div');
    div.className = `chat-msg ${role}`;
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${role}${isTemp ? ' temp' : ''}`;

    if (role === 'assistant' && !isTemp) {
        bubble.classList.add('playable');
        bubble.style.cursor = 'pointer';
        bubble.innerHTML = `${text} <span class="replay-speaker-icon" style="margin-left: 6px; font-size: 14px; opacity: 0.8;">🔊</span>`;
        bubble.onclick = () => {
            speakText(text, true);
        };
    } else {
        bubble.textContent = text;
    }

    const time = document.createElement('span');
    time.className = 'chat-time';
    time.textContent = new Date().toLocaleTimeString();
    div.appendChild(bubble);
    div.appendChild(time);
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
}

function setChatStatus(state, text) {
    const el = document.getElementById('voiceStatus');
    if (state === 'thinking') {
        el.textContent = text;
        el.style.color = 'var(--yellow)';
    } else {
        el.textContent = text;
        el.style.color = 'var(--muted)';
    }
}

// 彻底关闭后台服务进程
async function shutdownServer() {
    if (!confirm("确定要关闭后台 Python 服务器进程吗？\n关闭后，IoT 控制面板将无法再收发任何数据，AI 对话也将中断。")) {
        return;
    }
    try {
        const resp = await fetch(API_SHUTDOWN, { method: "POST" });
        if (resp.ok) {
            alert("后台服务器已成功关闭！\n您现在可以关闭此浏览器窗口。");
            // 尝试直接关闭页面
            window.opener = null;
            window.open("", "_self");
            window.close();
        } else {
            alert("关闭指令发送失败，响应码: " + resp.status);
        }
    } catch (e) {
        alert("网络连接已断开，服务可能已关闭或无法访问。");
    }
}

// ============================================================
//  未连接 MQTT 时阻止控制操作 & 提示
// ============================================================
(function() {
    let lastAlertTime = 0;
    const offlineAlert = (e) => {
        if (!client || !client.connected) {
            e.stopPropagation();
            e.preventDefault();
            const now = Date.now();
            if (now - lastAlertTime > 2000) {
                lastAlertTime = now;
                alert('请先开启并连接 MQTT 服务器！');
            }
        }
    };

    const grid = document.querySelector('.device-grid');
    if (grid) {
        grid.classList.add('offline-lock');
        grid.addEventListener('click', offlineAlert, true);
        grid.addEventListener('mousedown', offlineAlert, true);
        grid.addEventListener('touchstart', offlineAlert, true);
        grid.addEventListener('pointerdown', offlineAlert, true);
    }

    const pollBtn = document.querySelector('.btn-poll');
    if (pollBtn) {
        pollBtn.classList.add('offline-lock');
        pollBtn.addEventListener('click', offlineAlert, true);
        pollBtn.addEventListener('mousedown', offlineAlert, true);
        pollBtn.addEventListener('touchstart', offlineAlert, true);
        pollBtn.addEventListener('pointerdown', offlineAlert, true);
    }
})();
