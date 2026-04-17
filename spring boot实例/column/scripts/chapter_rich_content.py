# -*- coding: utf-8 -*-
"""Rich chapter bodies aligned with pilot style (027 / 054 / 112)."""

from __future__ import annotations

import textwrap


def sanitize_title_for_filename(title: str) -> str:
    """Windows 等系统禁止文件名中的 / \\ : * ? \" < > |，替换为全角或近似字符。"""
    trans = str.maketrans(
        {
            "/": "／",
            "\\": "＼",
            ":": "：",
            "*": "＊",
            "?": "？",
            '"': "＂",
            "<": "《",
            ">": "》",
            "|": "｜",
        }
    )
    return title.translate(trans)


def chapter_filename(num: int, slug: str, title_zh: str) -> str:
    """专栏章节 Markdown 文件名：`第 NNN 章：slug —— 标题.md`。"""
    safe = sanitize_title_for_filename(title_zh)
    return f"第 {num:03d} 章：{slug} —— {safe}.md"


def pilot_chapter_links_for_footer() -> str:
    """章末「试点」交叉引用（与 MODULES 中 027 / 054 / 112 一致）。"""
    p027 = chapter_filename(27, "spring-boot-data-jpa", "Spring Data JPA 与仓库")
    p054 = chapter_filename(54, "spring-boot-webmvc", "Spring MVC 与 Boot")
    p112 = chapter_filename(112, "spring-boot-actuator", "Actuator 端点与运维 API")
    return (
        "*本章结构与篇幅对齐专栏模板 [template.md](../template.md)；深度参照试点 "
        f"[{p027}]({p027})、[{p054}]({p054})、[{p112}]({p112})。*"
    )

