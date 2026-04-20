# 第29章：Shader 入门到实战 GLSL 定制业务特效

## 元信息
- 学习目标：掌握 Shader 基础与业务化落地方法，能独立实现可控特效。
- 先修要求：理解 geometry/material 机制与渲染流程。
- 预估时长：6-8 小时。
- 最终产出：一个可参数化的业务特效 Shader 组件（如告警波纹/流光）。
- 适用角色：资深开发。

## 1. 项目背景（约500字）

当项目进入高级视觉阶段，标准材质往往不足以表达业务语义。例如告警区域需要“脉冲扩散”，管线状态需要“沿路径流动”，风险热区需要“渐进扩散”。这些效果用纯贴图和动画可以做，但控制力差、复用性低、性能不可预测。此时 Shader 成为关键能力。

很多团队对 Shader 的误解是“炫技专属”，其实在数字孪生里，Shader 是信息表达工具。关键在于工程化：效果必须参数可配置、性能可预算、逻辑可回退。若只写一次性 GLSL 片段，后续维护会非常痛苦，尤其跨团队协作时。

本章目标是从业务视角掌握 Shader：先理解顶点/片元职责，再实现一个告警波纹特效，并封装为可复用组件，支持主题色、速度、半径、强度等参数控制。

## 2. 项目设计（剧本式交锋对话，约1200字）

### 第一轮：Shader 会不会太难

小胖：  
“一看到 GLSL 我就头大，是不是没必要学这么深？”

小白：  
“不必一口吃成专家，但核心机制必须懂，不然高级视觉全靠碰运气。”

大师：  
“Shader 不是玄学，先从可控小效果开始，再逐步抽象。”  

技术映射：  
“小步上手” = `incremental shader adoption`。

### 第二轮：为什么不用后处理替代

小胖：  
“后处理也能做发光和波纹，为啥还写 Shader？”

小白：  
“后处理偏全屏，局部语义效果更适合对象级 Shader。”

大师：  
“对象级语义建议 Shader，屏幕级风格建议后处理，各司其职。”  

技术映射：  
“各司其职” = `object-space vs screen-space effects`。

### 第三轮：如何工程化维护

小胖：  
“GLSL 写在字符串里，改起来很痛。”

小白：  
“可拆分 common chunk，统一 uniform 约定和参数校验。”

大师：  
“Shader 也要像业务代码一样模块化和可测试。”  

技术映射：  
“Shader 工程化” = `modular shader pipeline`。

## 3. 项目实战（约1500-2000字）

### 3.1 环境准备

```bash
npm install three
```

### 3.2 分步实现

#### 步骤1：定义基础 ShaderMaterial

```ts
const material = new THREE.ShaderMaterial({
  uniforms: {
    uTime: { value: 0 },
    uColor: { value: new THREE.Color("#ef4444") },
    uRadius: { value: 1.5 }
  },
  vertexShader: `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: `
    uniform float uTime;
    uniform vec3 uColor;
    uniform float uRadius;
    varying vec2 vUv;
    void main() {
      float d = distance(vUv, vec2(0.5));
      float wave = sin((d * 20.0) - uTime * 3.0) * 0.5 + 0.5;
      float alpha = smoothstep(uRadius, uRadius - 0.2, d) * wave;
      gl_FragColor = vec4(uColor, alpha);
    }
  `,
  transparent: true
});
```

结果：基础波纹可见。  
坑：忘记 `transparent` 导致混合异常。

#### 步骤2：驱动时间 uniform

```ts
const clock = new THREE.Clock();
function animate() {
  material.uniforms.uTime.value = clock.getElapsedTime();
  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}
```

结果：波纹持续动态。  
坑：时间步长过大导致闪烁。

#### 步骤3：封装业务特效组件

```ts
type AlertFxConfig = { color: string; speed: number; radius: number };

function createAlertFx(config: AlertFxConfig) {
  // 返回 mesh + 更新接口，供业务层调用
}
```

结果：效果可参数化复用。  
坑：参数无边界校验导致极值异常。

### 3.3 完整代码清单

- `src/shader/alertWave.vert.glsl`
- `src/shader/alertWave.frag.glsl`
- `src/shader/createAlertFx.ts`

### 3.4 测试验证

```bash
npm run build
```

验证：
1. 不同颜色/速度配置可实时生效；
2. 多实例同时运行无明显掉帧；
3. 关闭特效后可恢复默认材质。

## 4. 项目总结（约500-800字）

### 优点
1. 视觉表达自由度高。  
2. 业务语义可精确映射。  
3. 效果可组件化复用。

### 缺点
1. 学习和调试门槛高。  
2. 不同 GPU 兼容性需验证。  
3. 容易出现性能过载。

### 常见故障案例
1. 黑屏：Shader 编译错误未捕获。  
2. 特效穿帮：深度与混合配置冲突。  
3. 帧率下降：片元计算过重。

### 思考题
1. 如何将 Shader 参数与业务告警等级自动映射？  
2. 如何构建团队级 GLSL 代码规范与 review 清单？

## 跨部门推广提示
- 开发：沉淀通用 Shader 组件库。  
- 测试：补充多机型兼容和性能用例。  
- 运维：关注开启特效后的资源占用变化。  
