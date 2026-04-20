# 第28章：BufferGeometry/Material 核心机制深潜

## 元信息
- 学习目标：理解 `BufferGeometry` 和 `Material` 的数据结构、上传时机与性能影响。
- 先修要求：完成渲染主流程源码导读。
- 预估时长：6-8 小时。
- 最终产出：一套可用于几何与材质性能优化的诊断清单。
- 适用角色：资深开发、架构师。

## 1. 项目背景（约500字）

高级性能问题里，最常被忽视的是“数据布局问题”。很多项目在业务层做了大量优化，仍然卡顿，根因却是几何属性更新策略和材质状态切换策略不合理：每帧重建 `BufferAttribute`、频繁切换材质宏、过度细分 geometry。结果是 CPU/GPU 两端都被拖垮。

three.js 的 `BufferGeometry` 与 `Material` 是性能核心。一端决定数据如何存进显存，一端决定渲染管线如何编译和切换。如果不了解它们的内部机制，优化就会停留在表面，比如“减少对象数量”却忽略属性上传次数，或“共享材质”却忽略 uniform 动态变化成本。

本章目标是把几何和材质从“API 选项”上升为“数据结构工程”：你将理解 attribute 布局、index 与非 index 取舍、动态更新标记、材质 program 缓存命中条件，以及这些机制如何影响真实业务性能。

## 2. 项目设计（剧本式交锋对话，约1200字）

### 第一轮：几何体就是顶点数组？

小胖：  
“几何不就是一堆点吗，差别有这么大？”

小白：  
“顶点组织方式会影响上传频率和缓存命中，差别很大。”

大师：  
“同样是砖块，散装和托盘装运效率完全不同。geometry 布局就是‘托盘策略’。”  

技术映射：  
“托盘策略” = `attribute layout strategy`。

### 第二轮：材质共享一定更快？

小胖：  
“那就所有对象用同一材质，肯定快。”

小白：  
“共享能减少切换，但如果每帧改 uniform 过多也会有成本。”

大师：  
“优化看整体链路：program 切换、uniform 更新、纹理绑定都要算账。”  

技术映射：  
“整体算账” = `state change cost model`。

### 第三轮：何时需要自定义属性

小胖：  
“默认 position/normal 不够用，想加啥就加啥？”

小白：  
“属性越多带宽越大，移动端更敏感。”

大师：  
“属性是预算，不是愿望清单。每加一个字段都要有业务收益。”  

技术映射：  
“属性预算” = `vertex bandwidth budget`。

## 3. 项目实战（约1500-2000字）

### 3.1 环境准备

```bash
npm install three
```

### 3.2 分步实现

#### 步骤1：手写 BufferGeometry 并控制更新粒度

```ts
const geometry = new THREE.BufferGeometry();
const positions = new Float32Array(3000);
geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));

// 动态更新时只更新变化区间
const attr = geometry.getAttribute("position") as THREE.BufferAttribute;
attr.needsUpdate = true;
```

结果：理解 attribute 更新触发机制。  
坑：每帧 new 新数组导致频繁 GC 和上传。

#### 步骤2：比较 index / non-index 几何

```ts
geometry.setIndex(indices); // 复用顶点
```

结果：在重复顶点场景中可减少显存占用。  
坑：法线/UV 拆分场景下 index 复用收益下降。

#### 步骤3：材质 program 缓存实验

```ts
const matA = new THREE.MeshStandardMaterial({ color: "#22c55e", transparent: false });
const matB = new THREE.MeshStandardMaterial({ color: "#ef4444", transparent: false });
// 仅颜色变化通常可复用 program，宏开关变化会触发新 program
```

结果：识别哪些参数会触发 program 重编译。  
坑：频繁切换 `defines` 导致 shader 编译抖动。

### 3.3 完整代码清单

- `src/deep/geometryLab.ts`
- `src/deep/materialLab.ts`
- `src/deep/programProbe.ts`

### 3.4 测试验证

```bash
npm run build
```

验证：
1. 对比不同布局下内存与帧率；
2. 观察材质参数变更对 program 数量影响；
3. 动态更新时无异常内存增长。

## 4. 项目总结（约500-800字）

### 优点
1. 几何/材质优化更有抓手。  
2. 可解释 draw call 之外的深层性能问题。  
3. 为 Shader 与自定义 Pass 奠定基础。

### 缺点
1. 调试门槛较高。  
2. 需要更多底层知识储备。  
3. 易陷入过度优化。

### 常见故障案例
1. 帧率抖动：每帧重建 attribute。  
2. 首帧卡顿：大量 program 编译集中发生。  
3. 材质异常：共享材质被误修改导致全局串色。

### 思考题
1. 如何为业务团队提供“材质参数安全子集”？  
2. 如何在 CI 中检测 program 数量异常增长？

## 跨部门推广提示
- 开发：建立 geometry/material 性能规范文档。  
- 测试：新增首帧编译和内存基线检查。  
- 运维：关注 GPU 内存与 program 数量趋势。  