# slug -> Maven artifact id (Spring Boot starters / special)
STARTER_BY_SLUG: dict[str, str] = {
    "spring-boot-activemq": "spring-boot-starter-activemq",
    "spring-boot-amqp": "spring-boot-starter-amqp",
    "spring-boot-artemis": "spring-boot-starter-artemis",
    "spring-boot-jms": "spring-boot-starter-jms",
    "spring-boot-kafka": "spring-boot-starter-kafka",
    "spring-boot-pulsar": "spring-boot-starter-pulsar",
    "spring-boot-integration": "spring-boot-starter-integration",
    "spring-boot-batch": "spring-boot-starter-batch",
    "spring-boot-batch-jdbc": "spring-boot-starter-batch-jdbc",
    "spring-boot-batch-data-mongodb": "spring-boot-starter-batch-data-mongodb",
    "spring-boot-quartz": "spring-boot-starter-quartz",
    "spring-boot-mongodb": "spring-boot-starter-mongodb",
    "spring-boot-cassandra": "spring-boot-starter-cassandra",
    "spring-boot-couchbase": "spring-boot-starter-couchbase",
    "spring-boot-neo4j": "spring-boot-starter-neo4j",
    "spring-boot-data-commons": "spring-boot-starter-data-jpa",
    "spring-boot-data-jdbc": "spring-boot-starter-data-jdbc",
    "spring-boot-data-jdbc-test": "spring-boot-starter-data-jdbc-test",
    "spring-boot-jdbc": "spring-boot-starter-jdbc",
    "spring-boot-jdbc-test": "spring-boot-starter-jdbc-test",
    "spring-boot-sql": "spring-boot-starter-jdbc",
    "spring-boot-r2dbc": "spring-boot-starter-r2dbc",
    "spring-boot-data-r2dbc": "spring-boot-starter-data-r2dbc",
    "spring-boot-data-r2dbc-test": "spring-boot-starter-data-r2dbc-test",
    "spring-boot-jpa": "spring-boot-starter-data-jpa",
    "spring-boot-jpa-test": "spring-boot-starter-data-jpa-test",
    "spring-boot-data-jpa-test": "spring-boot-starter-data-jpa-test",
    "spring-boot-hibernate": "spring-boot-starter-data-jpa",
    "spring-boot-persistence": "spring-boot-starter-data-jpa",
    "spring-boot-data-mongodb": "spring-boot-starter-data-mongodb",
    "spring-boot-data-mongodb-test": "spring-boot-starter-data-mongodb-test",
    "spring-boot-data-cassandra": "spring-boot-starter-data-cassandra",
    "spring-boot-data-cassandra-test": "spring-boot-starter-data-cassandra-test",
    "spring-boot-data-couchbase": "spring-boot-starter-data-couchbase",
    "spring-boot-data-couchbase-test": "spring-boot-starter-data-couchbase-test",
    "spring-boot-data-neo4j": "spring-boot-starter-data-neo4j",
    "spring-boot-data-neo4j-test": "spring-boot-starter-data-neo4j-test",
    "spring-boot-data-redis": "spring-boot-starter-data-redis",
    "spring-boot-data-redis-test": "spring-boot-starter-data-redis-test",
    "spring-boot-data-elasticsearch": "spring-boot-starter-data-elasticsearch",
    "spring-boot-data-elasticsearch-test": "spring-boot-starter-data-elasticsearch-test",
    "spring-boot-data-ldap": "spring-boot-starter-data-ldap",
    "spring-boot-data-ldap-test": "spring-boot-starter-data-ldap-test",
    "spring-boot-data-rest": "spring-boot-starter-data-rest",
    "spring-boot-elasticsearch": "spring-boot-starter-elasticsearch",
    "spring-boot-jooq": "spring-boot-starter-jooq",
    "spring-boot-jooq-test": "spring-boot-starter-jooq-test",
    "spring-boot-flyway": "spring-boot-starter-flyway",
    "spring-boot-liquibase": "spring-boot-starter-liquibase",
    "spring-boot-ldap": "spring-boot-starter-ldap",
    "spring-boot-transaction": "spring-boot-starter-jdbc",
    "spring-boot-servlet": "spring-boot-starter-web",
    "spring-boot-webmvc": "spring-boot-starter-webmvc",
    "spring-boot-webmvc-test": "spring-boot-starter-webmvc-test",
    "spring-boot-webflux": "spring-boot-starter-webflux",
    "spring-boot-webflux-test": "spring-boot-starter-webflux-test",
    "spring-boot-websocket": "spring-boot-starter-websocket",
    "spring-boot-tomcat": "spring-boot-starter-tomcat",
    "spring-boot-jetty": "spring-boot-starter-jetty",
    "spring-boot-netty": "spring-boot-starter-reactor-netty",
    "spring-boot-reactor-netty": "spring-boot-starter-reactor-netty",
    "spring-boot-reactor": "spring-boot-starter-reactor-netty",
    "spring-boot-web-server": "spring-boot-starter-web",
    "spring-boot-web-server-test": "spring-boot-starter-webmvc-test",
    "spring-boot-http-client": "spring-boot-starter-restclient",
    "spring-boot-http-codec": "spring-boot-starter-webflux",
    "spring-boot-http-converter": "spring-boot-starter-webmvc",
    "spring-boot-restclient": "spring-boot-starter-restclient",
    "spring-boot-restclient-test": "spring-boot-starter-restclient-test",
    "spring-boot-webclient": "spring-boot-starter-webclient",
    "spring-boot-webclient-test": "spring-boot-starter-webclient-test",
    "spring-boot-webtestclient": "spring-boot-starter-webflux-test",
    "spring-boot-resttestclient": "spring-boot-starter-restclient-test",
    "spring-boot-jersey": "spring-boot-starter-jersey",
    "spring-boot-graphql": "spring-boot-starter-graphql",
    "spring-boot-graphql-test": "spring-boot-starter-graphql-test",
    "spring-boot-grpc-client": "spring-boot-starter-grpc-client",
    "spring-boot-grpc-server": "spring-boot-starter-grpc-server",
    "spring-boot-grpc-test": "spring-boot-starter-grpc-test",
    "spring-boot-thymeleaf": "spring-boot-starter-thymeleaf",
    "spring-boot-freemarker": "spring-boot-starter-freemarker",
    "spring-boot-mustache": "spring-boot-starter-mustache",
    "spring-boot-groovy-templates": "spring-boot-starter-groovy-templates",
    "spring-boot-hateoas": "spring-boot-starter-hateoas",
    "spring-boot-webservices": "spring-boot-starter-webservices",
    "spring-boot-webservices-test": "spring-boot-starter-webservices-test",
    "spring-boot-restdocs": "spring-boot-starter-restdocs",
    "spring-boot-security": "spring-boot-starter-security",
    "spring-boot-security-test": "spring-boot-starter-security-test",
    "spring-boot-security-oauth2-client": "spring-boot-starter-security-oauth2-client",
    "spring-boot-security-oauth2-resource-server": "spring-boot-starter-security-oauth2-resource-server",
    "spring-boot-security-oauth2-authorization-server": "spring-boot-starter-security-oauth2-authorization-server",
    "spring-boot-security-saml2": "spring-boot-starter-security-saml2",
    "spring-boot-session": "spring-boot-starter-session-data-redis",
    "spring-boot-session-jdbc": "spring-boot-starter-session-jdbc",
    "spring-boot-session-data-redis": "spring-boot-starter-session-data-redis",
    "spring-boot-h2console": "spring-boot-starter-data-jpa",
    "spring-boot-cache": "spring-boot-starter-cache",
    "spring-boot-cache-test": "spring-boot-starter-cache-test",
    "spring-boot-hazelcast": "spring-boot-starter-hazelcast",
    "spring-boot-mail": "spring-boot-starter-mail",
    "spring-boot-sendgrid": "spring-boot-starter-sendgrid",
    "spring-boot-cloudfoundry": "spring-boot-starter-cloudfoundry",
    "spring-boot-devtools": "spring-boot-devtools",
    "spring-boot-validation": "spring-boot-starter-validation",
    "spring-boot-jackson": "spring-boot-starter-jackson",
    "spring-boot-jackson2": "spring-boot-starter-jackson",
    "spring-boot-gson": "spring-boot-starter-gson",
    "spring-boot-jsonb": "spring-boot-starter-jsonb",
    "spring-boot-kotlinx-serialization-json": "spring-boot-starter-kotlinx-serialization-json",
    "spring-boot-actuator": "spring-boot-starter-actuator",
    "spring-boot-actuator-autoconfigure": "spring-boot-starter-actuator",
    "spring-boot-health": "spring-boot-starter-actuator",
    "spring-boot-micrometer-metrics": "spring-boot-starter-micrometer-metrics",
    "spring-boot-micrometer-metrics-test": "spring-boot-starter-micrometer-metrics-test",
    "spring-boot-micrometer-observation": "spring-boot-starter-micrometer-metrics",
    "spring-boot-micrometer-tracing": "spring-boot-starter-actuator",
    "spring-boot-micrometer-tracing-brave": "spring-boot-starter-zipkin",
    "spring-boot-micrometer-tracing-opentelemetry": "spring-boot-starter-opentelemetry",
    "spring-boot-micrometer-tracing-test": "spring-boot-starter-zipkin-test",
    "spring-boot-opentelemetry": "spring-boot-starter-opentelemetry",
    "spring-boot-zipkin": "spring-boot-starter-zipkin",
    "spring-boot-rsocket": "spring-boot-starter-rsocket",
    "spring-boot-rsocket-test": "spring-boot-starter-rsocket-test",
    "spring-boot-autoconfigure-classic": "spring-boot-starter-classic",
    "spring-boot-autoconfigure-classic-modules": "spring-boot-starter-classic",
    "spring-boot-test-classic-modules": "spring-boot-starter-test-classic",
}


