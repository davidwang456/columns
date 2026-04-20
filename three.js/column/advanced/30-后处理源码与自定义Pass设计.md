# 第30章：后处理源码与自定义 Pass 设计

## 元信息
- 学习目标：深入理解 EffectComposer 机制并实现一个可复用自定义 Pass。
- 先修要求：掌握 Shader 与后处理基础。
- 预估时长：6-8 小时。
- 最终产出：自定义业务 Pass（如告警热点增强）及插件化接入方案。
- 适用角色：资深开发、架构师。

## 1. 项目背景（约500字）

中级篇已经使用了 Bloom/Outline 等标准后处理，但高级项目常常遇到“现成 Pass 不够用”：业务需要只对特定对象叠加热度层、按告警等级动态调色、对多场景切换做一致视觉过渡。直接堆第三方效果既不精准，也不稳定，最终会导致复杂度和性能双重失控。

要解决这类问题，必须理解后处理源码：渲染目标如何流转、readBuffer/writeBuffer 如何交换、pass 之间如何组合。没有这些认知，自定义 Pass 容易出现黑屏、顺序错乱、重复清屏、alpha 污染等问题。

本章目标是搭建可维护的 Pass 体系：统一 Pass 生命周期、参数协议、质量档位、降级开关，并实现一个业务可用示例 Pass，作为后续复杂特效的基础设施。

## 2. 项目设计（剧本式交锋对话，约1200字）

### 第一轮：现成 Pass 不够吗

小胖：  
“官方都给了这么多 pass，为什么还要自定义？”

小白：  
“标准 pass 是通用能力，业务语义常常需要定制化处理。”

大师：  
“通用工具解决 80%，业务竞争力在剩下 20%。”  

技术映射：  
“剩下 20%” = `domain-specific rendering pass`。

### 第二轮：Pass 链怎么保证稳定

小胖：  
“我把新 pass 插进去，结果全屏发灰。”

小白：  
“可能是 clear、blend 或 buffer 交换处理错误。”

大师：  
“Pass 是流水线，顺序和输入输出契约必须严格一致。”  

技术映射：  
“流水线契约” = `pass I/O contract`。

### 第三轮：如何避免性能失控

小胖：  
“特效好看就开着呗。”

小白：  
“自定义 pass 通常是全屏片元计算，成本可能很高。”

大师：  
“每个 pass 都要有预算、档位、开关，做到可观测可降级。”  

技术映射：  
“可降级 pass” = `budgeted adaptive pass`。

## 3. 项目实战（约1500-2000字）

### 3.1 环境准备

```bash
npm install three
```

### 3.2 分步实现

#### 步骤1：理解 Composer 双缓冲机制

```ts
const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
// 后续 pass 读取 readBuffer，写入 writeBuffer，框架内部 swap
```

结果：明确 pass 执行顺序和 buffer 生命周期。  
坑：误用 `renderToScreen` 导致链路中断。

#### 步骤2：实现自定义 Pass

```ts
class HeatPulsePass extends Pass {
  material: THREE.ShaderMaterial;
  fsQuad: FullScreenQuad;
  constructor() {
    super();
    this.material = new THREE.ShaderMaterial({
      uniforms: { tDiffuse: { value: null }, uIntensity: { value: 0.6 } },
      vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.0,1.0); }`,
      fragmentShader: `
        uniform sampler2D tDiffuse; uniform float uIntensity; varying vec2 vUv;
        void main() {
          vec4 c = texture2D(tDiffuse, vUv);
          float heat = smoothstep(0.4, 1.0, c.r) * uIntensity;
          gl_FragColor = vec4(c.rgb + vec3(heat, heat * 0.2, 0.0), c.a);
        }
      `
    });
    this.fsQuad = new FullScreenQuad(this.material);
  }
  render(renderer: THREE.WebGLRenderer, write: THREE.WebGLRenderTarget, read: THREE.WebGLRenderTarget) {
    this.material.uniforms.tDiffuse.value = read.texture;
    renderer.setRenderTarget(this.renderToScreen ? null : write);
    this.fsQuad.render(renderer);
  }
}
```

结果：可对全屏颜色做业务增强。  
坑：未处理 clear 导致残影。

#### 步骤3：接入配置和降级

```ts
function applyPassProfile(level: "high" | "medium" | "low") {
  heatPass.enabled = level !== "low";
  heatPass.material.uniforms.uIntensity.value = level === "high" ? 0.8 : 0.4;
}
```

结果：自定义 pass 可控可降级。  
坑：关闭 pass 时忘记恢复基线效果。

### 3.3 完整代码清单

- `src/post/custom/HeatPulsePass.ts`
- `src/post/pipeline.ts`
- `src/post/profile.ts`

### 3.4 测试验证

```bash
npm run build
```

验证：
1. Pass 开关与强度参数即时生效；
2. 链路顺序变更后画面仍稳定；
3. 低配模式关闭 pass 后帧率回升。

## 4. 项目总结（约500-800字）

### 优点
1. 业务视觉能力可定制。  
2. 后处理架构可扩展。  
3. 性能与效果可按档位平衡。

### 缺点
1. 开发调试复杂。  
2. 对图形管线理解要求高。  
3. 版本升级可能带来兼容风险。

### 常见故障案例
1. 黑屏：输入纹理绑定错误。  
2. 色彩异常：混合或色彩空间处理不一致。  
3. 帧率骤降：Pass 过多且未降级。

### 思考题
1. 如何为 Pass 增加可视化调试面板？  
2. 如何设计可插拔 Pass 市场化机制？

## 跨部门推广提示
- 开发：维护统一 Pass 接口与编码规范。  
- 测试：新增 Pass 链顺序与质量档位回归。  
- 运维：观察启用不同 pass 组合后的性能指标。  
