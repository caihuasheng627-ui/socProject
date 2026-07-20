# WebSocket 实时通信协议

> **基础路径**: `wss://api.csvest.local/ws`
> **协议版本**: 1.1
> **认证**: JWT Token (query 参数 `?token=xxx`)

## 目录

- [1. 概述](#1-概述)
- [2. 客户端连接](#2-客户端连接)
- [3. 消息格式](#3-消息格式)
- [4. 通道列表](#4-通道列表)
- [5. 事件类型](#5-事件类型)
- [6. 心跳机制](#6-心跳机制)
- [7. 错误处理](#7-错误处理)

---

## 1. 概述

CSVest 使用 WebSocket 实现以下实时场景:

| 场景 | 说明 | 频率 |
|------|------|------|
| 价格预警触发推送 | 达到条件立即通知 | 事件触发 |
| AI 对话流式输出 | 边生成边返回 | 实时 |
| K 线图实时增量 | 60 秒更新一次 | 1/分钟 |
| 行情看板实时刷新 | 拉取最新成交价 | 5/分钟 |
| Agent 辩论进度 | 多轮对话状态 | 实时 |

---

## 2. 客户端连接

### 2.1 连接示例

```javascript
const ws = new WebSocket('wss://api.csvest.local/ws?token=' + jwtToken);

ws.onopen = () => {
  console.log('WebSocket connected');
  // 订阅通道
  ws.send(JSON.stringify({
    action: 'subscribe',
    channels: ['price.alerts', 'market.tickers']
  }));
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  console.log('Received:', msg);
};

ws.onerror = (err) => {
  console.error('WebSocket error:', err);
};

ws.onclose = (event) => {
  console.log('WebSocket closed:', event.code, event.reason);
  // 断线重连
  setTimeout(reconnect, 3000);
};
```

### 2.2 鉴权

通过 query 参数 `token` 传递 JWT。Token 由 `/api/auth/login` 获取。

如果 token 过期或无效,服务器会发送 `type: 'auth_error'` 消息并立即关闭连接(4401)。

### 2.3 断线重连

客户端应当实现指数退避重连:

```javascript
let retryDelay = 1000;
function reconnect() {
  const ws = new WebSocket(WS_URL);
  ws.onopen = () => { retryDelay = 1000; /* 重新订阅 */ };
  ws.onclose = () => {
    setTimeout(reconnect, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 30000); // 最多 30 秒
  };
}
```

---

## 3. 消息格式

所有消息均为 JSON 格式。

### 3.1 客户端 → 服务端

```typescript
interface ClientMessage {
  action: 'subscribe' | 'unsubscribe' | 'ping' | 'chat.send' | 'debate.start';
  channels?: string[];      // subscribe/unsubscribe 时必填
  requestId?: string;       // 用于请求-响应匹配(可选)
  payload?: any;            // action 特定参数
}
```

### 3.2 服务端 → 客户端

```typescript
interface ServerMessage {
  type: 'event' | 'ack' | 'error' | 'ping' | 'pong' | 'auth_error' | 'data';
  channel?: string;         // 事件所属通道
  event?: string;           // 事件类型
  data?: any;               // 事件数据
  requestId?: string;       // 对应客户端请求
  timestamp: number;        // 毫秒时间戳
  message?: string;         // 错误描述
}
```

---

## 4. 通道列表

| 通道 | 说明 | 鉴权要求 |
|------|------|---------|
| `price.alerts` | 用户价格预警触发推送 | 登录 |
| `market.tickers` | 全部饰品最新价(5/分钟) | - |
| `market.kline.{skinId}` | 单一饰品 K线增量(60s) | - |
| `chat.session.{sessionId}` | AI 对话流式输出 | 登录 |
| `debate.{skinId}` | 双 Agent 辩论进度 | - |
| `daily.report` | 市场日报更新(每日 09:00) | - |
| `system.broadcast` | 系统级广播(维护通知等) | - |

### 4.1 订阅/取消订阅

```json
// 客户端
{
  "action": "subscribe",
  "channels": ["price.alerts", "market.tickers"]
}

// 服务端 ack
{
  "type": "ack",
  "requestId": "req-123",
  "data": { "subscribed": ["price.alerts", "market.tickers"] }
}
```

---

## 5. 事件类型

### 5.1 价格预警触发 (`price.alerts`)

```json
{
  "type": "event",
  "channel": "price.alerts",
  "event": "alert.triggered",
  "data": {
    "alertId": 1,
    "skinId": "ak47-fireserpent-fn",
    "skinName": "AK-47 | Fire Serpent (FN)",
    "type": "above",
    "targetPrice": 16500,
    "currentPrice": 16580,
    "triggeredAt": "2026-07-15T10:23:45Z"
  },
  "timestamp": 1752569025000
}
```

### 5.2 行情实时价 (`market.tickers`)

```json
{
  "type": "data",
  "channel": "market.tickers",
  "event": "price.update",
  "data": {
    "tickers": [
      {
        "skinId": "ak47-fireserpent-fn",
        "price": 15850,
        "change24h": 0.85,
        "volume24h": 55,
        "source": "BUFF"
      },
      {
        "skinId": "awp-dragonlore-ft",
        "price": 42788,
        "change24h": 1.23,
        "volume24h": 16,
        "source": "BUFF"
      }
    ]
  },
  "timestamp": 1752569100000
}
```

### 5.3 AI 对话流式输出 (`chat.session.{sessionId}`)

```json
// 客户端发送
{
  "action": "chat.send",
  "payload": {
    "message": "AK-47 火蛇现在能买吗?",
    "sessionId": "sess-abc123"
  }
}

// 服务端流式返回(每个 chunk 一条消息)
{
  "type": "event",
  "channel": "chat.session.sess-abc123",
  "event": "chat.chunk",
  "data": { "chunk": "根据" }
}
{
  "type": "event",
  "channel": "chat.session.sess-abc123",
  "event": "chat.chunk",
  "data": { "chunk": " 6 个回归模型" }
}
{
  "type": "event",
  "channel": "chat.session.sess-abc123",
  "event": "chat.chunk",
  "data": { "chunk": " 综合..." }
}

// 结束
{
  "type": "event",
  "channel": "chat.session.sess-abc123",
  "event": "chat.done",
  "data": {
    "model": "deepseek-chat",
    "usage": { "promptTokens": 234, "completionTokens": 567, "totalTokens": 801 },
    "duration": 2340
  }
}
```

### 5.4 双 Agent 辩论进度 (`debate.{skinId}`)

```json
// Round 1
{
  "type": "event",
  "channel": "debate.ak47-fireserpent-fn",
  "event": "debate.round.start",
  "data": { "round": 1, "type": "bull_bear" }
}

{
  "type": "event",
  "channel": "debate.ak47-fireserpent-fn",
  "event": "debate.agent.message",
  "data": {
    "round": 1,
    "agent": "bull",
    "message": "🐂 多头 Agent:技术面显示 MA_30..."
  }
}

{
  "type": "event",
  "channel": "debate.ak47-fireserpent-fn",
  "event": "debate.round.end",
  "data": { "round": 1 }
}

// Round 2、3 类似...

// 最终共识
{
  "type": "event",
  "channel": "debate.ak47-fireserpent-fn",
  "event": "debate.consensus",
  "data": {
    "recommendation": "谨慎看多",
    "entryRange": "¥15,400 - 15,600",
    "stopLoss": "¥14,800",
    "targetPrice": "¥16,800 (30天)",
    "consensusScore": 76,
    "risks": ["短期回调风险", "CS2 后市场结构变化"]
  }
}
```

### 5.5 市场日报 (`daily.report`)

每日 09:00 自动推送:

```json
{
  "type": "event",
  "channel": "daily.report",
  "event": "daily.generated",
  "data": {
    "date": "2026-07-15",
    "summary": "今日 CS2 饰品市场整体偏强震荡...",
    "metrics": { "monitored": 20, "gainers": 14, "losers": 6 },
    "hotVolume": [...]
  }
}
```

### 5.6 系统广播 (`system.broadcast`)

```json
{
  "type": "event",
  "channel": "system.broadcast",
  "event": "system.maintenance",
  "data": {
    "level": "info",
    "title": "系统维护通知",
    "message": "数据源 BUFF API 临时不可用,已切换到 Skinport 备用",
    "until": "2026-07-15T12:00:00Z"
  }
}
```

---

## 6. 心跳机制

### 6.1 Ping/Pong

客户端应当每 30 秒发送一次 ping:

```json
// 客户端
{ "action": "ping" }

// 服务端
{ "type": "pong", "timestamp": 1752569100000 }
```

### 6.2 超时处理

如果服务器 60 秒内没收到客户端任何消息,会主动关闭连接(4408)。

---

## 7. 错误处理

### 7.1 错误消息格式

```json
{
  "type": "error",
  "code": "INVALID_CHANNEL",
  "message": "Channel 'foo.bar' is not a valid channel",
  "requestId": "req-123"
}
```

### 7.2 错误码

| code | 描述 |
|------|------|
| `INVALID_PAYLOAD` | 消息格式错误 |
| `INVALID_CHANNEL` | 通道名不存在 |
| `UNAUTHORIZED` | 需要登录 |
| `RATE_LIMIT` | 超过频率限制 |
| `INTERNAL_ERROR` | 服务器内部错误 |
| `MODEL_UNAVAILABLE` | ML 模型暂时不可用 |
| `LLM_QUOTA_EXCEEDED` | DeepSeek 配额用尽 |

### 7.3 关闭码

| code | 含义 |
|------|------|
| 1000 | 正常关闭 |
| 4400 | 协议错误(消息格式错) |
| 4401 | 鉴权失败 |
| 4403 | 权限不足 |
| 4408 | 心跳超时 |
| 4429 | 频率限制 |
| 1011 | 服务器内部错误 |

---

## 8. 集成示例

### 8.1 前端订阅价格预警

```javascript
// js/api-ws.js
class CSVestWS {
  constructor(baseURL, token) {
    this.baseURL = baseURL;
    this.token = token;
    this.ws = null;
    this.listeners = new Map();
  }

  connect() {
    this.ws = new WebSocket(`${this.baseURL}/ws?token=${this.token}`);

    this.ws.onopen = () => {
      this.send({ action: 'subscribe', channels: ['price.alerts'] });
    };

    this.ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      this.notify(msg.channel, msg);
    };

    this.ws.onclose = () => {
      setTimeout(() => this.connect(), 3000);
    };
  }

  on(channel, callback) {
    if (!this.listeners.has(channel)) this.listeners.set(channel, []);
    this.listeners.get(channel).push(callback);
  }

  notify(channel, msg) {
    const cbs = this.listeners.get(channel) || [];
    cbs.forEach(cb => cb(msg));
  }

  send(payload) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }
}

// 使用
const ws = new CSVestWS('wss://api.csvest.local', token);
ws.connect();
ws.on('price.alerts', (msg) => {
  if (msg.event === 'alert.triggered') {
    showToast({
      title: '价格预警触发',
      subtitle: `${msg.data.skinName} ¥${msg.data.currentPrice}`,
      type: 'success'
    });
  }
});
```

### 8.2 后端实现 (FastAPI 伪代码)

```python
from fastapi import FastAPI, WebSocket

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str):
    # 1. 鉴权
    user = verify_jwt(token)
    if not user:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    subscriptions = set()

    try:
        while True:
            data = await websocket.receive_json()

            if data["action"] == "subscribe":
                for ch in data["channels"]:
                    subscriptions.add(ch)
                    # 注册到对应管理器
                    await channel_manager.subscribe(ch, websocket)
                await websocket.send_json({
                    "type": "ack",
                    "data": {"subscribed": list(subscriptions)}
                })

            elif data["action"] == "ping":
                await websocket.send_json({"type": "pong", "timestamp": now_ms()})

    except WebSocketDisconnect:
        await channel_manager.unsubscribe_all(websocket)
```

### 8.3 价格预警推送(订阅者)

```python
# 后端 cron 每 30 秒检查
async def check_alerts():
    triggered = alert_service.check_all()
    for alert in triggered:
        await channel_manager.broadcast(
            channel=f"price.alerts",
            event="alert.triggered",
            data={
                "alertId": alert.id,
                "skinId": alert.skin_id,
                ...
            }
        )
```

---

## 9. 性能指标

| 指标 | 目标 |
|------|------|
| WebSocket 消息延迟 | < 100ms |
| 心跳间隔 | 30s |
| 重连退避 | 1s → 30s 指数 |
| 单连接最大订阅数 | 20 |
| 并发连接数(单实例) | 10,000 |
| 消息频率限制 | 100 msg/分钟/连接 |

---

## 10. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.1 | 2026-07-15 | 初始版本(对应 API v1.1) |