def infer_domain(slug: str) -> str:
    if any(
        x in slug
        for x in (
            "activemq",
            "amqp",
            "artemis",
            "jms",
            "kafka",
            "pulsar",
            "integration",
        )
    ):
        return "messaging"
    if "batch" in slug or "quartz" in slug:
        return "batch"
    if any(
        x in slug
        for x in (
            "data-",
            "jdbc",
            "jpa",
            "r2dbc",
            "mongodb",
            "cassandra",
            "redis",
            "elasticsearch",
            "neo4j",
            "couchbase",
            "ldap",
            "flyway",
            "liquibase",
            "jooq",
            "sql",
            "persistence",
            "hibernate",
            "transaction",
        )
    ):
        return "data"
    if any(
        x in slug
        for x in (
            "webmvc",
            "webflux",
            "servlet",
            "websocket",
            "tomcat",
            "jetty",
            "netty",
            "reactor",
            "http-",
            "restclient",
            "webclient",
            "webtestclient",
            "resttestclient",
            "jersey",
            "graphql",
            "grpc",
            "web-server",
            "thymeleaf",
            "freemarker",
            "mustache",
            "groovy-templates",
            "hateoas",
            "webservices",
            "restdocs",
            "rsocket",
        )
    ):
        return "web"
    if "security" in slug or "session" in slug or "oauth" in slug or "saml" in slug:
        return "security"
    if any(
        x in slug
        for x in (
            "actuator",
            "health",
            "micrometer",
            "opentelemetry",
            "zipkin",
        )
    ):
        return "observability"
    if "cache" in slug or "hazelcast" in slug:
        return "cache"
    if any(x in slug for x in ("jackson", "gson", "jsonb", "kotlinx-serialization", "validation")):
        return "foundation"
    if "test" in slug or "classic" in slug:
        return "testing"
    if any(x in slug for x in ("mail", "sendgrid", "cloudfoundry", "devtools")):
        return "ops"
    return "general"


