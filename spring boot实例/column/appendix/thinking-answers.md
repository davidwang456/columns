# 附录：专栏思考题参考答案

本附录收录各章末尾「思考题」的参考答案，按章节编号与模块名索引。试点章节与批量章节的题目均可在此查阅，避免与下一章主题错位。

**终稿对照：** 各章是否满足「两道题 + 参考答案去向」的约定，见 [REVIEW_CHECKLIST.md](../REVIEW_CHECKLIST.md)。

## 使用说明

- 每章两道进阶题：题 1 偏设计与排错，题 2 偏原理与扩展。
- 若某章尚未收录答案，表示与 [column/template.md](../template.md) 同步撰写中。
- 由 [scripts/generate_chapters.py](../scripts/generate_chapters.py) 批量生成的章节，思考题答案可在定稿时逐条补入本附录对应序号。

## 索引（按章节序号）

| 章节序号 | 模块 | 题 1 答案位置 | 题 2 答案位置 |
|---------|------|---------------|---------------|
| 027 | spring-boot-data-jpa | 见 [第 027 章：spring-boot-data-jpa —— Spring Data JPA 与仓库.md](../chapters/第 027 章：spring-boot-data-jpa —— Spring Data JPA 与仓库.md) 本章总结 | 同上 |
| 054 | spring-boot-webmvc | 见 [第 054 章：spring-boot-webmvc —— Spring MVC 与 Boot.md](../chapters/第 054 章：spring-boot-webmvc —— Spring MVC 与 Boot.md) 本章总结 | 同上 |
| 112 | spring-boot-actuator | 见 [第 112 章：spring-boot-actuator —— Actuator 端点与运维 API.md](../chapters/第 112 章：spring-boot-actuator —— Actuator 端点与运维 API.md) 本章总结 | 同上 |

其余章节的参考答案在对应 `chapters/第 NNN 章：… .md` 文件末尾「参考答案」小节中维护，并在此表逐章补全链接。

---

## 027 spring-boot-data-jpa

**题 1：** 在读写分离或多数据源场景下，`@Transactional` 与 `EntityManager` 绑定关系容易踩坑。请说明如何显式指定事务管理器，并指出只读事务的合理用法。

**参考答案概要：** 通过 `@Transactional(transactionManager = "...")` 或限定 `Qualifier` 的 `PlatformTransactionManager` Bean；只读事务使用 `readOnly = true` 提示优化并避免意外写入，多数据源时为每个数据源注册独立事务管理器并在服务层明确选用。

**题 2：** `spring.jpa.open-in-view` 默认为 true 的利弊是什么？生产环境常见关闭理由？

**参考答案概要：** Open-Session-In-View 延长会话至 Web 层，懒加载在视图渲染期可用，但易导致 N+1、会话持有过久与边界模糊；生产常关闭以强制在 Service 层完成数据装配并使用 DTO/显式 fetch join。

---

## 054 spring-boot-webmvc

**题 1：** `DispatcherServlet` 与 `HandlerMapping` 的匹配顺序受哪些配置影响？自定义 `WebMvcConfigurer` 时如何避免覆盖默认转换器？

**参考答案概要：** 顺序由注册的 `HandlerMapping` Bean 顺序及 `@Order`、路径模式 specificity 等决定；扩展应 `addFormatters`/`configureMessageConverters` 等增量配置，避免替换整个列表除非有意为之。

**题 2：** 大型上传与流式响应在 MVC 下分别要注意哪些 Servlet 容器与 Spring 限制？

**参考答案概要：** 上传注意 `multipart` 阈值、临时目录与反压；流式注意异步请求、`StreamingResponseBody` 与超时、缓冲区与背压。

---

## 112 spring-boot-actuator

**题 1：** 生产环境暴露 `health`、`metrics` 时，如何与 Spring Security 协同，避免敏感端点匿名访问？

**参考答案概要：** 使用 `management.endpoints.web.base-path` 与独立 `management.server.port` 或同一端口下用 Security 规则按路径与方法限制，配合角色与 Actuator 专用用户。

**题 2：** Kubernetes 探针应优先用哪种 Actuator 端点或扩展，注意哪些响应语义？

**参考答案概要：** 常用 `health` 的 liveness/readiness 分组（Spring Boot 3.x `management.endpoint.health.probes.enabled` 等）；注意第三方依赖导致的 `DOWN` 传播与超时。

---

*（随章节发布持续追加各行模块的参考答案条目。）*
