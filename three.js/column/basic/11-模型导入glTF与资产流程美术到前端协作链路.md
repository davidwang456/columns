# 第11章：模型导入glTF与资产流程美术到前端协作链路

## 元信息
- 学习目标：掌握 glTF 导入、资产规范、模型后处理，打通美术到前端协作流程。
- 先修要求：完成材质、纹理、交互章节。
- 预估时长：5-7 小时。
- 最终产出：可复用的模型加载与校验流程。
- 适用角色：开发、测试、运维。

## 1. 项目背景（约500字）

随着项目进入真实交付阶段，粗模已无法满足展示要求，需要接入美术团队产出的精模。但联调很快暴露问题：模型坐标系不一致、贴图丢失、面数过高、命名混乱、版本反复覆盖。前端同学常陷入“模型能显示但不好用”的状态，测试同学也难以制定稳定回归标准。

如果没有统一资产流程，项目会出现典型协作内耗：美术说“本地没问题”，前端说“线上加载崩”，测试说“每次都不一样”。根因通常不是某一方能力，而是缺乏共同协议：导出格式、尺寸单位、命名规则、压缩策略、验收清单都不统一。

本章聚焦“流程即能力”：采用 glTF 作为交换格式，制定资产规范，前端加载后做标准化处理（缩放、居中、节点重命名、可交互标记），并建立自动检查清单。目标是让模型接入从“人工救火”变成“可复制流水线”。

## 2. 项目设计（剧本式交锋对话，约1200字）

### 第一轮：为什么必须 glTF

小胖：  
“OBJ 也能用，FBX 也能导，为什么非得 glTF？”

小白：  
“多格式会增加转换成本和不确定性。glTF 在 Web 端生态更友好。”

大师：  
“统一格式是协作效率的前提。glTF 在材质、动画、压缩链路上都更适合浏览器。”  

技术映射：  
“统一格式” = `single interchange format (glTF)`。

### 第二轮：模型能显示就行？

小胖：  
“能看到模型就算完成吧？”

小白：  
“还要可交互、可定位、可维护。节点命名和层级结构也必须标准化。”

大师：  
“对。可视只是第一步，业务系统需要语义化节点和稳定 ID。”  

技术映射：  
“可视 + 可语义” = `renderable + operable model`。

### 第三轮：如何减少反复返工

小胖：  
“现在每次都靠人肉对齐，很痛苦。”

小白：  
“是不是应建立导出检查清单和自动校验脚本？”

大师：  
“这是必选项。流程化比个人经验更可靠。”  

技术映射：  
“流程化” = `asset checklist + automated validation`。

## 3. 项目实战（约1500-2000字）

### 3.1 环境准备

```bash
npm install three
```

### 3.2 分步实现

#### 步骤1：接入 GLTFLoader

```ts
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

const gltfLoader = new GLTFLoader();

async function loadModel(url: string) {
  const gltf = await gltfLoader.loadAsync(url);
  return gltf.scene;
}
```

结果：基础模型可加载。  
坑：资源路径不一致导致贴图丢失。

#### 步骤2：标准化后处理

```ts
function normalizeModel(root: THREE.Object3D) {
  const box = new THREE.Box3().setFromObject(root);
  const center = box.getCenter(new THREE.Vector3());
  root.position.sub(center); // 居中

  root.traverse((obj) => {
    if (obj instanceof THREE.Mesh) {
      obj.castShadow = true;
      obj.receiveShadow = true;
      // 绑定语义标签
      if (obj.name.startsWith("Device_")) {
        obj.userData.pickable = true;
      }
    }
  });
}
```

结果：模型坐标与交互语义统一。  
坑：忽略模型单位导致尺寸异常。

#### 步骤3：加载失败兜底与版本标识

```ts
async function safeLoadModel(url: string) {
  try {
    const model = await loadModel(url);
    normalizeModel(model);
    return model;
  } catch (e) {
    console.error("load model failed", url, e);
    return new THREE.Group(); // 兜底空对象
  }
}
```

结果：异常资源不会拖垮全场景。  
坑：不加版本号导致 CDN 缓存旧模型。

### 3.3 完整代码清单

- `src/model/loader.ts`
- `src/model/normalize.ts`
- `src/model/checklist.md`

### 3.4 测试验证

```bash
npm run build
```

验证：
1. 模型可显示且比例正确；  
2. 可交互节点命中准确；  
3. 人为破坏资源时有可预期降级。

## 4. 项目总结（约500-800字）

### 优点
1. 打通美术到前端协作链路。  
2. 模型接入标准化，返工减少。  
3. 为中级篇性能优化打基础。

### 缺点
1. 需要跨团队执行规范。  
2. 老资产改造有成本。  
3. 大模型仍需压缩与切分策略。

### 常见故障案例
1. 贴图丢失：资源相对路径错误。  
2. 比例失真：单位体系不统一。  
3. 不可点选：节点命名与语义标签缺失。

### 思考题
1. 如何设计资产发布流水线（上传、校验、版本化）？  
2. 如何在不改模型文件的情况下补充业务语义？

## 跨部门推广提示
- 开发：沉淀模型接入 SDK。  
- 测试：建立模型验收基线（比例、节点、交互）。  
- 运维：配合模型资源版本回滚机制。  
