# 第19章：后处理管线BloomSSAOOutline在业务中的取舍

## 元信息
- 学习目标：掌握常见后处理效果的业务价值与性能成本，建立可配置的后处理管线。
- 先修要求：掌握基础渲染优化与资源治理。
- 预估时长：5-7 小时。
- 最终产出：基于场景等级自动开关的后处理策略。
- 适用角色：开发、测试、运维。

## 1. 项目背景（约500字）

项目在视觉升级阶段经常出现两极：要么“太素”，缺少重点；要么“太花”，性能下降。Bloom、SSAO、Outline 等后处理本身都很有价值，但在业务系统中不是越多越好。核心问题在于没有“效果收益-性能成本”评估机制，导致特效叠加失控。

业务场景要求通常很明确：告警对象需要更显眼（Outline/Bloom）、空间层次要更真实（SSAO），但必须保证实时交互流畅。尤其是监控大屏，稳定性优先于炫技。若后处理策略没有分级，低端设备上会直接掉帧，反而影响业务使用。

本章目标是建立“可解释的后处理管线”：为每个效果定义适用场景、启用条件和降级策略，支持按终端性能级别动态开关。让后处理成为可运营能力，而不是演示专用特效。

## 2. 项目设计（剧本式交锋对话，约1200字）

### 第一轮：效果越多越高级？

小胖：  
“Bloom、SSAO、景深全开，画面绝对高级。”

小白：  
“高级不等于可用。业务场景更看重信息传达和稳定帧率。”

大师：  
“特效是放大器，不是主角。主角永远是业务信息。”  

技术映射：  
“信息优先” = `business readability first`。

### 第二轮：如何做取舍

小胖：  
“那到底留哪些？”

小白：  
“应该按场景目的选：告警强调用 Outline/Bloom，空间层次用 SSAO。”

大师：  
“对，按目标选效果，不按流行选效果。”  

技术映射：  
“目标驱动” = `use-case-driven postprocessing`。

### 第三轮：低配设备怎么办

小胖：  
“低配设备顶不住，岂不是效果全废？”

小白：  
“可以做质量等级：high/medium/low。”

大师：  
“对。先保障可用，再追求精致。”  

技术映射：  
“质量等级” = `adaptive quality profile`。

## 3. 项目实战（约1500-2000字）

### 3.1 环境准备

```bash
npm install three
```

### 3.2 分步实现

#### 步骤1：构建 EffectComposer 管线

```ts
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";

const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
```

结果：渲染入口切换到后处理管线。  
坑：仍调用 `renderer.render` 导致双渲染。

#### 步骤2：按需接入 Bloom/Outline

```ts
// 伪代码：根据业务配置启用效果
const enableBloom = profile !== "low";
const enableOutline = true;
// composer.addPass(bloomPass);
// composer.addPass(outlinePass);
```

结果：告警对象突出，视觉层级增强。  
坑：Outline 作用对象集合未更新导致错描边。

#### 步骤3：质量分级降级策略

```ts
type Quality = "high" | "medium" | "low";

function applyQuality(q: Quality) {
  if (q === "low") {
    disableSSAO();
    reduceBloomStrength();
  } else if (q === "medium") {
    disableSSAO();
  }
}
```

结果：不同设备都可用。  
坑：切换质量时未同步更新 render target 尺寸。

### 3.3 完整代码清单

- `src/postprocess/pipeline.ts`
- `src/postprocess/profile.ts`
- `src/postprocess/effects/*`

### 3.4 测试验证

```bash
npm run build
```

验证：
1. 高质量模式效果完整；
2. 低质量模式帧率提升明显；
3. 告警描边在三种模式都正确。

## 4. 项目总结（约500-800字）

### 优点
1. 视觉表达更聚焦业务重点。  
2. 支持多设备分级运行。  
3. 管线化便于持续扩展。

### 缺点
1. 管线复杂度上升。  
2. 效果叠加易带来性能风险。  
3. 参数调优需要持续迭代。

### 常见故障案例
1. 画面发糊：后处理链顺序错误。  
2. 掉帧严重：SSAO 在低端设备未关闭。  
3. 描边错位：相机和 pass 参数不同步。

### 思考题
1. 如何做自动质量探测并动态切档？  
2. 如何评估“视觉收益”是否值得对应性能成本？

## 跨部门推广提示
- 开发：为每个特效定义业务目的与性能预算。  
- 测试：建立多机型质量档回归矩阵。  
- 运维：观察不同档位下帧率与错误率。  
