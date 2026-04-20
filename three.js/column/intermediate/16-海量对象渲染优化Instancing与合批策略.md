# 第16章：海量对象渲染优化Instancing与合批策略

## 元信息
- 学习目标：掌握 `InstancedMesh` 与合批策略，解决海量对象下 draw call 爆炸问题。
- 先修要求：理解分层架构与模块化工程。
- 预估时长：5-7 小时。
- 最终产出：支持万级对象渲染的性能优化方案。
- 适用角色：开发、测试、运维。

## 1. 项目背景（约500字）

当仓储和园区场景扩展到上万对象后，系统出现明显掉帧：GPU 占用看似正常，但 CPU 主线程持续高负载，交互延迟变大。排查发现主要瓶颈在 draw calls：每个对象独立 mesh，导致渲染命令数量过多。即使单个对象很简单，累计开销也会压垮实时渲染。

很多项目第一反应是“换更高配机器”，但这只是掩盖问题。真正可持续方案是减少提交次数：对重复几何和材质对象使用实例化；对静态对象进行合批；对动态对象进行分组更新。中级阶段必须建立这套方法论，否则后续功能越加越卡。

本章目标是把“对象数量”与“渲染成本”脱钩。你将学会判断什么对象适合实例化、什么时候要合并几何、如何保留业务 ID 映射与交互能力。

## 2. 项目设计（剧本式交锋对话，约1200字）

### 第一轮：对象多就一定卡吗

小胖：  
“1 万个箱子肯定会卡，没救吧？”

小白：  
“关键不是对象数量，是提交次数和状态切换次数。”

大师：  
“对。相同模型重复渲染时，实例化能把 N 次提交压成 1 次。”  

技术映射：  
“压提交” = `reduce draw calls with instancing`。

### 第二轮：实例化会丢失交互吗

小胖：  
“用了 InstancedMesh，是不是就点不中单个对象？”

小白：  
“可以通过 instanceId 映射回业务 ID。”

大师：  
“实例化不等于失去语义，只是把语义映射从对象引用变成索引表。”  

技术映射：  
“语义映射” = `instanceId -> businessId map`。

### 第三轮：合批与实例化怎么选

小胖：  
“那都用实例化就行了？”

小白：  
“静态异构对象可能更适合合批，动态对象更适合实例化。”

大师：  
“方案不是二选一，而是组合：静态合批、重复实例、高频动态单独处理。”  

技术映射：  
“组合策略” = `hybrid batching strategy`。

## 3. 项目实战（约1500-2000字）

### 3.1 环境准备

```bash
npm install three
```

### 3.2 分步实现

#### 步骤1：使用 InstancedMesh 渲染重复对象

```ts
const count = 10000;
const geometry = new THREE.BoxGeometry(1, 1, 1);
const material = new THREE.MeshStandardMaterial({ color: "#22c55e" });
const instances = new THREE.InstancedMesh(geometry, material, count);
const matrix = new THREE.Matrix4();

for (let i = 0; i < count; i++) {
  matrix.makeTranslation((i % 100) * 1.2, 0.5, Math.floor(i / 100) * 1.2);
  instances.setMatrixAt(i, matrix);
}
instances.instanceMatrix.needsUpdate = true;
scene.add(instances);
```

结果：draw calls 大幅下降。  
坑：忘记 `needsUpdate` 导致位置不生效。

#### 步骤2：构建 instanceId 映射

```ts
const instanceToBizId = new Map<number, string>();
for (let i = 0; i < count; i++) instanceToBizId.set(i, `slot-${i}`);

function onPick(intersection: THREE.Intersection) {
  if (intersection.instanceId == null) return;
  const bizId = instanceToBizId.get(intersection.instanceId);
  if (bizId) selectByBusinessId(bizId);
}
```

结果：保留点选语义。  
坑：重建实例后未同步更新映射表。

#### 步骤3：静态几何合批

```ts
// 示例逻辑：将静态装饰物几何合并（伪代码）
// const merged = BufferGeometryUtils.mergeGeometries(geometries, false);
// scene.add(new THREE.Mesh(merged, staticMaterial));
```

结果：静态对象进一步减少提交。  
坑：合批后单体对象不再可独立控制。

### 3.3 完整代码清单

- `src/perf/instancing.ts`
- `src/perf/idMap.ts`
- `src/perf/staticBatch.ts`

### 3.4 测试验证

```bash
npm run build
```

验证：
1. 对比优化前后 draw calls；
2. 点选仍能定位到正确业务对象；
3. 1 万对象场景保持可交互帧率。

## 4. 项目总结（约500-800字）

### 优点
1. 性能收益显著。  
2. 适合重复对象密集场景。  
3. 与业务语义可兼容。

### 缺点
1. 动态更新复杂度上升。  
2. 调试难度高于普通 mesh。  
3. 合批会牺牲对象独立性。

### 常见故障案例
1. instance 矩阵更新遗漏。  
2. 映射表错位导致点选错对象。  
3. 合批后材质差异丢失。

### 思考题
1. 如何对 InstancedMesh 做局部高亮？  
2. 海量动态对象下如何做分帧更新？

## 跨部门推广提示
- 开发：建立性能预算和对象分层策略。  
- 测试：加入 draw calls 回归指标。  
- 运维：采集低端设备性能样本。  
