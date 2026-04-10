# Quarkus 企业培训专栏（Kubernetes 导向）

本目录为 **38 章**独立讲义，**文件名与各章 Markdown 首行标题（`# 第 N 章：…`）一致**。

每章讲义体例（已扩写为可授课版本）：

- **0. 课程卡片**：建议课时、学习目标、先修、课堂材料  
- **1. 项目背景**  
- **2. 项目设计：大师与小白的对话**（多轮、可含运维/测试/架构/安全角色）  
- **3. 知识要点**  
- **4. 项目实战**：按需含 **完整 `pom.xml` 片段、`application.properties`、Java 示例、`Dockerfile` / **多文档 Kubernetes YAML**（`---` 分隔）、`docker-compose` 等  
- **5. 课堂实验**：分步骤表格（操作 / 预期结果）、**验收标准**、**清理**  
- **6. 项目总结**  

> **版本**：文中 Quarkus Platform 示例版本（如 `3.19.2`）请按培训统一版本替换；部分属性名以当前 [Quarkus Guides](https://quarkus.io/guides/) 为准核对。

> **说明**：Windows 文件名不能包含半角 `/`。第 7、17、19、20 章标题中的「A/B」在文件名与正文标题里统一为全角分隔符 **／**（U+FF0F），与半角斜杠同形义、可合法保存。

## 目录

| 章 | 文件 |
|----|------|
| 1 | [第 1 章：为什么 Java 要上 Kubernetes 还要选 Quarkus？.md](<第 1 章：为什么 Java 要上 Kubernetes 还要选 Quarkus？.md>) |
| 2 | [第 2 章：Quarkus 扩展（Extension）与 BOM.md](<第 2 章：Quarkus 扩展（Extension）与 BOM.md>) |
| 3 | [第 3 章：配置体系与 12-factor.md](<第 3 章：配置体系与 12-factor.md>) |
| 4 | [第 4 章：第一个 REST API（Jakarta REST）.md](<第 4 章：第一个 REST API（Jakarta REST）.md>) |
| 5 | [第 5 章：依赖注入（CDI）与作用域.md](<第 5 章：依赖注入（CDI）与作用域.md>) |
| 6 | [第 6 章：日志与结构化输出.md](<第 6 章：日志与结构化输出.md>) |
| 7 | [第 7 章：健康检查（Liveness／Readiness）.md](<第 7 章：健康检查（Liveness／Readiness）.md>) |
| 8 | [第 8 章：指标（Micrometer）与 Prometheus.md](<第 8 章：指标（Micrometer）与 Prometheus.md>) |
| 9 | [第 9 章：容器镜像构建与交付.md](<第 9 章：容器镜像构建与交付.md>) |
| 10 | [第 10 章：Kubernetes 部署清单（最小集）.md](<第 10 章：Kubernetes 部署清单（最小集）.md>) |
| 11 | [第 11 章：测试分层与 @QuarkusTest.md](<第 11 章：测试分层与 @QuarkusTest.md>) |
| 12 | [第 12 章：Dev UI 与开发体验.md](<第 12 章：Dev UI 与开发体验.md>) |
| 13 | [第 13 章：REST Client（类型安全调用下游）.md](<第 13 章：REST Client（类型安全调用下游）.md>) |
| 14 | [第 14 章：Mutiny 与响应式入门.md](<第 14 章：Mutiny 与响应式入门.md>) |
| 15 | [第 15 章：Vert.x 事件循环与阻塞纪律.md](<第 15 章：Vert.x 事件循环与阻塞纪律.md>) |
| 16 | [第 16 章：Hibernate ORM 与 Panache.md](<第 16 章：Hibernate ORM 与 Panache.md>) |
| 17 | [第 17 章：数据库迁移（Flyway／Liquibase）.md](<第 17 章：数据库迁移（Flyway／Liquibase）.md>) |
| 18 | [第 18 章：缓存（Cache）.md](<第 18 章：缓存（Cache）.md>) |
| 19 | [第 19 章：消息（Kafka／响应式消息）.md](<第 19 章：消息（Kafka／响应式消息）.md>) |
| 20 | [第 20 章：安全（OIDC／JWT）.md](<第 20 章：安全（OIDC／JWT）.md>) |
| 21 | [第 21 章：OpenAPI 与 Swagger UI.md](<第 21 章：OpenAPI 与 Swagger UI.md>) |
| 22 | [第 22 章：错误模型与异常映射.md](<第 22 章：错误模型与异常映射.md>) |
| 23 | [第 23 章：定时任务（Scheduler）.md](<第 23 章：定时任务（Scheduler）.md>) |
| 24 | [第 24 章：文件上传下载与大对象.md](<第 24 章：文件上传下载与大对象.md>) |
| 25 | [第 25 章：GraalVM Native Image.md](<第 25 章：GraalVM Native Image.md>) |
| 26 | [第 26 章：构建期 Augmentation 与扩展机制.md](<第 26 章：构建期 Augmentation 与扩展机制.md>) |
| 27 | [第 27 章：Native 下的类初始化与反射.md](<第 27 章：Native 下的类初始化与反射.md>) |
| 28 | [第 28 章：Qute 模板引擎.md](<第 28 章：Qute 模板引擎.md>) |
| 29 | [第 29 章：gRPC 与 GraphQL（多协议选型）.md](<第 29 章：gRPC 与 GraphQL（多协议选型）.md>) |
| 30 | [第 30 章：OpenTelemetry 与全链路可观测.md](<第 30 章：OpenTelemetry 与全链路可观测.md>) |
| 31 | [第 31 章：测试进阶（集成、WireMock、CI 加速）.md](<第 31 章：测试进阶（集成、WireMock、CI 加速）.md>) |
| 32 | [第 32 章：Kubernetes 进阶（优雅停机、HPA、PDB）.md](<第 32 章：Kubernetes 进阶（优雅停机、HPA、PDB）.md>) |
| 33 | [第 33 章：安全基线（非 root、只读根文件系统）.md](<第 33 章：安全基线（非 root、只读根文件系统）.md>) |
| 34 | [第 34 章：模块化单体与微服务边界.md](<第 34 章：模块化单体与微服务边界.md>) |
| 35 | [第 35 章：编写最小 Quarkus 扩展.md](<第 35 章：编写最小 Quarkus 扩展.md>) |
| 36 | [第 36 章：源码阅读工作坊（HTTP 请求路径）.md](<第 36 章：源码阅读工作坊（HTTP 请求路径）.md>) |
| 37 | [第 37 章：性能工程（压测、剖析与容器误区）.md](<第 37 章：性能工程（压测、剖析与容器误区）.md>) |
| 38 | [第 38 章：落地路线图（试点到标准）.md](<第 38 章：落地路线图（试点到标准）.md>) |

官方文档入口：<https://quarkus.io/guides/>