def background_paragraphs(slug: str, title_zh: str, is_test: bool) -> tuple[str, str, str]:
    """Returns (blockquote_hint, para1, para2)."""
    d = infer_domain(slug)
    if is_test:
        hint = (
            f"本章定位为**测试基础设施与切片实践**：与「{title_zh}」配套的主线模块章节共用业务故事，"
            "此处侧重 `@…Test`、Mock、Testcontainers 与 CI 中的稳定复现。"
        )
        p1 = (
            f"团队在联调「{title_zh}」相关能力时，最怕**本地能通过、CI 偶发失败**：数据库版本漂移、"
            "嵌入式中间件与生产不一致、切片上下文漏载 Security/Web 配置。`{slug}` 模块把测试侧自动配置与"
            "可选依赖收敛到可重复的组合里。"
        )
        p2 = (
            "若没有专用测试模块与约定，测试代码会复制粘贴 `@Import` 与 `MockBean`，维护成本逼近业务代码。"
            f"结合本仓库 `module/{slug}` 的 `build.gradle`，可以把**最小失败用例**固定成团队模板。"
        )
        return hint, p1, p2

    scenarios = {
        "messaging": (
            f"某零售企业的「订单状态机」与「库存回冲」依赖可靠异步链路：峰值时每秒数万条事件需要在应用与中间件间解耦。"
            f"业务方希望以 `{title_zh}` 对齐团队对消息语义（至少一次、有序、死信）的共同理解。",
            f"若完全手写 ConnectionFactory、Listener 与重试策略，**联调周期长**且**监控缺位**（无法统一健康检查与指标）。"
            f"引入 `spring-boot` 体系中的 `{slug}` 后，可把精力投入到领域事件建模与幂等设计。",
        ),
        "batch": (
            "财务结算与对账任务需要在窗口期内批量处理千万级明细：失败要可重跑、步骤要可观测、断点续跑要可靠。",
            f"`{title_zh}` 与 Spring Batch / 调度生态结合时，Boot 的自动配置能把 JobRepository、DataSource 与事务边界对齐。",
        ),
        "data": (
            f"中台服务要同时支撑报表、交易与搜索：数据访问层若各自为政，会出现**连接池风暴**、**事务穿透**与**方言泄漏**。"
            f"`{title_zh}` 所覆盖的能力，是多数业务应用的「主航道」。",
            f"手写 `DataSource`、模板客户端与事务模板，短期可行但**演进痛苦**：升级 ORM 或驱动时全员踩坑。"
            f"使用 `{slug}` 可把配置收敛到 `spring.*` 命名空间并与其它模块协同。",
        ),
        "web": (
            f"对外 API 与 BFF 需要统一的错误模型、超时、编解码与测试工具链；网关与 Sidecar 还关心线程模型与背压。"
            f"`{title_zh}` 通常处在请求路径的核心。",
            f"脱离 Boot 的默认装配，团队容易在 **Filter 顺序**、**消息转换器**与**客户端超时**上重复造轮子。"
            f"`{slug}` 提供与 Spring 生态一致的默认行为与覆盖点。",
        ),
        "security": (
            "零信任架构下，服务既要对接企业 IdP（SAML/OIDC），又要保护资源服务器与后台会话；配置错误即全线风险。",
            f"`{title_zh}` 相关的安全能力必须与 Spring Security 过滤器链、会话存储与 Boot 属性一致。",
        ),
        "observability": (
            "SRE 要求黄金信号（延迟、流量、错误、饱和度）与追踪关联日志；K8s 探针要可读且不误判。",
            f"`{title_zh}` 是把运维需求产品化的关键拼图：端点、指标、追踪需统一导出路径。",
        ),
        "cache": (
            "热点数据若每次都打穿数据库，核心交易链路会被拖垮；缓存一致性又容易引发「幽灵库存」类问题。",
            f"`{title_zh}` 需要在序列化、TTL、集群同步与监控之间取得平衡。",
        ),
        "foundation": (
            "前后端与微服务之间靠 JSON 契约协作：字段演进、时区、未知属性处理任一环节失误都会在生产放大。",
            f"`{title_zh}` 决定序列化与校验的默认策略与扩展点。",
        ),
        "testing": (
            "架构演进时，**测试分层**若混乱，会出现「集成测过慢、单元测假绿」。经典模块迁移还要兼容旧测试基座。",
            f"`{title_zh}` 帮助团队在切片与全量上下文之间做出显式选择。",
        ),
        "ops": (
            "交付链路涉及邮件通知、云平台元数据与开发体验（热重载）；任何一环不可靠都会拖慢迭代。",
            f"`{title_zh}` 通常与运维告警、营销触达或平台绑定配置相关。",
        ),
        "general": (
            f"企业应用需要在一致的技术栈上交付可复制的微服务；`{title_zh}` 对应的能力往往是横切关注点。",
            "缺少统一自动配置时，各服务「各写各的」会导致线上排障与升级不可控。",
        ),
    }
    p1, p2 = scenarios.get(d, scenarios["general"])
    hint = (
        f"本章在**基础**层面讲清 `{title_zh}` 的起步依赖与关键属性；**中级**讨论多环境、容量与可观测性衔接；"
        "**高级**指向条件装配、源码中的 `*Properties` 与自定义扩展。"
    )
    return hint, p1, p2


