# 第32章：大规模地图与 GIS 融合——Tiles 与地理坐标转换

## 元信息
- 学习目标：掌握地理坐标系转换、地图瓦片加载与 three.js 场景融合策略。
- 先修要求：完成流式加载与大场景优化章节。
- 预估时长：6-8 小时。
- 最终产出：支持经纬度数据落图、瓦片流式加载和业务对象精准定位的 GIS 融合模块。
- 适用角色：架构师、资深开发。

## 1. 项目背景（约500字）

高级阶段常见需求是把三维业务系统接入真实地理底图：道路、行政区、楼宇、传感器点位都基于经纬度。很多团队初次融合 GIS 时会遇到“看起来差不多、实际上全偏移”的问题：坐标系不统一、投影误差累积、不同数据源尺度不一致，导致设备点位偏几十米甚至几百米。

此外，大规模地图意味着海量数据流。若无瓦片与分级加载策略，首屏会非常慢；若转换链路不规范，定位误差会在业务联动中放大（告警点跳到错误位置）。这不仅是技术误差，也是业务风险：调度人员可能基于错误地理位置做决策。

本章目标是建立“可验证的 GIS 融合链路”：明确坐标系（WGS84/WebMercator/本地坐标）转换规范，按 Tiles 流式加载地图，统一场景原点与缩放策略，保证业务对象定位准确且性能可控。

## 2. 项目设计（剧本式交锋对话，约1200字）

### 第一轮：经纬度直接当坐标用？

小胖：  
“经纬度就是两个数字，直接塞进 x/z 不就行？”

小白：  
“经纬度不是线性坐标，直接用会有严重形变与误差。”

大师：  
“地理坐标先投影再渲染，这是铁律。”  

技术映射：  
“先投影” = `geodetic -> projected coordinate transform`。

### 第二轮：多数据源如何对齐

小胖：  
“A 系统的点和 B 系统的路网总对不上，是谁错了？”

小白：  
“可能是坐标基准不同，也可能是原点设定不同。”

大师：  
“统一基准、统一原点、统一缩放，三步缺一不可。”  

技术映射：  
“三步统一” = `datum + origin + scale normalization`。

### 第三轮：性能怎么兜底

小胖：  
“地图全量加载最省事，反正都要看。”

小白：  
“全量会拖垮首屏，应按视野和缩放级别加载 Tiles。”

大师：  
“GIS 渲染本质是流式系统，不是静态模型展示。”  

技术映射：  
“流式系统” = `tile streaming + LOD`。

## 3. 项目实战（约1500-2000字）

### 3.1 环境准备

```bash
npm install three
```

### 3.2 分步实现

#### 步骤1：坐标转换工具

```ts
const EARTH_RADIUS = 6378137;

function lonLatToMercator(lon: number, lat: number) {
  const x = (lon * Math.PI * EARTH_RADIUS) / 180;
  const y = Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)) * EARTH_RADIUS;
  return { x, y };
}
```

结果：建立地理到平面坐标转换基础。  
坑：纬度接近极值时需做边界裁剪。

#### 步骤2：统一场景原点与缩放

```ts
const origin = lonLatToMercator(121.4737, 31.2304);

function toScenePos(lon: number, lat: number) {
  const p = lonLatToMercator(lon, lat);
  const scale = 0.001; // 米到场景单位
  return new THREE.Vector3((p.x - origin.x) * scale, 0, (p.y - origin.y) * scale);
}
```

结果：多源点位可对齐到同一局部坐标。  
坑：未固定 origin 导致跨会话漂移。

#### 步骤3：瓦片分级加载

```ts
type TileKey = `${number}/${number}/${number}`; // z/x/y

function shouldLoadTile(tileBounds: THREE.Box3, cameraPos: THREE.Vector3) {
  return tileBounds.distanceToPoint(cameraPos) < 1500;
}
```

结果：按视野动态加载地图资源。  
坑：切片边缘缝隙需做邻接处理。

### 3.3 完整代码清单

- `src/gis/coord.ts`
- `src/gis/origin.ts`
- `src/gis/tileStream.ts`
- `src/gis/layerManager.ts`

### 3.4 测试验证

```bash
npm run build
```

验证：
1. 已知点位落图误差在阈值内；
2. 瓦片按缩放级别与视野加载；
3. 漫游过程中无明显拼缝和抖动。

## 4. 项目总结（约500-800字）

### 优点
1. 实现真实地理语义融合。  
2. 支持大规模地图流式渲染。  
3. 为跨系统联动提供统一坐标基线。

### 缺点
1. 坐标体系学习成本高。  
2. 数据源异构时治理复杂。  
3. 瓦片服务依赖网络与服务稳定性。

### 常见故障案例
1. 点位偏移：投影或基准混用。  
2. 地图闪烁：瓦片加载与卸载阈值不合理。  
3. 边界错位：切片坐标索引计算错误。

### 思考题
1. 如何支持 3D Tiles 与业务对象统一拾取？  
2. 如何在离线模式下提供 GIS 基础能力？

## 跨部门推广提示
- 开发：统一坐标转换 SDK，禁止散落实现。  
- 测试：维护标准地理点位校准集。  
- 运维：监控瓦片服务延迟、错误率和缓存命中。  
