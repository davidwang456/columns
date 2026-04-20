# 第21章：实时数据驱动WebSocket推流与场景状态同步

## 元信息
- 学习目标：构建可靠的实时数据链路，解决场景状态延迟、乱序、丢包下的一致性问题。
- 先修要求：完成中级篇 14-20 章架构与性能基础。
- 预估时长：5-7 小时。
- 最终产出：支持断线重连、增量更新、状态回放的实时同步模块。
- 适用角色：开发、测试、运维。

## 1. 项目背景（约500字）

在城市运行看板中，“实时”不是锦上添花，而是核心价值：告警、设备状态、车流热力必须秒级变化。当前系统如果仍靠轮询接口（每 10 秒拉一次），就会出现两类业务问题：一是告警滞后，调度决策晚半拍；二是数据抖动，用户看到状态“跳来跳去”，失去信任。

真实生产里，实时同步比“连上 WebSocket”复杂得多。网络波动会造成断连与重连，消息可能乱序到达，前端切页会丢失上下文，后端高峰时会批量推送。没有同步策略时，常见现象是“设备明明恢复了，场景还在报警红色”。

本章目标是建立一套工程化实时链路：消息协议（seq/timestamp/type）、接收队列、去重与乱序处理、断线重连、增量补偿。最终让场景状态“快且准”，并可被测试验证、被运维观测。

## 2. 项目设计（剧本式交锋对话，约1200字）

### 第一轮：轮询不行吗

小胖：  
“10 秒轮询也能更新，为什么要搞 WebSocket 这么复杂？”

小白：  
“轮询有时延上限，还会产生无效请求。高频状态变化下体验很差。”

大师：  
“实时场景要推模型而不是拉模型。像快递消息，不该你每分钟打电话问一次。”  

技术映射：  
“推模型” = `server push with websocket`。

### 第二轮：消息乱序怎么办

小胖：  
“我收到了就更新，先后顺序无所谓吧？”

小白：  
“乱序会把新状态被旧状态覆盖，必须有 seq 或版本号。”

大师：  
“状态更新必须可比较。无版本消息就像没有时间戳的账单，没法对账。”  

技术映射：  
“可比较消息” = `sequence-based ordering`。

### 第三轮：断线重连会不会丢数据

小胖：  
“掉线就重连，应该自动恢复吧？”

小白：  
“重连期间消息可能丢失，需补偿拉取。”

大师：  
“重连只是通道恢复，不是状态恢复。必须加增量补偿机制。”  

技术映射：  
“状态恢复” = `reconnect + delta catch-up`。

## 3. 项目实战（约1500-2000字）

### 3.1 环境准备

```bash
npm install three
npm install ws
```

### 3.2 分步实现

#### 步骤1：定义消息协议与状态容器

```ts
type RealtimeMsg = {
  seq: number;
  ts: number;
  type: "device:update" | "alarm:new";
  payload: any;
};

let lastSeq = 0;
const pending: RealtimeMsg[] = [];
```

运行结果：前端具备排序和去重基础。  
坑：不加 `seq` 只能“最后写覆盖”，无法保证一致性。

#### 步骤2：接入 WebSocket 与乱序处理

```ts
function onMessage(raw: string) {
  const msg = JSON.parse(raw) as RealtimeMsg;
  if (msg.seq <= lastSeq) return; // 去重
  pending.push(msg);
  pending.sort((a, b) => a.seq - b.seq);
  flushQueue();
}

function flushQueue() {
  while (pending.length && pending[0].seq === lastSeq + 1) {
    const msg = pending.shift()!;
    applyMsgToStore(msg);
    lastSeq = msg.seq;
  }
}
```

运行结果：消息按序落地，避免状态回滚。  
坑：队列积压无上限会导致内存增长。

#### 步骤3：断线重连与增量补偿

```ts
async function reconnectWithBackoff() {
  // 伪代码：指数退避重连
  // 成功后请求 /delta?fromSeq=lastSeq
}

async function catchup(fromSeq: number) {
  const missed = await fetch(`/api/realtime/delta?from=${fromSeq}`).then((r) => r.json());
  missed.forEach((msg: RealtimeMsg) => onMessage(JSON.stringify(msg)));
}
```

运行结果：短时断线后状态可恢复。  
坑：重连成功但不补偿，期间状态永久丢失。

### 3.3 完整代码清单

- `src/realtime/protocol.ts`
- `src/realtime/socketClient.ts`
- `src/realtime/recovery.ts`
- `src/store/realtimeReducer.ts`

### 3.4 测试验证

```bash
npm run build
```

验证清单：
1. 模拟乱序消息，最终状态仍正确；  
2. 模拟断网 15 秒后重连，状态自动补偿；  
3. 压测下消息处理无明显堆积。

## 4. 项目总结（约500-800字）

### 优点
1. 实时性显著提升，业务响应更快。  
2. 一致性可控，避免“看板假实时”。  
3. 具备重连与恢复能力，稳定性增强。

### 缺点
1. 协议与状态机复杂度上升。  
2. 后端也需配合实现增量补偿。  
3. 高并发场景需要限流与背压机制。

### 常见故障案例
1. 状态倒退：未做 seq 校验。  
2. 重连风暴：无退避机制。  
3. 消息积压：主线程处理过慢未降载。

### 思考题
1. 如何设计“关键告警优先级通道”？  
2. 当 WebSocket 不可用时如何平滑降级到 SSE/轮询？

## 跨部门推广提示
- 开发：前后端统一协议版本和幂等策略。  
- 测试：注入乱序、丢包、重连场景自动化用例。  
- 运维：监控连接数、重连率、消息积压长度。  
