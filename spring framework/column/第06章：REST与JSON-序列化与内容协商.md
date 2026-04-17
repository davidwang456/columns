# 第 06 章：REST 与 JSON——序列化与内容协商

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

## 上一章思考题回顾

1. **HttpMessageConverter**：`Accept` / `Content-Type` 与 **内容协商**（`ContentNegotiationConfigurer`）决定用 **Jackson** 还是 XML 等；`MappingJackson2HttpMessageConverter` 处理 `application/json`。  
2. **`@Valid` 失败**：默认 Spring MVC 返回 **400**，`MethodArgumentNotValidException` 或 `BindException`，可由 `@ControllerAdvice` 统一为 `ProblemDetail`。

---

## 1 项目背景

「鲜速达」App 与小程序共用同一套 API：订单对象含 **`Instant` 下单时间**、**`BigDecimal` 金额**。若 Jackson 默认序列化成难以解析的格式，或 **精度丢失**，会引发**对账纠纷**。

**痛点**：  
- 时间格式不一致（时区）。  
- `Long` 被前端转 Number **精度丢失**。  
- 同一接口既要 JSON 又要 XML（极少数 B2B）。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：先承认「能跑」的偷懒写法 → 再抬到「契约与对账」视角。

**小胖**：为啥不直接 `toString()` 返回？我打印出来也是字符串啊。

**小白**：那你让前端怎么 **稳定解析**？`Order@1a2b3c` 这种输出，版本一升级字段顺序变了，小程序全挂。

**大师**：**序列化**是契约：字段名、类型、时区、金额精度都要稳定。Jackson 通过 **注解 + 全局模块** 固化；**DTO** 与 **领域对象** 分离，避免把实体类直接当 API 合同。

**技术映射**：**ObjectMapper** + **HttpMessageConverter**；MVC 层通过 **`MappingJackson2HttpMessageConverter`** 写入 HTTP body。

**小白**：`@JsonFormat` 和全局配置冲突谁赢？

**大师**：一般 **字段级注解** 优先；全局是**默认**。团队规范建议：**全局定「底线」**，字段上只处理**例外**。

**小胖**：金额我用 `double` 行不行？就俩小数点。

**大师**：金融场景请 **`BigDecimal`**；`double` 是二进制浮点，**0.1 + 0.2** 都能给你惊喜。前端若用 JS，**字符串承载金额**是常见解。

**技术映射**：**JsonFormat.Shape.STRING** 让 JSON 里金额以字符串出现，降低 JS 精度坑。

**小白**：时间戳用毫秒还是 ISO-8601？

**大师**：对内服务可用 **epoch millis**；对多端协作 **ISO-8601 + 明确时区**更不容易撕逼。务必在文档写清：**UTC 还是 Asia/Shanghai**。

---

## 3 项目实战

本章在 **第 05 章 Web 基础**之上，补一个「**能验收 JSON 合同**」的最小例子：先看 **HTTP 头**，再看 **body 字段形态**。

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 依赖 | `spring-boot-starter-web`（已含 Jackson） |
| 工具 | `curl` 或 HTTPie；可选 `jq` 美化输出 |

### 3.2 分步实现

**步骤 1 — 目标**：定义 **DTO**（金额字符串化 + 时间格式化）。

**DTO**

```java
package com.example.api;

import com.fasterxml.jackson.annotation.JsonFormat;
import java.math.BigDecimal;
import java.time.Instant;

public record OrderResponse(
    String id,
    @JsonFormat(shape = JsonFormat.Shape.STRING)
    BigDecimal amount,
    @JsonFormat(pattern = "yyyy-MM-dd'T'HH:mm:ss'Z'", timezone = "UTC")
    Instant createdAt
) { }
```

**步骤 2 — 目标（可选）**：全局关闭「日期变时间戳」，减少前后端误会。

**全局配置（可选）**

```java
package com.example.api;

import com.fasterxml.jackson.databind.SerializationFeature;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.converter.json.Jackson2ObjectMapperBuilder;

@Configuration
public class JacksonConfig {
    @Bean
    public Jackson2ObjectMapperBuilder jackson2ObjectMapperBuilder() {
        return new Jackson2ObjectMapperBuilder()
                .featuresToDisable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }
}
```

**步骤 3 — 目标**：提供 `GET /api/orders/demo` 返回 `OrderResponse`（可用 `@GetMapping` 手写演示数据）。

**验证命令（建议带 `-v` 看协商）**

```bash
curl -v -H "Accept: application/json" http://localhost:8080/api/orders/demo
```

**运行结果（文字描述）**：`Content-Type` 为 `application/json`；body 中 **`amount` 为字符串**；`createdAt` 为格式化时间字符串（与 `@JsonFormat` 一致）。

### 3.3 完整代码清单与仓库

`chapter06-json`。

### 3.4 测试验证

1. **`@JsonTest`**：序列化快照，防止字段名漂移。  
2. **`MockMvc`**：`jsonPath("$.amount").isString()` 断言金额形态。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 时间差 8 小时 | `timezone` 未指定 | 明确 UTC/业务时区 |
| Long 精度丢 | JS Number 53 位限制 | 改字符串或拆分 |
| 循环引用 StackOverflow | 实体互相引用 | DTO + `@JsonIgnore` 或 `@JsonManagedReference` |

---

## 4 项目总结

### 优点与缺点

| 维度 | 注解 + 全局模块 | 手写拼接 JSON |
|------|-----------------|---------------|
| 一致性 | 高 | 低 |
| 灵活性 | 需理解优先级 | 随意 |

### 常见踩坑经验

1. **时区**未指定导致跨环境差 8 小时。  
2. **BigDecimal** 未用字符串形态在 JS 里丢精度。  
3. **循环引用** 导致 StackOverflow（需 `@JsonIgnore` 或 DTO）。

---

## 思考题

1. `groups` 与 `@Validated` 在 Controller 方法参数上如何用？（第 07 章。）  
2. `@ControllerAdvice` 与 `@RestControllerAdvice` 差异？（第 07 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **测试** | 契约测试校验 JSON Schema。 |
| **前端** | 对齐金额/时间字段类型。 |

**下一章预告**：Bean Validation、统一异常体、错误码。
