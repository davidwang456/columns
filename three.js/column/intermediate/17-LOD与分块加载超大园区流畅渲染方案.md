# 第17章：LOD与分块加载超大园区流畅渲染方案

## 元信息
- 学习目标：掌握 LOD 与分块加载，解决超大园区首屏慢与远景浪费渲染问题。
- 先修要求：完成第 16 章海量对象优化。
- 预估时长：5-7 小时。
- 最终产出：按距离动态降级、按区域按需加载的园区渲染方案。
- 适用角色：开发、测试、运维。

## 1. 项目背景（约500字）

项目从“单厂区”升级为“多园区联动”后，场景规模增长到原来的十倍。最先暴露的问题是首屏加载时间和镜头移动卡顿：用户只看眼前一个区域，却要为全园区资源买单。传统一次性全加载策略在中级规模下已经不可持续。

业务侧需求很明确：总览时看全局轮廓，拉近时看细节，切换区域时快速响应。技术上要同时解决两个问题：一是远处对象不必高精渲染（LOD）；二是当前看不到的区域不应提前加载（分块加载）。这两者结合，才能真正把成本与可见范围绑定。

本章目标是构建“可见即加载，远景即降级”的渲染策略：按地理网格切块管理资源，按相机距离切换模型细节，结合缓存和卸载策略保证稳定性。

## 2. 项目设计（剧本式交锋对话，约1200字）

### 第一轮：全量加载是不是更稳

小胖：  
“全都加载完最省事，切哪都不卡。”

小白：  
“代价是首屏慢和内存高，很多资源用户根本看不到。”

大师：  
“要按可见性付费，不要按全量付费。”  

技术映射：  
“按可见性付费” = `visibility-driven loading`。

### 第二轮：LOD 会不会影响精度

小胖：  
“远处模型变简，会不会看起来糊？”

小白：  
“远景本来就不需要高细节，只要轮廓和语义正确。”

大师：  
“LOD 的目标不是‘一样清晰’，而是‘感知一致、成本更低’。”  

技术映射：  
“感知一致” = `perceptual quality optimization`。

### 第三轮：块怎么切才合理

小胖：  
“按业务区域切还是按坐标网格切？”

小白：  
“业务切好理解，网格切好计算；可能要二者结合。”

大师：  
“存储按网格，展示按业务。底层高效，上层可解释。”  

技术映射：  
“双维切块” = `grid partition + business grouping`。

## 3. 项目实战（约1500-2000字）

### 3.1 环境准备

```bash
npm install three
```

### 3.2 分步实现

#### 步骤1：构建分块索引

```ts
type ChunkId = string;
type ChunkMeta = { id: ChunkId; bounds: THREE.Box3; loaded: boolean };
const chunkTable = new Map<ChunkId, ChunkMeta>();

function calcChunkId(x: number, z: number) {
  return `${Math.floor(x / 100)}_${Math.floor(z / 100)}`;
}
```

结果：场景对象可按块归档。  
坑：边界对象跨块导致重复加载。

#### 步骤2：按相机位置触发加载/卸载

```ts
function updateChunkVisibility(cameraPos: THREE.Vector3) {
  chunkTable.forEach((chunk) => {
    const dist = chunk.bounds.distanceToPoint(cameraPos);
    if (dist < 220 && !chunk.loaded) loadChunk(chunk.id);
    if (dist > 320 && chunk.loaded) unloadChunk(chunk.id);
  });
}
```

结果：资源随视角流动，内存可控。  
坑：阈值无滞后区会造成频繁抖动加载。

#### 步骤3：接入 LOD

```ts
const lod = new THREE.LOD();
lod.addLevel(highDetailMesh, 0);
lod.addLevel(midDetailMesh, 80);
lod.addLevel(lowDetailMesh, 180);
scene.add(lod);
```

结果：远景成本下降，观感基本一致。  
坑：LOD 切换距离设置不当会产生“跳变感”。

### 3.3 完整代码清单

- `src/streaming/chunkIndex.ts`
- `src/streaming/chunkLoader.ts`
- `src/lod/createLod.ts`

### 3.4 测试验证

```bash
npm run build
```

验证：
1. 首屏只加载邻近分块；
2. 漫游过程中分块动态加载/卸载稳定；
3. LOD 切换时无明显闪烁。

## 4. 项目总结（约500-800字）

### 优点
1. 显著缩短首屏时间。  
2. 内存占用随视角动态控制。  
3. 支持超大场景扩展。

### 缺点
1. 流式加载逻辑复杂。  
2. LOD 资产制作成本增加。  
3. 切换策略需持续调参。

### 常见故障案例
1. 频繁加载抖动：缺少滞后阈值。  
2. 视觉跳变：LOD 距离配置不合理。  
3. 内存不降：卸载未 `dispose`。

### 思考题
1. 如何结合用户路线预测提前预载分块？  
2. 如何把分块与权限控制联动？

## 跨部门推广提示
- 开发：定义统一切块规格与 LOD 规则。  
- 测试：做长路径漫游稳定性回归。  
- 运维：监控加载失败率与缓存命中率。  
