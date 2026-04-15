# 第 2 章：十分钟跑通：Spring Boot + Camunda 最小可运行项目

## 元信息

| 项目 | 内容 |
|------|------|
| 章节编号 | 第 2 章 |
| 标题 | 十分钟跑通：Spring Boot + Camunda 最小可运行项目 |
| 难度 | 入门 |
| 预计阅读 | 25～30 分钟 |
| 受众侧重 | 开发为主 |
| 依赖章节 | 第 1 章（概念） |
| 环境版本 | `examples/camunda-column-examples/VERSIONS.md` → `baseline-2026Q1` |

---

## 1. 项目背景

你已经知道「为什么」需要流程引擎，接下来最实在的问题是：**在我自己的笔记本上，最短路径看到「引擎转起来、模型被部署、界面能点开」**。很多教程一上来堆概念，读者卡在 Maven 依赖、端口冲突或 Camunda 与 Spring Boot 版本不匹配。本章要解决的**一条主线问题**是：用 **Spring Boot 3 + Camunda Platform 7 嵌入式引擎**，在十分钟量级内搭出**可启动、可自动部署 BPMN、可打开 Web 控制台**的最小工程，并明确哪些配置仅适用于开发环境。

---

## 2. 项目设计（三角色对话）

### 2.1 小胖开球

小胖说：「我 Spring Boot 会啊，`spring-boot-starter-web` 一引就跑。Camunda 是不是再引一个 starter 就行？为啥文档里还有 Cockpit、Tasklist，我本地要全开吗？」

他的问题抓住了两点：**依赖组合**与**默认 Web 应用是否都要**。嵌入式场景下，starter 往往已经打包了引擎与常用 Web 控制台，但生产可裁剪；本地学习建议全开，减少心智负担。

### 2.2 小白追问

小白问：「第一，**自动部署**到底扫描哪个目录？我放 `resources/bpmn` 行不行？第二，H2 内存库重启后实例没了，算不算『假成功』？第三，默认用户 `demo/demo` 写在配置里，**提交到 Git** 会不会被安全扫描打回来？」

### 2.3 大师定调

大师归纳：**最小可运行**的目标是验证「类路径上的 BPMN/DMN 会被部署进引擎元数据表」，而不是一次把高可用与生产安全做完。

- **自动部署**：Camunda Spring Boot 默认从 `classpath:/META-INF/processes.xml` 或约定目录加载（具体以所用 starter 文档为准）；业界最常见做法是把 BPMN 放在 `src/main/resources/processes/` 并依赖默认扫描，或在 `application.yaml` 中显式配置 `camunda.bpm.deployment-resource-pattern`。团队应**统一目录约定**，避免「本地能跑、同事机器找不到流程」。
- **H2**：适合教程与 CI；要验证「重启后仍在」应换 file 模式或 PostgreSQL（后续章节）。入门阶段接受「进程重启实例消失」是正常现象。
- **默认账户**：仅开发 profile 使用；合并主分支前用 Spring Profile、`@ConditionalOnProperty` 或外部密钥管理替换，并在 README 写明**禁止生产默认值**。

---

## 3. 项目实战

### 3.1 环境前提

- JDK 17+，Maven 3.9+（或 Gradle，本专栏示例以 Maven 为主）。
- Camunda 7.20+ 与 Spring Boot 3.2.x，版本见 `VERSIONS.md`。
- 代码规划路径：`examples/camunda-column-examples/part1-basic/ch02-minimal-boot/`（若目录尚未创建，可按下列结构自建）。

### 3.2 步骤说明

1. **创建 Spring Boot 工程**：引入 `spring-boot-starter-web`、`camunda-bpm-spring-boot-starter`（具体 artifact 以官方 BOM 为准）。父 POM 用 `dependencyManagement` 锁定 `camunda.version` 与 `spring-boot.version`。
2. **添加最小 BPMN**：在 `src/main/resources/processes/` 新建 `hello-process.bpmn`，流程 id 如 `hello`，仅含开始—用户任务—结束（或开始—结束）以便先跑通部署。
3. **配置应用**：`server.port` 避免与本地其他服务冲突（如 8080）；`camunda.bpm.admin-user` 等仅在 `application-dev.yaml`。
4. **启动**：`mvn spring-boot:run` 或 IDE 运行主类。
5. **验证控制台**：浏览器访问 Camunda 默认上下文路径（常见为 `/camunda/app/welcome/default/` 或文档所示入口），使用配置的管理员账户登录；打开 **Cockpit** 确认流程定义已部署。

### 3.3 源码与说明

典型 `pom.xml` 片段（示意，版本用属性占位）：

```xml
<dependency>
  <groupId>org.camunda.bpm.springboot</groupId>
  <artifactId>camunda-bpm-spring-boot-starter</artifactId>
</dependency>
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-web</artifactId>
</dependency>
```

**为什么需要 Web**：嵌入式引擎可不带 Web，但教程阶段需要 Cockpit/Tasklist 可视化；`spring-boot-starter-web` 提供 Servlet 容器与 REST 能力。

`application.yaml` 示意：

```yaml
camunda.bpm:
  admin-user:
    id: demo
    password: demo
  filter:
    create: All tasks
```

**为什么单独提 admin-user**：没有账户时 Tasklist 无法登录；生产应接 SSO 或 Camunda 身份插件，而非长期用 YAML 密码。

BPMN 放置位置说明：保持团队统一；若使用 `processes.xml` 显式列举资源，更利于控制部署顺序与租户（进阶）。

### 3.4 验证

- **构建**：`mvn -q -DskipTests package` 无报错。
- **运行**：日志出现流程引擎初始化、无部署失败异常。
- **界面**：Cockpit 中可见流程定义；可手动发起一条流程实例（后续章节用 API 自动化）。
- **安全自查**：确认默认密码未进入生产配置分支。

---

## 4. 项目总结

| 维度 | 内容 |
|------|------|
| 优点 | 十分钟内建立信心；与 Spring 生态一致，便于团队已有技能复用。 |
| 缺点 / 代价 | 默认配置偏「开发友好」，直接上生产会踩安全与容量坑。 |
| 适用场景 | 本地 PoC、培训、第 12 章端到端前置。 |
| 不适用场景 | 已标准化平台团队——应直接使用组织脚手架。 |
| 注意事项 | 版本与 JDK 必须匹配；目录约定写进贡献指南。 |
| 常见踩坑 | 依赖冲突导致引擎未启动；BPMN 未进类路径；端口被占用误以为 Camunda 挂了。 |

**延伸阅读**：官方 Spring Boot 集成文档；下一章进入 BPMN 元素总览，不再纠结工程脚手架。
