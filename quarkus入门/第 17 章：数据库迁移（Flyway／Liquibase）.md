# 第 17 章：数据库迁移（Flyway／Liquibase）

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 50～60 分钟 |
| **学习目标** | 使用 Flyway 管理版本化脚本；与 K8s 多实例启动协调 |
| **先修** | 第 16 章 |

---

## 1. 项目背景

Schema 变更必须 **可重复、可审计**。Flyway／Liquibase 在应用启动时迁移是常见模式，但要警惕：**多 Pod 并行**时的锁、**长时间迁移**导致 readiness 失败。

---

## 2. 项目设计：大师与小白的对话

**小白**：「我连上库手动执行 DDL。」

**大师**：「不可追溯、不可 CI。迁移脚本必须进 Git。」

**运维**：「回滚发布但迁移已向前？」

**大师**：「需要 **expand-contract**：应用向后兼容两步走。」

**测试**：「迁移如何在 CI 验证？」

**大师**：「空库起服务 + 集成测试；或对脚本做 **dry-run**（工具支持时）。」

**架构师**：「大表加列如何避免锁表？」

**大师**：「在线 DDL 策略、分批回填——超出入门，标记为专题。」

---

## 3. 知识要点

- `src/main/resources/db/migration/V*.sql`  
- `quarkus.flyway.migrate-at-start`  
- 生产关闭 `drop-and-create`

---

## 4. 项目实战

### 4.1 `pom.xml`

```xml
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-flyway</artifactId>
</dependency>
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-jdbc-postgresql</artifactId>
</dependency>
```

### 4.2 `application.properties`

```properties
quarkus.flyway.migrate-at-start=true
quarkus.flyway.locations=classpath:db/migration
```

### 4.3 `src/main/resources/db/migration/V1__init.sql`

```sql
CREATE TABLE book (
  id BIGSERIAL PRIMARY KEY,
  title TEXT NOT NULL
);
```

### 4.4 `V2__add_author.sql`

```sql
ALTER TABLE book ADD COLUMN author TEXT;
```

### 4.5 Kubernetes：Job 迁移 vs 应用内迁移（讨论用 YAML）

**方案 A**：应用内 `migrate-at-start`（课堂默认）。

**方案 B**：独立 `Job`：

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: flyway-migrate
spec:
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: flyway
          image: flyway/flyway:10
          args:
            - migrate
            - -url=jdbc:postgresql://postgres:5432/app
            - -user=$(DB_USER)
            - -password=$(DB_PASS)
          env:
            - name: DB_USER
              valueFrom:
                secretKeyRef: { name: db-secret, key: username }
            - name: DB_PASS
              valueFrom:
                secretKeyRef: { name: db-secret, key: password }
```

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 空库启动应用 | Flyway 创建 `flyway_schema_history` |
| 2 | 加 `V2` 脚本重启 | 版本递增 |
| 3 | 故意写坏 SQL | 启动失败，学会读日志 |
| 4 | 讨论：多副本同时启动谁拿锁？ | Flyway 行为理解 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 版本化、可协作。 |
| **缺点** | 坏迁移影响发布。 |
| **适用场景** | 一切有 schema 的服务。 |
| **注意事项** | 大表变更策略。 |
| **常见踩坑** | 与 Hibernate `generation` 冲突；无锁竞争意识。 |

**延伸阅读**：<https://quarkus.io/guides/flyway>
