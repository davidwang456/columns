# 第 26 章：容器源码——BeanFactory 与 ApplicationContext

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章偏源码阅读，可结合调试。

## 上一章思考题回顾

1. **`BeanFactory`**：最小容器接口，**延迟初始化**（`getBean` 时才创建 prototype 等）；**`ApplicationContext`** 组合 **`BeanFactory`** 并增加 **事件、国际化、资源加载、自动 BeanPostProcessor 注册** 等。  
2. **三级缓存**：解决 **单例 setter 循环依赖** 的早期引用暴露（**singletonObjects / earlySingletonObjects / singletonFactories**）。

---

## 1 项目背景

线上 **Bean 创建顺序** 异常、**父子容器** Bean 覆盖问题，需要读 **`refresh()`** 流程定位。

**痛点**：  
- 只看文档无法解释 **为什么 `getBean` 触发一整套初始化**。  
- **后置处理器** 顺序影响 **AOP/事务** 代理。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：从「容器好像黑盒」→ `refresh` 主线 → `BeanPostProcessor` 为何能改天换地。

**小胖**：`ApplicationContext` 和 `BeanFactory` 不就是 `getBean` 吗？我背 API 就够了。

**大师**：`BeanFactory` 是**最小容器**；`ApplicationContext` 在之上叠了 **事件、资源、MessageSource、自动注册后置处理器**。你调 `getBean` 背后可能走过 **一整条初始化链**。

**技术映射**：**BeanDefinitionRegistry** + **BeanPostProcessor** + **BeanFactoryPostProcessor**（阶段不同）。

**小白**：`refresh()` 为啥这么长？

**大师**：它把**配置元数据**转成**运行时对象网**：准备 BeanFactory → 处理工厂后置 → 注册 Bean 后置 → **finishBeanFactoryInitialization**（单例预实例化）。

**技术映射**：**AbstractApplicationContext#refresh** 是阅读 Spring 的「**主航道**」。

**小胖**：`BeanPostProcessor` 是不是「偷偷改我类」的黑魔法？

**大师**：**AOP 代理**、**@Autowired** 处理、**校验注解**都可能通过后处理器完成；所以顺序敏感——**排错时要看代理链**。

---

## 3 项目实战

### 3.1 阅读路径

1. `AbstractApplicationContext.refresh()`  
2. `DefaultListableBeanFactory#getBean`  
3. `AbstractAutowireCapableBeanFactory.doCreateBean`

### 3.2 调试

在 `doCreateBean` 打断点，观察 **单例三级缓存** 与 **earlySingletonExposure**；再用 **Evaluate** 查看 `singletonObjects` 与 `earlySingletonObjects`。

### 3.3 完整代码清单与仓库

本地 **spring-framework** 源码或依赖 **sources.jar**；IDE **Download Sources** 即可。

### 3.4 测试验证

写最小 `AnnotationConfigApplicationContext` 与 **循环依赖** 示例，对照调用栈；记录 **10 次 `Step Into` 的关键帧**（训练「源码阅读笔记」）。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 断点不进 | 断在接口而非实现类 | 对实现类下断 |
| 与文档不一致 | 版本差异 | 以当前版本源码为准 |

---

## 4 项目总结

### 常见踩坑经验

1. **BeanFactoryPostProcessor** 与 **BeanPostProcessor** 阶段混淆。  
2. **早期引用** 暴露半成品 Bean。  
3. **父子容器** 单例查找顺序。

---

## 思考题

1. **构造器循环依赖** 为何难解决？（第 27 章。）  
2. **`ObjectFactory` 注入** 用途？（第 27 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **资深开发** | 结合 ARTHAS 观察 Bean 代理类。 |

**下一章预告**：循环依赖、三级缓存、边界。
