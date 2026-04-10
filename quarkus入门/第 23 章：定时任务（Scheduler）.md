# 第 23 章：定时任务（Scheduler）

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 40～50 分钟 |
| **学习目标** | 使用 `@Scheduled`；解释 K8s 多副本重复执行；对比 CronJob |
| **先修** | 第 5 章 |

---

## 1. 项目背景

定时任务用于对账、清理、批处理。在 **Deployment 多副本**下，同一 cron 会**重复执行**，需要幂等、leader 选举或改用 **Kubernetes CronJob**。

---

## 2. 项目设计：大师与小白的对话

**小白**：「每分钟跑一次 `@Scheduled`。」

**大师**：「三个副本跑三次。你的任务**幂等吗**？」

**运维**：「长任务占满线程池怎么办？」

**大师**：「限制并发、拆分、或独立 worker Deployment。」

**架构师**：「何时用 CronJob？」

**大师**：「与业务进程**解耦**、需要独立扩缩、失败重试策略清晰时。」

---

## 3. 知识要点

- `quarkus-scheduler`  
- cron 表达式（Quartz 风格 6～7 字段，以文档为准）  
- `@Scheduled.concurrentExecution = ConcurrentExecution.SKIP` 等（若版本支持）

---

## 4. 项目实战

### 4.1 `pom.xml`

```xml
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-scheduler</artifactId>
</dependency>
```

### 4.2 任务 Bean

`src/main/java/org/acme/jobs/TickJob.java`：

```java
package org.acme.jobs;

import io.quarkus.scheduler.Scheduled;
import jakarta.enterprise.context.ApplicationScoped;
import org.jboss.logging.Logger;

@ApplicationScoped
public class TickJob {

    private static final Logger LOG = Logger.getLogger(TickJob.class);

    @Scheduled(cron = "0 */2 * * * ?")
    void everyTwoMinutes() {
        LOG.info("tick");
    }
}
```

### 4.3 Kubernetes：`CronJob` 对照示例

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: nightly-report
spec:
  schedule: "0 2 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: runner
              image: registry.example.com/acme/batch:1.0.0
              args: ["run-report"]
```

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | dev 模式观察 tick 日志 | 每 2 分钟一条 |
| 2 | 扩展副本为 3（集群或本地无副本则讨论） | 理解重复执行 |
| 3 | 设计幂等键（DB 唯一约束）草图 | 小组产出 |
| 4 | 对比 CronJob YAML 与应用内调度 | 选型表 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 简单。 |
| **缺点** | 多副本重复。 |
| **适用场景** | 轻量周期任务。 |
| **注意事项** | 超时；线程池。 |
| **常见踩坑** | 长事务；无幂等。 |

**延伸阅读**：<https://quarkus.io/guides/scheduler>