def mermaid_for_domain(d: str, slug: str) -> str:
    if d == "messaging":
        return textwrap.dedent(
            f"""
            ```mermaid
            flowchart LR
              app[应用服务] --> mod["{slug}"]
              mod --> broker[消息中间件]
              broker --> consumer[消费者]
            ```
            """
        ).strip()
    if d == "data":
        return textwrap.dedent(
            f"""
            ```mermaid
            flowchart LR
              svc[Service] --> repo[Repository或客户端]
              repo --> mod["{slug}"]
              mod --> store[(存储)]
            ```
            """
        ).strip()
    if d == "observability":
        return textwrap.dedent(
            """
            ```mermaid
            flowchart LR
              app[应用] --> act[Actuator端点]
              act --> prom[Prometheus或OTLP]
            ```
            """
        ).strip()
    return textwrap.dedent(
        f"""
        ```mermaid
        flowchart LR
          boot[SpringBoot应用] --> mod["{slug}"]
          mod --> ext[外部依赖或运行时]
        ```
        """
    ).strip()


def dialogue_section(title_zh: str, slug: str, is_test: bool) -> str:
    d = infer_domain(slug)
    if is_test:
        return textwrap.dedent(
            f"""
            **场景：** 测试架构周会，议题是「如何把 `{title_zh}` 测得又快又真」。

            **小胖：** 不就写个 `@Test` 吗？为啥还要单独一个模块？跟多打一份外卖有啥区别？

            **大师：** 单元测试像「试吃一口」；切片测试像「同一厨房出餐流程」——只启动 MVC 或 JPA 子上下文，速度才上得来。  
            **技术映射：** `{slug}` 提供测试侧自动配置与 Testcontainers 可选集成。

            **小白：** 那如果我要测 Security 呢？只开 `@WebMvcTest` 会不会缺过滤器链？

            **大师：** 用 `@Import` / `@AutoConfigureMockMvc(addFilters = …)` 或改用 `@SpringBootTest` 分层；关键是**声明你依赖的切片**。  
            **技术映射：** 测试上下文是**显式契约**，不是全家桶。

            **小胖：** CI 里 Docker 起不来咋办？

            **大师：** 用 Testcontainers 的 Ryuk 与重用策略，或降级嵌入式（H2/embedded-kafka 等）并标明**环境差异风险**。  
            **技术映射：** 测试金字塔底座要**可移植**。

            **小白：** 和上一章业务代码重复叙事怎么办？

            **大师：** 业务故事共用一条线，本章只换镜头：**Given/When/Then** 与失败注入。  
            **技术映射：** 文档结构遵循专栏计划——主模块讲能力，`*-test` 讲验证。
            """
        ).strip()

    metaphors = {
        "messaging": ("快递分拣中心", "包裹是否按路由码投递", "Kafka/AMQP 主题与消费组"),
        "batch": ("夜班流水线", "哪道工序卡住要亮红灯", "Step 与 Job 状态机"),
        "data": ("仓库货架与拣货单", "拿错批次谁负责", "事务边界与隔离级别"),
        "web": ("餐厅前台与后厨出餐口", "点单和传菜能不能串", "Dispatcher 与 Handler"),
        "security": ("大楼门禁与访客登记", "临时卡过期怎么办", "过滤器链与 Token 校验"),
        "observability": ("体检中心仪表盘", "哪些指标异常要转诊", "Health 分组与指标标签"),
        "cache": ("便利店冰柜", "过期酸奶谁背锅", "TTL 与驱逐策略"),
        "foundation": ("海关报关单格式", "字段对不上谁负责", "Schema 与校验注解"),
        "testing": ("彩排与正式演出", "彩排缺灯光算不算数", "切片与全量上下文"),
        "ops": ("物业与快递柜", "短信通知失败谁重试", "外部 SaaS 配额与重试"),
        "general": ("城市水电煤接口", "断供时谁先恢复", "默认配置与降级路径"),
    }
    place, risk, _ = metaphors.get(d, metaphors["general"])

    return textwrap.dedent(
        f"""
        **场景：** 架构评审室，白板标题为「{title_zh}」。

        **小胖：** 这不就跟{place}一样吗？为啥不能我自己接根线完事？

        **大师：** 业务要的是**可复制的正确默认**：连接、超时、序列化、指标，一旦散落各处，线上就是拼图游戏。  
        **技术映射：** `{slug}` 把横切关注点收敛到 `*AutoConfiguration` 与 `*Properties`。

        **小白：** {risk}？如果队头阻塞或下游抖动，我们怎么降级？

        **大师：** 先分清**同步还是异步**、**强一致还是最终一致**；再选熔断、隔离、重试与幂等键。Boot 不负责替你选模式，但让集成成本可控。  
        **技术映射：** Resilience4j/Micrometer 等与 `{title_zh}` 常一起出现。

        **小胖：** 升级 Boot 大版本我最怕「默认变了」。

        **大师：** 看发行说明 + `spring-boot-properties-migrator`（迁移期）+ 预发对比指标；必要时显式写出曾依赖的隐式默认。  
        **技术映射：** **配置显式化**是长期可维护性的关键。

        **小白：** 和同类技术相比，我们何时不该用 `{title_zh}`？

        **大师：** 当组织已有成熟平台 SDK 且与 Spring 生命周期冲突、或资源极度受限时，要评估**引入成本**与**退出策略**。  
        **技术映射：** 技术选型 = **约束优化**，不是功能清单。

        **技术映射（小结）：** `{title_zh}` 对应的自动配置类由 `{slug}` 提供，入口注册见 `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`（以当前 Boot 版本为准）。
        """
    ).strip()


