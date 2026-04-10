# 第 26 章：构建期 Augmentation 与扩展机制

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 60 分钟（含 IDE 导航） |
| **学习目标** | 解释 augmentation；能在本仓库定位 `*Processor`；区分 build time vs runtime 配置 |
| **先修** | 第 2 章；本 Quarkus 源码检出 |

---

## 1. 项目背景

Quarkus 在 **Maven/Gradle compile** 阶段运行扩展的 **deployment** 模块，消费与生产 **BuildItem**。理解该机制是阅读构建日志、编写扩展、升级版本不慌的前提。

---

## 2. 项目设计：大师与小白的对话

**小白**：「谁生成了那些类？」

**大师**：「**deployment 处理器**。用 IDE 全局搜 `BuildStep`。」

**架构师**：「我们能在 runtime 改 augmentation 结果吗？」

**大师**：「不能简单改；那是构建期产物。runtime 配置应是设计允许的那部分。」

**运维**：「为什么 CI 有时只改配置也要重新 build？」

**大师**：「部分配置是 **build time fixed**；见官方配置清单。」

---

## 3. 知识要点

- `extensions/<name>/deployment/`  
- `FeatureBuildItem`、`BytecodeRecorder`（概念）  
- `mvn -X` 或 `-Dquarkus.debug.build=true`（若支持）辅助排障

---

## 4. 项目实战

### 4.1 本仓库导航实验（无 pom）

1. 打开 `extensions/arc/deployment/src/main/java/io/quarkus/arc/deployment/` 下任一 `*Processor.java`。  
2. 搜索 `void register(` 或 `@BuildStep`。  
3. 记录：该方法 **消费** 了哪些 `BuildItem`，**生产** 了哪些。

### 4.2 最小 `pom.xml`（学员自用扩展实验可跳过）

本章以读源码为主；若编写扩展见第 35 章。

### 4.3 构建日志分析

```bash
./mvnw -pl extensions/smallrye-health/deployment -am clean compile -DskipTests 2>&1 | tee build.log
```

在 `build.log` 中搜索 `ERROR` 与 `Build` 相关栈。

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 三人一组，各选一个 Processor，5 分钟讲清输入输出 | 白板图 |
| 2 | 故意破坏一个扩展的 `deployment` 依赖版本（讲师分支） | 观察编译失败形态 |
| 3 | 对照官方 **Configuration Reference** 标出一个 build time 属性 | 建立心智模型 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 性能与裁剪来源；可扩展。 |
| **缺点** | 学习曲线陡。 |
| **适用场景** | 平台组、深度排障。 |
| **注意事项** | 版本升级读 release note。 |
| **常见踩坑** | runtime 做 build time 的事。 |

**延伸阅读**：本仓库 `docs/src/main/asciidoc/writing-extensions.adoc`
