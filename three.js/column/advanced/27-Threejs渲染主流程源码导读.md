# 第27章：Three.js 渲染主流程源码导读

## 元信息
- 学习目标：从源码视角理解 three.js 一帧渲染的关键路径，建立高级调优认知。
- 先修要求：完成中级篇并具备基础 WebGL 概念。
- 预估时长：6-8 小时。
- 最终产出：一份可用于排障和性能调优的渲染流程图与关键模块索引。
- 适用角色：资深开发、架构师。

## 1. 项目背景（约500字）

进入高级阶段后，单纯“会用 API”已经不够。面对复杂问题（某些对象不渲染、特效 pass 顺序异常、特定机型帧率断崖）时，团队需要回答更底层的问题：一帧里到底发生了什么？哪些步骤可控，哪些步骤是引擎内部策略？如果只停留在业务层，很容易在问题出现时陷入盲修。

在生产项目中，渲染流程认知不足会直接带来风险：改动一处材质参数影响整个排序结果，新增 pass 后前后景深关系异常，透明对象在某些视角“穿帮”。这些问题往往不是“某个 API 用错”，而是对渲染主流程的时序缺乏理解。

本章通过源码导读建立一条清晰主线：`renderer.render()` 如何驱动场景遍历、可见性裁剪、渲染列表构建、排序、状态绑定、draw call 提交。目标是把 three.js 从“黑箱”变成“灰箱”。

## 2. 项目设计（剧本式交锋对话，约1200字）

### 第一轮：API 用熟就够了吗

小胖：  
“我 API 都背熟了，为什么还要看源码？”

小白：  
“API 解决‘怎么用’，源码解决‘为什么这样表现’。高级排障必须知道底层路径。”

大师：  
“就像开车和修车的区别。日常驾驶只需会开，长途保养要懂发动机结构。”  

技术映射：  
“会用到会调” = `API literacy -> internal model`。

### 第二轮：源码阅读从哪下手

小胖：  
“源码太大，看两页就迷路。”

小白：  
“应从主入口开始，沿调用链追踪，而不是全仓库漫读。”

大师：  
“抓主链路：`render -> projectObject -> renderObjects -> renderBufferDirect`，先通主路再看支路。”  

技术映射：  
“主链路阅读” = `top-down call trace`。

### 第三轮：源码理解如何反哺业务

小胖：  
“看懂了又怎样，业务开发还不是写组件？”

小白：  
“源码认知可以指导排序、透明处理、状态切换和性能优化。”

大师：  
“高级能力不是多写代码，而是少走弯路。源码是避坑地图。”  

技术映射：  
“避坑地图” = `source-informed engineering decisions`。

## 3. 项目实战（约1500-2000字）

### 3.1 环境准备

```bash
npm install three
```

### 3.2 分步实现

#### 步骤1：建立渲染流程观察点

```ts
const originalRender = renderer.render.bind(renderer);
renderer.render = ((scene, camera) => {
  performance.mark("frame:start");
  originalRender(scene, camera);
  performance.mark("frame:end");
  performance.measure("frame", "frame:start", "frame:end");
}) as typeof renderer.render;
```

结果：可以观察每帧整体耗时。  
坑：频繁 `measure` 未清理，造成性能噪音。

#### 步骤2：追踪对象进入渲染列表

```ts
scene.traverse((obj) => {
  if ((obj as THREE.Mesh).isMesh) {
    // 结合 frustum 可见性和图层信息打点
    debugLog("render-candidate", { id: obj.uuid, visible: obj.visible, layer: obj.layers.mask });
  }
});
```

结果：理解“为什么这个对象被渲染/被剔除”。  
坑：忽略父节点 `visible=false` 的级联影响。

#### 步骤3：验证透明排序与渲染顺序

```ts
meshA.material.transparent = true;
meshB.material.transparent = true;
meshA.renderOrder = 2;
meshB.renderOrder = 1;
```

结果：可验证 `renderOrder`、深度测试与透明排序关系。  
坑：只调 `renderOrder` 忽略 `depthWrite` 导致伪影。

### 3.3 完整代码清单

- `src/source-study/frameProbe.ts`
- `src/source-study/renderCandidate.ts`
- `src/source-study/transparentOrderDemo.ts`

### 3.4 测试验证

```bash
npm run build
```

验证：
1. 每帧耗时可稳定采样；
2. 可解释对象为何被渲染或剔除；
3. 透明对象顺序可按预期控制。

## 4. 项目总结（约500-800字）

### 优点
1. 建立源码级排障能力。  
2. 对性能与画面问题定位更高效。  
3. 为后续 Shader/Pass 深潜打基础。

### 缺点
1. 学习曲线较陡。  
2. 需要持续跟踪版本变化。  
3. 过度深挖可能影响业务迭代节奏。

### 常见故障案例
1. 透明对象闪烁：排序和深度写入冲突。  
2. 对象偶发丢失：裁剪或图层配置错误。  
3. 多 pass 叠加异常：渲染顺序理解偏差。

### 思考题
1. 如何把关键渲染步骤抽象成团队内部知识图谱？  
2. 当 three.js 升级时，如何快速评估渲染主流程变更影响？

## 跨部门推广提示
- 开发：建立源码导读笔记与关键调用链文档。  
- 测试：针对排序、剔除、透明案例补专项回归。  
- 运维：配合性能打点定位渲染阶段瓶颈。  