def yaml_snippet(slug: str, domain: str, is_test: bool) -> str:
    short = slug.replace("spring-boot-", "")
    base = f"""spring:
  application:
    name: demo-{short}
"""
    if is_test:
        return (
            base
            + """# 测试常用：随机端口、简化日志
server:
  port: 0
logging:
  level:
    org.springframework: INFO
"""
        )
    if domain == "messaging":
        return base + f"""# 示例：按模块补充 broker、并发与消费者组；以 {slug} 与官方文档为准
# （Kafka 示例）# spring.kafka.bootstrap-servers: localhost:9092
# （JMS 示例）# spring.activemq.broker-url: tcp://localhost:61616
"""
    if domain == "data":
        return (
            base.rstrip()
            + """
  datasource:
    url: jdbc:h2:mem:demo;DB_CLOSE_DELAY=-1
    driver-class-name: org.h2.Driver
"""
        )
    if domain == "observability":
        return (
            base.rstrip()
            + """
management:
  endpoints:
    web:
      exposure:
        include: health,info,metrics
  endpoint:
    health:
      show-details: when_authorized
"""
        )
    if domain == "security":
        return base + """# spring.security.* 按组织 IdP 与资源服务器策略填写
"""
    return base


def practice_java_step2(slug: str, domain: str) -> str:
    if domain == "messaging":
        return """```java\n// 示例：消息监听骨架（接口因 JMS/Kafka 而异）\n// @KafkaListener(topics = \"orders\") 或 @JmsListener(destination = \"queue.orders\")\n// public void onMessage(String payload) { ... }\n```"""
    if domain == "data":
        return """```java\n// 示例：声明 Spring Data 仓库或 JdbcTemplate Bean 注入点\n// public interface OrderRepository extends JpaRepository<Order, Long> {}\n```"""
    if domain == "web":
        return """```java\n// 示例：REST 入口（按模块替换为 RouterFunction / Controller）\n// @RestController @RequestMapping(\"/api\") class DemoController {}\n```"""
    if domain == "observability":
        return """```java\n// 示例：自定义 HealthIndicator\n// @Component class DemoHealth implements HealthIndicator { ... }\n```"""
    if domain == "security":
        return """```java\n// 示例：SecurityFilterChain Bean（Spring Security 6 风格）\n// @Bean SecurityFilterChain chain(HttpSecurity http) throws Exception { ... return http.build(); }\n```"""
    return """```java\n// 按业务注入模块提供的关键 Bean；参阅 module 源码中的 *AutoConfiguration\n```"""


