# 第 28 章：Qute 模板引擎

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 45 分钟 |
| **学习目标** | 使用 Qute 渲染文本/HTML；理解 XSS 与转义 |
| **先修** | 第 5 章 |

---

## 1. 项目背景

邮件、运营通知、简单 SSR 适合 **Qute**（本仓库 `independent-projects/qute`）。与 SPA 互补，不是替代。

---

## 2. 项目设计：大师与小白的对话

**小白**：「模板引擎过时吗？」

**大师**：「**边界场景**成本最低。别用技术标签代替 ROI。」

**安全**：「用户输入进模板？」

**大师**：「**必须转义**；模板里不写业务规则。」

**测试**：「模板怎么测？」

**大师**：「快照测试或渲染结果断言关键子串。」

---

## 3. 知识要点

- `quarkus-qute`  
- `src/main/resources/templates/*.html`  
- `{name}` vs `{name.raw}`（慎用 raw）

---

## 4. 项目实战

### 4.1 `pom.xml`

```xml
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-qute</artifactId>
</dependency>
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-rest-qute</artifactId>
</dependency>
```

### 4.2 `src/main/resources/templates/mail.txt`

```text
您好 {name}，

您的订单 {orderId} 已确认。

此致
ACME
```

### 4.3 渲染 Bean

`src/main/java/org/acme/MailRenderer.java`：

```java
package org.acme;

import io.quarkus.qute.Template;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

@ApplicationScoped
public class MailRenderer {

    @Inject
    Template mail;

    public String render(String name, String orderId) {
        return mail.data("name", name).data("orderId", orderId).render();
    }
}
```

### 4.4 JAX-RS 返回模板（若使用 rest-qute）

或使用 `MailRenderer` 注入到普通 Resource 返回 `String`。

### 4.5 Kubernetes

无特殊 YAML；若模板从 ConfigMap 挂载，可用 `volumeMount`（一般不推荐，版本与 Git 管理更好）。

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 渲染含 `<script>` 的用户名 | 验证是否被转义 |
| 2 | 修改模板热重载（dev） | 体验开发流 |
| 3 | 讨论：与 MJML/第三方邮件服务集成点 | 架构草图 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 轻、快。 |
| **缺点** | 团队前端栈分裂时需规范。 |
| **适用场景** | 邮件、文本。 |
| **注意事项** | XSS；国际化。 |
| **常见踩坑** | 业务逻辑写进模板。 |

**延伸阅读**：<https://quarkus.io/guides/qute>
