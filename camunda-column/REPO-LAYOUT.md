# 示例代码仓库布局与版本锁定

## 目录结构（建议）

仓库根目录：`examples/camunda-column-examples/`（与本专栏 `docs/camunda-column/chapters/` 对应）。

```
camunda-column-examples/
├── README.md                 # 总览：如何构建、统一环境
├── VERSIONS.md               # 唯一事实来源：JDK / Boot / Camunda 版本矩阵
├── pom.xml                   # 父 POM（若采用多模块）
├── part1-basic/              # 第 1～12 章相关示例（可合并为 fewer 模块）
├── part2-intermediate/       # 第 13～24 章
└── part3-advanced/           # 第 25～34 章
```

### 章与目录映射原则

- **按 Part 分模块**：降低父 POM 复杂度；每章在对应 `part` 下用子目录 `chNN-短名/`（如 `ch01-intro/`），或在单模块内用 package 区分。
- **试写章对应关系**：
  - 第 1 章：概念为主，代码可为「最小空壳」或指向第 2 章示例。
  - 第 12 章：`part1-basic/ch12-leave-request/`（请假端到端）。
  - 第 18 章：`part2-intermediate/ch18-testing/`（测试示例）。
  - 第 25 章：`part3-advanced/ch25-architecture-notes/`（以文档 + 最小代码说明边界为主）。

## 版本锁定策略

1. **单一矩阵**：所有示例在 `VERSIONS.md` 中声明一行「专栏当前基线」，子示例仅在必要时增加「变体行」（如不同数据库驱动）。
2. **升级流程**：升级 Camunda 小版本时，优先跑通 `ch02` 最小工程与 `ch12` 端到端，再批量替换依赖属性。
3. **与正文一致**：每章「环境前提」引用 `VERSIONS.md` 中的标签（如 `baseline-2026Q1`），避免正文手写版本号漂移。

## CI 建议（可选）

- 父工程：`mvn -q verify` 跑单元/集成测试。
- 使用与第 18 章一致的 Testcontainers 策略时，CI 需 Docker；本地可 profile 跳过。

## 与专栏文档的链接方式

- 章节内「获取代码」统一写：**相对路径** `examples/camunda-column-examples/...`（从仓库根算起）。
- 若专栏与代码分仓，在专栏 `README.md` 增加代码仓 URL，本章仍保留子路径约定。