def summary_table_rows(slug: str, domain: str) -> str:
    return f"""| 优点 | 缺点 |
|------|------|
| 与 Spring Boot BOM 对齐，降低依赖地狱 | 默认值未必覆盖极端吞吐或合规要求 |
| 自动配置缩短从 0 到可运行的时间 | 需要团队规范 exclude/覆盖策略 |
| 与 Actuator/Micrometer/Security 等同栈协同 | 大版本升级需阅读发行说明与迁移工具 |"""


def pitfalls_for(domain: str) -> str:
    return f"""1. **配置写了不生效：** relaxed binding 与 `{{prefix}}` 层级写错——根因是属性元数据与 YAML 结构不一致。
2. **本地与 CI 行为不一致：** 环境变量/Profile 未对齐——根因是配置来源未收口到配置中心或 `.env` 约定。
3. **与其它自动配置冲突：** 两个 `DataSource` 或两套 Web 栈并存——根因是条件装配边界未用 `@ConditionalOn*` 或 Profile 切开。"""


def thinking_questions(title_zh: str, slug: str) -> str:
    return f"""1. 若要在不修改 `{slug}` 自带 `AutoConfiguration` 的情况下替换其中一个 Bean，你会用 `@Bean @Primary`、`@Qualifier` 还是 `BeanDefinitionRegistryPostProcessor`？各自适用什么顺序风险？
2. 与「{title_zh}」最相关的生产指标与健康检查项是什么？如何在预发用 Actuator 与 Micrometer 验证？"""


