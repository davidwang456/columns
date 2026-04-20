# 第15章：模块化工程实践ViteTypeScriptThreejs目录设计

## 元信息
- 学习目标：构建可持续迭代的工程目录，解决多人协作下“文件乱、依赖乱、职责乱”问题。
- 先修要求：理解第 14 章分层架构。
- 预估时长：4-6 小时。
- 最终产出：一套包含模块边界、别名、构建脚本和代码规范的工程模板。
- 适用角色：开发、测试、运维。

## 1. 项目背景（约500字）

进入中级阶段后，项目文件通常会迅速膨胀。很多团队一开始目录很干净，三个月后就变成“到处都是 util、service、helper”。问题不是文件多，而是缺少组织规则：新需求不知道放哪，老代码没人敢动，跨模块引用随意穿透，最终导致协作效率下降。

Vite + TypeScript + three.js 组合本身足够灵活，但灵活也意味着容易失控。没有统一目录规范时，最常见后果是：打包构建时间变长、循环依赖频发、测试难以落地、上线风险增大。特别是 3D 项目，资源和渲染逻辑天然复杂，更需要工程纪律。

本章目标是落地一套可复制模板：按 `core/domain/render/feature/shared` 分层目录，统一路径别名，约束依赖方向，拆分配置，形成“新成员一看就懂、老成员长期可维护”的工程基线。

## 2. 项目设计（剧本式交锋对话，约1200字）

### 第一轮：目录随手建不行吗

小胖：  
“写到哪算哪，等乱了再整理呗。”

小白：  
“后整理成本很高，而且会阻塞需求。规范最好在规模前建立。”

大师：  
“工程规范是护城河，不是束缚。越早建立，后面越省心。”  

技术映射：  
“先规范后扩张” = `scaffold-first strategy`。

### 第二轮：别名值不值得配

小胖：  
“相对路径也能用，为啥要配 `@/` 别名？”

小白：  
“深层目录 `../../../../` 可读性很差，重构时也容易改漏。”

大师：  
“别名提升可读性和重构安全性，是中大型项目标配。”  

技术映射：  
“路径别名” = `tsconfig paths + vite resolve.alias`。

### 第三轮：依赖规则如何落地

小胖：  
“规则写在文档里就好了吧？”

小白：  
“仅靠文档不够，最好有 lint 或约定检查。”

大师：  
“文档+工具双保险。口头约定扛不住高频迭代。”  

技术映射：  
“双保险” = `convention + automated checks`。

## 3. 项目实战（约1500-2000字）

### 3.1 环境准备

```bash
npm create vite@latest chapter-15-template -- --template vanilla-ts
cd chapter-15-template
npm install three
```

### 3.2 分步实现

#### 步骤1：规划目录结构

```txt
src/
  app/          # 入口装配
  core/         # 生命周期、事件总线、基础设施
  domain/       # 业务模型和规则
  render/       # three 场景渲染
  feature/      # 具体业务功能
  shared/       # 通用工具
```

结果：职责清晰，定位快。  
坑：把共享目录当“垃圾桶”。

#### 步骤2：配置路径别名

```ts
// vite.config.ts
import { defineConfig } from "vite";
import path from "node:path";

export default defineConfig({
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") }
  }
});
```

```json
// tsconfig.json
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": { "@/*": ["src/*"] }
  }
}
```

结果：导入路径稳定可读。  
坑：只配一端导致编辑器与构建行为不一致。

#### 步骤3：装配入口与模块注册

```ts
// src/app/bootstrap.ts
import { initStore } from "@/core/store";
import { initRenderer } from "@/render/initRenderer";
import { mountAlarmFeature } from "@/feature/alarm";

export function bootstrap() {
  initStore();
  initRenderer();
  mountAlarmFeature();
}
```

结果：功能模块可插拔。  
坑：在模块内部直接 new 全局单例，难以测试替换。

### 3.3 完整代码清单

- `src/app/bootstrap.ts`
- `src/core/*`
- `src/render/*`
- `src/feature/*`
- `vite.config.ts`、`tsconfig.json`

### 3.4 测试验证

```bash
npm run build
```

验证：
1. 别名导入可正常编译；
2. 模块拆分后功能不回归；
3. 新功能可按规范落目录并被快速定位。

## 4. 项目总结（约500-800字）

### 优点
1. 多人协作效率显著提升。  
2. 重构与扩展风险降低。  
3. 测试与构建链路更稳定。

### 缺点
1. 规范学习成本。  
2. 需要持续维护边界规则。  
3. 过度细分目录可能增加心智负担。

### 常见故障案例
1. 循环依赖导致运行异常。  
2. 别名配置不一致导致 CI 失败。  
3. shared 目录泛滥导致边界再次模糊。

### 思考题
1. 如何在 monorepo 场景复用该模板？  
2. 如何给目录规范增加自动化审查？

## 跨部门推广提示
- 开发：把模板沉淀成脚手架。  
- 测试：按目录定义测试策略。  
- 运维：把构建产物结构纳入发布检查。  
