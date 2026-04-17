# 附录：术语表（可选）

本表收录专栏各章反复出现的 Spring Boot 与生态术语，便于新人开发与测试同事统一口径。

| 术语 | 英文 | 简要说明 |
|------|------|----------|
| 自动配置 | Auto-configuration | 基于类路径与条件注解装配 Bean，可通过 `spring.autoconfigure.exclude` 等排除 |
| 起步依赖 | Starter | 聚合传递依赖与版本对齐的 Maven/Gradle 依赖坐标 |
| Actuator | Actuator | 生产运维端点：健康、指标、环境等 |
| 健康指示器 | Health Indicator | 实现 `HealthIndicator` 或响应式变体，汇总为 `/actuator/health` |
| Micrometer | Micrometer | 指标门面，对接 Prometheus、OTLP 等后端 |
| 观察 | Observation | Micrometer Observation API，统一 metrics 与 tracing 语义 |
| 切片测试 | Slice Test | 如 `@WebMvcTest`、`@DataJpaTest` 仅加载部分上下文 |
| 自动配置导入 | AutoConfiguration.imports | Boot 3 起常用的自动配置注册清单 |

*随专栏更新可继续扩充。*
