# 第01章：Three.js全景认知与首个业务级3D看板

## 元信息
- 学习目标：理解 Three.js 核心对象关系，完成一个可运行的业务看板最小闭环。
- 先修要求：掌握 JavaScript 基础与浏览器调试方法。
- 预估时长：4-6 小时。
- 最终产出：一个包含场景、相机、光照、基础模型和动画循环的看板页面。
- 适用角色：开发、测试。

## 1. 项目背景（约500字）

某制造企业准备在总部大屏上线“设备运行态势看板”。原先方案使用二维图表拼接，虽然能展示产量和告警数量，但管理层反馈两个问题：第一，看不出产线空间关系，无法快速定位问题设备；第二，多个页面之间切换频繁，值班人员需要“脑补”位置关系，响应效率低。业务团队希望把厂区、车间和设备关系统一放到一个 3D 画面中，支持点击设备查看状态，并能基于实时数据进行颜色变化。

如果没有 three.js，这类需求通常会落到“重型 3D 引擎 + 长周期开发”或者“2D 方案凑合上线”两条路径。前者成本高、前端接入难；后者表达力弱，扩展性差。更现实的痛点是：项目上线后会持续迭代，今天要加告警闪烁，明天要加巡检路径，后天要加多屏适配。如果技术选型没有工程化能力，后期维护成本会急速上升。

本章目标是做一个可交付的最小项目：单页加载厂区简化模型，展示 3 台设备，支持自动旋转和状态高亮。重点不是“画面多炫”，而是建立三维业务项目的标准骨架：`Scene` 管内容、`Camera` 管视角、`Renderer` 管输出、`AnimationLoop` 管实时更新。

## 2. 项目设计（剧本式交锋对话，约1200字）

### 第一轮：先上 3D 还是先上业务

小胖：  
“我看网上 demo 都是会飞来飞去的炫酷特效，咱要不先做个超酷大屏，领导一看就拍板？”

小白：  
“炫酷没问题，但业务指标先落地吧？如果只好看不好用，后面改动会很大。我们是不是要先定义最小可交付范围？”

大师：  
“先做业务闭环。第一版只做三件事：看得到设备、点得到设备、状态能变化。特效可以后补。你把它当成开餐厅，先保证菜能出、味道稳定，再考虑摆盘。”  

技术映射：  
“先做最小可交付范围” = `MVP + 可扩展架构`。

### 第二轮：技术选型是不是过度

小胖：  
“那为啥非 three.js？直接 Canvas 画方块不行吗？也能看设备啊。”

小白：  
“Canvas 可以画，但要自己处理透视、光照、交互拾取，复杂度会不会更高？还有模型导入怎么办？”

大师：  
“three.js 的优势不是‘画一个立方体’，而是把三维工程常见能力都准备好了：坐标变换、材质光照、加载器、后处理、控制器。你自己从零造轮子，后面每个需求都要重复造一次。”  

技术映射：  
“不重复造轮子” = `复用引擎能力 + 降低长期维护成本`。

### 第三轮：如何保证后续可维护

小胖：  
“我担心代码一多就乱，最后谁都不敢改。”

小白：  
“是不是要一开始就分层？比如渲染层、业务状态层、交互层。”

大师：  
“没错。第一章就立规矩：初始化逻辑独立、业务对象注册表独立、动画循环统一入口。这样第 5 章加阴影、第 8 章加拾取都不用推倒重来。”  

技术映射：  
“先立规矩” = `分层设计 + 单一职责 + 低耦合`。

## 3. 项目实战（约1500-2000字）

### 3.1 环境准备

- Node.js 20.x（LTS）
- npm 10+
- three（与项目当前版本保持一致）
- vite + typescript

```bash
npm create vite@latest chapter-01-dashboard -- --template vanilla-ts
cd chapter-01-dashboard
npm install three
npm run dev
```

### 3.2 分步实现