def render_rich_chapter(num: int, slug: str, title_zh: str) -> str:
    nn = f"{num:03d}"
    demo_name = slug.replace("spring-boot-", "")
    is_test = slug.endswith("-test") or "test-classic" in slug
    hint, p1, p2 = background_paragraphs(slug, title_zh, is_test)
    d = infer_domain(slug)
    starter = STARTER_BY_SLUG.get(slug, f"spring-boot-starter-{demo_name}")
    if starter == "spring-boot-devtools":
        maven_extra = textwrap.dedent(
            """
            ```xml
            <dependency>
              <groupId>org.springframework.boot</groupId>
              <artifactId>spring-boot-devtools</artifactId>
              <scope>runtime</scope>
              <optional>true</optional>
            </dependency>
            ```
            """
        ).strip()
    else:
        maven_extra = textwrap.dedent(
            f"""
            ```xml
            <dependency>
              <groupId>org.springframework.boot</groupId>
              <artifactId>{starter}</artifactId>
            </dependency>
            ```
            """
        ).strip()

    yml = yaml_snippet(slug, d, is_test)
    dialogue = dialogue_section(title_zh, slug, is_test)
    mermaid = mermaid_for_domain(d, slug)

    extra_starter_note = ""
    if slug == "spring-boot-h2console":
        extra_starter_note = (
            "\n> 提示：H2 控制台通常与 `spring-boot-starter-data-jpa` + `com.h2database:h2` 一起使用；生产务必关闭或加固。\n"
        )

    parts: list[str] = [
        f"# 第 {nn} 章：{slug} —— {title_zh}",
        "",
        f"> 对应模块：`{slug}`。{hint}",
    ]
    if extra_starter_note:
        parts.append(extra_starter_note.rstrip())
    parts.extend(
        [
        "",
        "---",
        "",
        "## 1 项目背景",
        "",
        p1,
        "",
        p2,
        "",
        mermaid,
        "",
        f"**小结：** 没有 `spring-boot` 对该能力的自动装配与属性命名空间时，团队要在**依赖对齐**、**Bean 生命周期**与**运维接口**上重复投入；引入 `{slug}` 后，可以把讨论焦点收束到业务约束与 SLA。",
        "",
        "---",
        "",
        "## 2 项目设计（剧本式交锋对话）",
        "",
        dialogue,
        "",
        "---",
        "",
        "## 3 项目实战",
        "",
        "### 环境准备",
        "",
        "- JDK 17+，Spring Boot 3.x（与当前仓库 `spring-boot-main` 版本族一致）。",
        "- 构建：Maven 或 Gradle；依赖以官方 **Starter** 或模块文档为准，源码对照 `spring-boot-main/module/"
        + slug
        + "/build.gradle`。",
        "",
        "**Maven 依赖示例：**",
        "",
        maven_extra,
        "",
        "**`application.yml` 片段：**",
        "",
        "```yaml",
        yml.strip(),
        "```",
        "",
        "### 分步实现",
        "",
        "**步骤 1 — 目标：** 创建可启动应用骨架。",
        "",
        "```java",
        "import org.springframework.boot.SpringApplication;",
        "import org.springframework.boot.autoconfigure.SpringBootApplication;",
        "",
        "@SpringBootApplication",
        "public class DemoApplication {",
        "  public static void main(String[] args) {",
        "    SpringApplication.run(DemoApplication.class, args);",
        "  }",
        "}",
        "```",
        "",
        "**运行结果（文字描述）：** 控制台出现 Spring Boot Banner，`Started DemoApplication` 表示上下文就绪；若缺中间件或配置，异常信息应指向具体 `*Properties`。",
        "",
        "**可能遇到的坑：** 类路径上同时存在互斥实现（例如两个 Web 引擎）导致条件装配失败——使用 `spring.autoconfigure.exclude` 精确排除。",
        "",
        "---",
        "",
        "**步骤 2 — 目标：** 编写与「" + title_zh + "」相关的最小业务或基础设施代码。",
        "",
        practice_java_step2(slug, d),
        "",
        "**可能遇到的坑：** Profile 未激活、或测试与主应用包扫描路径不一致导致 Bean 未注册。",
        "",
        "---",
        "",
        "**步骤 3 — 目标：** 验证（HTTP / 消息 / 批任务视领域选择）。",
        "",
        "```bash",
        "# Web/运维类：健康检查",
        "curl -s http://localhost:8080/actuator/health",
        "",
        "# 或执行测试",
        "./mvnw -q test",
        "```",
        "",
        "### 完整代码清单",
        "",
        f"建议新建示例工程 `demo-{demo_name}`，附 `README` 说明外部依赖（Docker Compose / 本地中间件）。**仓库占位：** `https://example.com/demo/{slug}.git`",
        "",
        "### 测试验证",
        "",
        "- **切片测试：** 按领域选用 `@WebMvcTest`、`@DataJpaTest`、`@JsonTest` 等；`*-test` 模块章节优先对齐本仓库测试基类。",
        "- **集成测试：** `@SpringBootTest` + Testcontainers（模块已可选依赖时常用）。",
        "",
        "---",
        "",
        "## 4 项目总结",
        "",
        "### 优点与缺点",
        "",
        summary_table_rows(slug, d),
        "",
        "### 适用场景与不适用场景",
        "",
        "- **适用：** 需要与 Spring 生态一致默认、并希望缩短联调周期的服务；已有 Spring Boot 基线的团队。",
        "- **不适用：** 目标运行环境禁止引入相关依赖；或已有非 Spring 技术栈且迁移成本高于收益。",
        "",
        "### 注意事项",
        "",
        f"- 对照 `spring-boot-main/module/{slug}/README.adoc`（若存在）与 `*Properties`。",
        "- 生产环境避免开启调试级日志；密钥走密钥管理而非 YAML 明文。",
        "",
        "### 常见踩坑经验（根因分析）",
        "",
        pitfalls_for(d),
        "",
        "### 思考题",
        "",
        thinking_questions(title_zh, slug),
        "",
        f"**参考答案：** 见 [附录：思考题参考答案](../appendix/thinking-answers.md)（可按序号 `{nn}` 维护）。",
        "",
        "### 推广计划提示",
        "",
        "- **开发：** 与架构组约定本模块的覆盖/排除策略；Code Review 检查是否误用默认线程池与超时。",
        "- **测试：** 固化切片模板；对 `*-test` 模块与主模块章节交叉评审，避免重复长文。",
        "- **运维：** 将 `management.*`、`spring.*` 关键项纳入配置审计与告警基线。",
        "",
        "---",
        "",
        pilot_chapter_links_for_footer(),
        ]
    )
    return "\n".join(parts).strip() + "\n"
