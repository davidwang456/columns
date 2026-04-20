# 第34章：Web Worker 与 OffscreenCanvas 并行渲染实践

## 元信息
- 学习目标：掌握浏览器并行渲染架构，把主线程交互与渲染计算解耦。
- 先修要求：理解分帧调度和渲染性能瓶颈。
- 预估时长：6-8 小时。
- 最终产出：主线程轻量交互 + Worker 渲染的可运行工程骨架。
- 适用角色：资深开发、架构师。

## 1. 项目背景（约500字）

在复杂三维应用中，主线程承担了太多职责：UI 渲染、用户交互、状态计算、三维绘制。即使做了分帧优化，主线程仍可能在高峰场景被压满，导致按钮点击延迟、拖拽不跟手、输入卡顿。业务方会直观感知为“系统不灵”。这类问题本质是线程资源竞争。

Web Worker + OffscreenCanvas 提供了一个关键方向：把重渲染逻辑迁移到 Worker，让主线程专注交互与编排。这样不仅提升响应性，也为后续并行计算（路径规划、数据聚合）留出空间。但这条路线有门槛：消息协议、资源传递、同步时序、兼容性都要处理好。

本章目标是搭建可落地的并行渲染方案：主线程负责 UI 和输入事件，Worker 持有 three.js 渲染循环，双方通过事件总线通信，支持 resize、交互命令、状态更新同步。

## 2. 项目设计（剧本式交锋对话，约1200字）

### 第一轮：Worker 真有必要吗

小胖：  
“主线程优化一下就好了，迁 Worker 太折腾。”

小白：  
“当主线程被渲染长期占用时，交互体验会持续恶化。”

大师：  
“优化是必要的，但线程解耦是结构性收益。”  

技术映射：  
“结构性收益” = `main-thread offloading`。

### 第二轮：通信会不会太重

小胖：  
“主线程和 Worker 来回发消息，开销会更大吧？”

小白：  
“要定义高价值消息，避免高频冗余同步。”

大师：  
“消息设计要粗粒度、语义化，不能逐对象聊天。”  

技术映射：  
“语义消息” = `command/event protocol`。

### 第三轮：兼容性如何兜底

小胖：  
“有些环境不支持 OffscreenCanvas，怎么办？”

小白：  
“需要 capability 检测和 fallback 路径。”

大师：  
“并行是增强能力，不应成为可用性门槛。”  

技术映射：  
“增强不阻断” = `graceful fallback architecture`。

## 3. 项目实战（约1500-2000字）

### 3.1 环境准备

```bash
npm install three
```

### 3.2 分步实现

#### 步骤1：主线程转移 Canvas

```ts
const canvas = document.querySelector("canvas")!;
const offscreen = canvas.transferControlToOffscreen();
const worker = new Worker(new URL("./render.worker.ts", import.meta.url), { type: "module" });

worker.postMessage({ type: "init", canvas: offscreen }, [offscreen]);
```

运行结果：渲染上下文迁移到 Worker。  
坑：同一 canvas 不能重复 transfer。

#### 步骤2：Worker 内启动 three 渲染循环

```ts
self.onmessage = (e) => {
  if (e.data.type === "init") {
    const renderer = new THREE.WebGLRenderer({ canvas: e.data.canvas });
    // 初始化 scene/camera...
    const loop = () => {
      renderer.render(scene, camera);
      requestAnimationFrame(loop);
    };
    loop();
  }
};
```

运行结果：Worker 独立渲染。  
坑：resize 未同步会导致画面拉伸。

#### 步骤3：定义主线程与 Worker 协议

```ts
// 主线程
worker.postMessage({ type: "resize", width, height, dpr });
worker.postMessage({ type: "command", name: "focusDevice", payload: { id } });

// Worker
function handleCommand(name: string, payload: any) {
  if (name === "focusDevice") focusDevice(payload.id);
}
```

运行结果：交互与渲染解耦且可协同。  
坑：消息风暴（鼠标移动逐帧发）会抵消收益。

### 3.3 完整代码清单

- `src/main.ts`
- `src/render.worker.ts`
- `src/protocol/workerMessage.ts`
- `src/fallback/mainThreadRenderer.ts`

### 3.4 测试验证

```bash
npm run build
```

验证：
1. 主线程交互延迟显著下降；
2. resize、点选命令可跨线程生效；
3. 不支持 OffscreenCanvas 时可自动回退主线程渲染。

## 4. 项目总结（约500-800字）

### 优点
1. 主线程响应性显著提升。  
2. 渲染与 UI 职责边界清晰。  
3. 为并行计算扩展打基础。

### 缺点
1. 架构复杂度上升。  
2. 调试链路跨线程，排障更难。  
3. 浏览器支持差异需额外处理。

### 常见故障案例
1. 黑屏：Canvas 转移时机错误。  
2. 同步错位：resize 消息延迟或丢失。  
3. 消息拥堵：高频事件未节流。

### 思考题
1. 如何把实时数据处理也迁移到 Worker 并保持一致性？  
2. 如何设计多 Worker 协作（渲染、计算、IO）？

## 跨部门推广提示
- 开发：制定跨线程通信协议规范。  
- 测试：增加并行模式与降级模式双路径回归。  
- 运维：对比并行/非并行模式性能指标。  