#### 步骤1：搭建渲染骨架

目标：创建 `Scene/Camera/Renderer`，确保页面可渲染。

```ts
import * as THREE from "three";

const scene = new THREE.Scene();
scene.background = new THREE.Color("#0b1020");

const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 1000);
camera.position.set(8, 6, 10);
camera.lookAt(0, 0, 0);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);
```

运行结果：页面显示深色背景，控制台无报错。  
常见坑：
- 看不到内容：相机位置和朝向错误。
- 画面变形：未处理窗口 resize。

#### 步骤2：放入地面和设备模型（简化几何体）

目标：快速构建“厂区 + 设备”可视对象。

```ts
const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(20, 20),
  new THREE.MeshStandardMaterial({ color: "#1e293b" })
);
ground.rotation.x = -Math.PI / 2;
scene.add(ground);

const deviceMaterial = new THREE.MeshStandardMaterial({ color: "#22c55e" });
const devices: THREE.Mesh[] = [];

for (let i = 0; i < 3; i++) {
  const device = new THREE.Mesh(new THREE.BoxGeometry(1, 2, 1), deviceMaterial.clone());
  device.position.set(-4 + i * 4, 1, 0);
  device.name = `device-${i + 1}`;
  devices.push(device);
  scene.add(device);
}
```

运行结果：地面上出现 3 个设备块。  
常见坑：
- 平面不可见：默认只渲染正面，注意旋转或材质 `side`。

#### 步骤3：加光照与动画循环

目标：让设备有立体感，并实现状态动画。

```ts
const ambient = new THREE.AmbientLight("#ffffff", 0.4);
scene.add(ambient);

const directional = new THREE.DirectionalLight("#ffffff", 1.2);
directional.position.set(6, 10, 4);
scene.add(directional);

let tick = 0;
function animate() {
  tick += 0.02;
  devices.forEach((d, i) => {
    d.scale.y = 1 + Math.sin(tick + i) * 0.08; // 模拟设备状态波动
  });
  scene.rotation.y += 0.002; // 轻微自动旋转，便于总览
  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}
animate();
```

运行结果：设备有轻微“呼吸感”，场景缓慢旋转。  
常见坑：
- 帧率不稳：动画中避免每帧创建新对象。

### 3.3 完整代码清单

- `src/main.ts`：初始化与主循环
- `src/domain/device.ts`：设备数据模型（后续章节扩展）
- `src/styles.css`：画布容器样式

### 3.4 测试验证

```bash
npm run build
```

验收点：
- 构建通过；
- 页面加载 2 秒内可见场景；
- 窗口缩放后无拉伸；
- 控制台无未处理异常。

## 4. 项目总结（约500-800字）

### 优点
1. 快速搭建三维业务表达，首版上线效率高。  
2. 生态成熟，后续可平滑引入模型加载与后处理。  
3. 与前端工程体系兼容，便于 CI/CD 与测试接入。

### 缺点
1. 对三维基础有学习门槛。  
2. 首版如果不做架构分层，后续迭代容易失控。  
3. 复杂场景下性能问题会快速暴露。

### 适用与不适用
- 适用：数字孪生看板、设备监控、园区导览。  
- 不适用：超高精度 CAD 编辑、离线影视级渲染。

### 常见故障案例
1. 首屏黑屏：相机 near/far 设置不合理。  
2. 场景发灰：光照强度和材质参数不匹配。  
3. 长时间卡顿：对象销毁未释放导致内存增长。

### 思考题
1. 如果设备数量从 3 台扩展到 3000 台，本章结构应先改哪一层？  
2. 如何在不改业务层代码的情况下，为场景增加“告警闪烁”效果？

## 跨部门推广提示
- 开发：先统一项目骨架，再分工开发模块。  
- 测试：从首章开始建立“黑屏/拉伸/性能”基础检查项。  
- 运维：记录首屏时长与帧率，作为后续优化基线。  
