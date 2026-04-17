# 第 34 章：Spring Native 与 GraalVM——启动与反射配置

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

## 上一章思考题回顾

1. **Native Image**：**AOT 编译** 为机器码，**启动快、内存低**；需 **反射/资源/动态代理** **reachability metadata**（`reflect-config.json` 或 **Spring AOT** 生成）。  
2. **Spring AOT**：构建阶段生成 **Bean 工厂源码** 与 **hint**，减少运行时反射。

---

## 1 项目背景

**Serverless** 与 **边缘节点** 要求 **冷启动 <200ms**。JVM 预热长；**Native** 换成本：**构建慢**、**部分库不兼容**。

**痛点**：  
- **CGLIB**、**SpEL**、**动态 SQL** 需额外 hint。  
- **调试** 与 **Profiling** 工具链不同。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：冷启动 vs 构建成本 → 反射与动态特性 → 兼容矩阵。

**小胖**：Native 镜像一运行，启动时间吊打 JVM，为啥不全上？

**大师**：换成本：**构建慢**、**生态兼容**、**动态特性**（反射/CGLIB/SpEL）要 **hint**；团队要有 **CI 资源**与**排障**能力。

**技术映射**：**RuntimeHintsRegistrar** + **`@RegisterReflectionForBinding`** + **Spring AOT**。

**小白**：`native-image` 报错「class not found for reflection」？

**大师**：需要 **reachability metadata**（生成或手写）；优先用 **Spring Boot** 的 `process-aot` 与 **`nativeTest`** 迭代。

---

## 3 项目实战

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| JDK | GraalVM 或配合 `native-maven-plugin` 下载 |
| 构建 | `native-maven-plugin` |

```xml
<plugin>
  <groupId>org.graalvm.buildtools</groupId>
  <artifactId>native-maven-plugin</artifactId>
</plugin>
```

### 3.2 分步实现

`mvn -Pnative native:compile`，运行 `./target/order-service`。

**运行结果（文字描述）**：冷启动时间显著下降；首次构建时间显著上升（视机器而定）。

### 3.3 完整代码清单与仓库

`chapter34-native`。

### 3.4 测试验证

**nativeTest** 与 **JUnit** 在 native 模式运行。

**命令**：`mvn -q -Pnative test`（若配置 nativeTest）。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 反射失败 | 缺 hint | 注册 `RuntimeHints` |
| 资源找不到 | 未打包 resources | `resource-config` |

---

## 4 项目总结

### 常见踩坑经验

1. **缺少** `resource-config.json`。  
2. **Agent** 追踪生成配置。  
3. **第三方** JNI 不兼容。

---

## 思考题

1. **Allocation profiling** 与 **heap**？（第 35 章。）  
2. ** biased lock 废除** 对锁竞争影响？（第 35 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **运维** | CI 中增加 native 构建流水线。 |

**下一章预告**：JVM 内存、锁、Async Profiler。
