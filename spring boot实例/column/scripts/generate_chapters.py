#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate column chapter markdown files for all modules except pilot chapters."""

from __future__ import annotations

from pathlib import Path

from chapter_rich_content import chapter_filename, render_rich_chapter

ROOT = Path(__file__).resolve().parents[1]
CHAPTERS = ROOT / "chapters"
PILOT = {27, 54, 112}  # 027, 054, 112

# (num, slug, title_zh) — aligned with column/INDEX.md
MODULES: list[tuple[int, str, str]] = [
    (1, "spring-boot-activemq", "ActiveMQ 与 JMS 客户端自动配置"),
    (2, "spring-boot-amqp", "Spring AMQP（RabbitMQ）集成"),
    (3, "spring-boot-artemis", "Artemis 嵌入式与 JMS 自动配置"),
    (4, "spring-boot-jms", "通用 JMS 抽象与 Boot 起步"),
    (5, "spring-boot-kafka", "Kafka 生产/消费与 Boot 配置"),
    (6, "spring-boot-pulsar", "Apache Pulsar 集成"),
    (7, "spring-boot-integration", "Spring Integration 与 Boot"),
    (8, "spring-boot-batch", "Spring Batch 自动配置与作业启动"),
    (9, "spring-boot-batch-jdbc", "Batch 与 JDBC 模式初始化"),
    (10, "spring-boot-batch-data-mongodb", "Batch 与 MongoDB 存储"),
    (11, "spring-boot-quartz", "Quartz 调度与 Spring 集成"),
    (12, "spring-boot-mongodb", "MongoDB 客户端（驱动层）与 Boot"),
    (13, "spring-boot-cassandra", "Cassandra 驱动与 Boot"),
    (14, "spring-boot-couchbase", "Couchbase 客户端与 Boot"),
    (15, "spring-boot-neo4j", "Neo4j 驱动与 Boot"),
    (16, "spring-boot-data-commons", "Spring Data 公共抽象与 Boot"),
    (17, "spring-boot-data-jdbc", "Spring Data JDBC"),
    (18, "spring-boot-data-jdbc-test", "Data JDBC 测试支持与实践"),
    (19, "spring-boot-jdbc", "JDBC 核心与数据源自动配置"),
    (20, "spring-boot-jdbc-test", "JDBC 测试切片与 Testcontainers 思路"),
    (21, "spring-boot-sql", "SQL 初始化与脚本执行"),
    (22, "spring-boot-r2dbc", "R2DBC 响应式数据库访问"),
    (23, "spring-boot-data-r2dbc", "Spring Data R2DBC"),
    (24, "spring-boot-data-r2dbc-test", "Data R2DBC 测试"),
    (25, "spring-boot-jpa", "JPA/Hibernate 起步（非 Data JPA）"),
    (26, "spring-boot-jpa-test", "JPA 测试支持"),
    (27, "spring-boot-data-jpa", "Spring Data JPA 与仓库"),
    (28, "spring-boot-data-jpa-test", "Data JPA 测试"),
    (29, "spring-boot-hibernate", "Hibernate 专属配置与 Boot"),
    (30, "spring-boot-persistence", "持久化上下文与 JPA 共性"),
    (31, "spring-boot-data-mongodb", "Spring Data MongoDB"),
    (32, "spring-boot-data-mongodb-test", "Data MongoDB 测试"),
    (33, "spring-boot-data-cassandra", "Spring Data Cassandra"),
    (34, "spring-boot-data-cassandra-test", "Data Cassandra 测试"),
    (35, "spring-boot-data-couchbase", "Spring Data Couchbase"),
    (36, "spring-boot-data-couchbase-test", "Data Couchbase 测试"),
    (37, "spring-boot-data-neo4j", "Spring Data Neo4j"),
    (38, "spring-boot-data-neo4j-test", "Data Neo4j 测试"),
    (39, "spring-boot-data-redis", "Spring Data Redis"),
    (40, "spring-boot-data-redis-test", "Data Redis 测试"),
    (41, "spring-boot-data-elasticsearch", "Spring Data Elasticsearch"),
    (42, "spring-boot-data-elasticsearch-test", "Data Elasticsearch 测试"),
    (43, "spring-boot-data-ldap", "Spring Data LDAP"),
    (44, "spring-boot-data-ldap-test", "Data LDAP 测试"),
    (45, "spring-boot-data-rest", "Spring Data REST 与超媒体 API"),
    (46, "spring-boot-elasticsearch", "Elasticsearch 高层客户端与 Boot"),
    (47, "spring-boot-jooq", "jOOQ 与 SQL 构建"),
    (48, "spring-boot-jooq-test", "jOOQ 测试"),
    (49, "spring-boot-flyway", "Flyway 数据库迁移"),
    (50, "spring-boot-liquibase", "Liquibase 迁移"),
    (51, "spring-boot-ldap", "LDAP 客户端与 Boot"),
    (52, "spring-boot-transaction", "声明式事务与事务管理器"),
    (53, "spring-boot-servlet", "Servlet 栈与 Filter 注册"),
    (54, "spring-boot-webmvc", "Spring MVC 与 Boot"),
    (55, "spring-boot-webmvc-test", "Web MVC 测试（MockMvc 等）"),
    (56, "spring-boot-webflux", "WebFlux 响应式 Web"),
    (57, "spring-boot-webflux-test", "WebFlux 测试"),
    (58, "spring-boot-websocket", "WebSocket 消息端点"),
    (59, "spring-boot-tomcat", "嵌入式 Tomcat 调优与配置"),
    (60, "spring-boot-jetty", "Jetty 作为嵌入式容器"),
    (61, "spring-boot-netty", "Netty 与 Boot 集成点"),
    (62, "spring-boot-reactor-netty", "Reactor Netty 服务器/客户端"),
    (63, "spring-boot-reactor", "Reactor 核心与 Boot 协程"),
    (64, "spring-boot-web-server", "通用 Web 服务器抽象"),
    (65, "spring-boot-web-server-test", "Web 服务器层测试"),
    (66, "spring-boot-http-client", "HTTP 客户端自动配置"),
    (67, "spring-boot-http-codec", "HTTP Codec 与编解码"),
    (68, "spring-boot-http-converter", "HttpMessageConverter 体系"),
    (69, "spring-boot-restclient", "RestClient（同步 HTTP）"),
    (70, "spring-boot-restclient-test", "RestClient 测试"),
    (71, "spring-boot-webclient", "WebClient（响应式 HTTP）"),
    (72, "spring-boot-webclient-test", "WebClient 测试"),
    (73, "spring-boot-webtestclient", "WebTestClient 统一测试"),
    (74, "spring-boot-resttestclient", "REST 测试客户端辅助"),
    (75, "spring-boot-jersey", "JAX-RS / Jersey 与 Boot"),
    (76, "spring-boot-graphql", "GraphQL 与 Spring for GraphQL"),
    (77, "spring-boot-graphql-test", "GraphQL 测试"),
    (78, "spring-boot-grpc-client", "gRPC 客户端"),
    (79, "spring-boot-grpc-server", "gRPC 服务端"),
    (80, "spring-boot-grpc-test", "gRPC 测试"),
    (81, "spring-boot-thymeleaf", "Thymeleaf 模板"),
    (82, "spring-boot-freemarker", "FreeMarker"),
    (83, "spring-boot-mustache", "Mustache"),
    (84, "spring-boot-groovy-templates", "Groovy 模板"),
    (85, "spring-boot-hateoas", "Spring HATEOAS 超媒体"),
    (86, "spring-boot-webservices", "Spring Web Services（SOAP）"),
    (87, "spring-boot-webservices-test", "Web Services 测试"),
    (88, "spring-boot-restdocs", "Spring REST Docs 文档化测试"),
    (89, "spring-boot-security", "Spring Security 起步"),
    (90, "spring-boot-security-test", "Security 测试"),
    (91, "spring-boot-security-oauth2-client", "OAuth2 客户端"),
    (92, "spring-boot-security-oauth2-resource-server", "OAuth2 资源服务器"),
    (93, "spring-boot-security-oauth2-authorization-server", "OAuth2 授权服务器"),
    (94, "spring-boot-security-saml2", "SAML2 与 SSO"),
    (95, "spring-boot-session", "Spring Session 核心"),
    (96, "spring-boot-session-jdbc", "Session 存 JDBC"),
    (97, "spring-boot-session-data-redis", "Session 存 Redis"),
    (98, "spring-boot-h2console", "H2 控制台与安全边界"),
    (99, "spring-boot-cache", "缓存抽象与 Caffeine 等"),
    (100, "spring-boot-cache-test", "缓存测试"),
    (101, "spring-boot-hazelcast", "Hazelcast 集成"),
    (102, "spring-boot-mail", "JavaMail 与邮件发送"),
    (103, "spring-boot-sendgrid", "SendGrid 邮件"),
    (104, "spring-boot-cloudfoundry", "Cloud Foundry Actuator 扩展"),
    (105, "spring-boot-devtools", "开发时热重载与 LiveReload"),
    (106, "spring-boot-validation", "Bean Validation 与校验"),
    (107, "spring-boot-jackson", "Jackson 自动配置"),
    (108, "spring-boot-jackson2", "Jackson 2 兼容路径"),
    (109, "spring-boot-gson", "Gson"),
    (110, "spring-boot-jsonb", "JSON-B（Yasson 等）"),
    (111, "spring-boot-kotlinx-serialization-json", "Kotlinx Serialization"),
    (112, "spring-boot-actuator", "Actuator 端点与运维 API"),
    (113, "spring-boot-actuator-autoconfigure", "Actuator 自动配置深入"),
    (114, "spring-boot-health", "Health 指标与贡献者"),
    (115, "spring-boot-micrometer-metrics", "Micrometer 指标注册"),
    (116, "spring-boot-micrometer-metrics-test", "Metrics 测试"),
    (117, "spring-boot-micrometer-observation", "Observation API 统一观测"),
    (118, "spring-boot-micrometer-tracing", "分布式追踪抽象"),
    (119, "spring-boot-micrometer-tracing-brave", "Brave/Zipkin 桥接"),
    (120, "spring-boot-micrometer-tracing-opentelemetry", "OTel 追踪桥接"),
    (121, "spring-boot-micrometer-tracing-test", "Tracing 测试"),
    (122, "spring-boot-opentelemetry", "OpenTelemetry SDK 集成"),
    (123, "spring-boot-zipkin", "Zipkin 上报与配置"),
    (124, "spring-boot-rsocket", "RSocket 协议与 Boot"),
    (125, "spring-boot-rsocket-test", "RSocket 测试"),
    (126, "spring-boot-autoconfigure-classic", "经典自动配置迁移与兼容"),
    (127, "spring-boot-autoconfigure-classic-modules", "经典自动配置模块集"),
    (128, "spring-boot-test-classic-modules", "经典测试模块集与组合切片"),
]


def main() -> None:
    CHAPTERS.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0
    for num, slug, title_zh in MODULES:
        if num in PILOT:
            skipped += 1
            continue
        path = CHAPTERS / chapter_filename(num, slug, title_zh)
        path.write_text(render_rich_chapter(num, slug, title_zh), encoding="utf-8")
        written += 1
    print(f"Written: {written}, skipped (pilots): {skipped}, root: {CHAPTERS}")


if __name__ == "__main__":
    main()
