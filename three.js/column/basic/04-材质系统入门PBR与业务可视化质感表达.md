# 第04章：材质系统入门PBR与业务可视化质感表达

## 元信息
- 学习目标：掌握 `MeshBasicMaterial`、`MeshStandardMaterial` 与 PBR 参数，建立“业务语义 -> 材质表达”映射。
- 先修要求：完成第 01-03 章，理解场景、光照、网格基础。
- 预估时长：4-6 小时。
- 最终产出：设备状态（正常/告警/离线）可通过材质统一表达的示例工程。
- 适用角色：开发、测试。

## 1. 项目背景（约500字）

某工厂数字看板在内测时出现一个典型问题：业务状态是对的，但现场人员看不懂。比如告警设备只是“偏红一点”，正常设备只是“偏绿一点”，在会议室投影和控制室大屏上观感差异很大，导致值班员经常误判。产品团队要求：颜色不只好看，还要“稳、准、可解释”，并且能在不同显示设备上保持相近观感。

这背后是材质系统没有设计。很多项目初期直接用 `MeshBasicMaterial` 填色，虽然上手快，但它不参与光照，场景真实感差；当项目引入光照后，若材质参数还是拍脑袋配置，结果会出现“有的物体塑料感太重、有的像金属反光过强、有的在暗区完全看不见”。业务层面看，这不是美术问题，而是信息传达失败。

本章将建立一套可落地的材质表达规范：先按业务对象分类（地面、建筑、设备、告警层），再按物理属性设置 PBR 参数（`metalness`、`roughness`、`emissive`），最后通过统一状态函数做动态更新。目标是把“质感”变成工程能力，而不是手工调色。

## 2. 项目设计（剧本式交锋对话，约1200字）

### 第一轮：颜色够不够

小胖：  
“设备不就是改个颜色嘛，红黄绿三种一上不就齐活了？”

小白：  
“只改颜色在强光和暗光下差异很大。还要考虑投影、LED 屏、普通显示器。是不是应该把亮度和自发光也纳入规则？”

大师：  
“对。颜色只是编码的一部分。告警除了红色，还要有轻微 `emissive`，保证远距离可识别；离线状态不只是灰色，还要降低高光，体现‘失活’感。”  

技术映射：  
“颜色 + 发光 + 粗糙度” = `视觉语义三元组`。

### 第二轮：为什么要上 PBR

小胖：  
“PBR 听起来很高端，会不会太复杂？”

小白：  
“如果后面要接 glTF 模型，不用 PBR 会不会出现模型和自建几何体风格断层？”

大师：  
“这就是关键。PBR 不是为了炫技，是为了统一渲染语言。你可以把它理解为全项目的‘材质普通话’，避免不同模块各说方言。”  

技术映射：  
“材质普通话” = `PBR-based material standard`。

### 第三轮：如何可维护

小胖：  
“如果每个设备都手动调参数，后面改需求得疯掉。”

小白：  
“那就要做材质工厂和状态映射表，把配置集中管理？”

大师：  
“没错。所有材质由工厂函数创建，状态变更只改映射，不直接散改 Mesh。这样测试也能对照验收。”  

技术映射：  
“集中配置” = `materialFactory + statusPalette`。

## 3. 项目实战（约1500-2000字）

### 3.1 环境准备

```bash
npm install three
```

### 3.2 分步实现

#### 步骤1：定义材质工厂

```ts
import * as THREE from "three";

type DeviceState = "normal" | "warning" | "offline";

export function createBaseMaterial() {
  return new THREE.MeshStandardMaterial({
    color: "#94a3b8",
    metalness: 0.2,
    roughness: 0.7
  });
}

export function createGroundMaterial() {
  return new THREE.MeshStandardMaterial({
    color: "#1e293b",
    metalness: 0.05,
    roughness: 0.95
  });
}
```

运行结果：对象具备基础真实感。  
常见坑：所有对象都用同一参数，导致“同材质化”。

#### 步骤2：建立状态到材质参数映射

```ts
const stateStyle: Record<DeviceState, { color: string; emissive: string; roughness: number }> = {
  normal: { color: "#22c55e", emissive: "#000000", roughness: 0.65 },
  warning: { color: "#ef4444", emissive: "#330000", roughness: 0.45 },
  offline: { color: "#64748b", emissive: "#000000", roughness: 0.9 }
};

export function applyDeviceState(mat: THREE.MeshStandardMaterial, state: DeviceState) {
  const style = stateStyle[state];
  mat.color.set(style.color);
  mat.emissive.set(style.emissive);
  mat.roughness = style.roughness;
}
```

运行结果：不同状态在远距离也有明显区分。  
常见坑：只改颜色不 `needsUpdate`（部分复杂材质需要更新标记）。

#### 步骤3：接入场景并动态切换

```ts
const devices: THREE.Mesh[] = [];

for (let i = 0; i < 5; i++) {
  const mat = createBaseMaterial();
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 2, 1), mat);
  mesh.position.set(-6 + i * 3, 1, 0);
  devices.push(mesh);
  scene.add(mesh);
}

setInterval(() => {
  const states: DeviceState[] = ["normal", "warning", "offline"];
  devices.forEach((d) => {
    const random = states[Math.floor(Math.random() * states.length)];
    applyDeviceState(d.material as THREE.MeshStandardMaterial, random);
  });
}, 2000);
```

运行结果：设备状态每 2 秒变化，观感稳定。  
常见坑：复用同一个材质实例导致“串色”。

### 3.3 完整代码清单

- `src/material/materialFactory.ts`：材质工厂
- `src/material/deviceStateStyle.ts`：状态映射
- `src/main.ts`：示例场景与状态驱动

### 3.4 测试验证

```bash
npm run build
```

验证清单：
1. 三种状态在不同亮度下可区分；
2. 设备状态切换不闪烁；
3. 30 次状态刷新后无异常报错。

## 4. 项目总结（约500-800字）

### 优点
1. 业务语义表达更清晰，值班识别效率高。  
2. 与后续 glTF 模型接轨，风格统一。  
3. 参数集中配置，维护和测试成本可控。

### 缺点
1. 前期需要建立规范和参数基线。  
2. 不同终端仍需做亮度校准。  
3. 材质种类过多会增加性能压力。

### 常见故障案例
1. 设备“发白”：`metalness` 过高 + 环境光过强。  
2. 告警不明显：只改 `color` 未设置 `emissive`。  
3. 状态串色：多个 Mesh 共享同一材质实例。

### 思考题
1. 如果要支持“告警等级 1-5”，如何扩展状态映射避免硬编码？  
2. 如何把材质参数配置化并接入后台管理？

## 跨部门推广提示
- 开发：沉淀统一材质库，禁止散落硬编码。  
- 测试：建立状态色对比基线和截图回归。  
- 运维：关注终端显示差异，制定屏幕校准规范。  
