# 第 07 章：参数校验与统一异常——Bean Validation

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

## 上一章思考题回顾

1. **`@Validated` + groups**：在 Controller 方法参数上使用 `@Validated(Create.class)` 等 **分组**，与 DTO 字段上 `@NotNull(groups=Create.class)` 配合，实现**同一 DTO 多场景**校验。  
2. **`@ControllerAdvice` vs `@RestControllerAdvice`**：与 Controller 类似，后者等价于 `@ControllerAdvice` + `@ResponseBody`，异常处理方法默认写 JSON。

---

## 1 项目背景

下单接口要求 **`skuId` 非空**、**数量 1–999**、**收货地址长度**限制。若在每个方法手写 `if`，**重复**且**错误码不统一**；若不做校验，**脏数据**会进入库存与支付，**回滚成本**高。

**痛点**：  
- 校验逻辑与业务逻辑耦合。  
- 国际化错误消息缺失。  
- 校验异常与业务异常混用，**HTTP 状态码**混乱。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：从「手写 if」到「声明式约束」→ 再讨论分组与异常模型。

**小胖**：为啥不直接 `if (qty < 1)`？我写三行代码，比注解清楚。

**大师**：三行在**一个方法**里清楚；三十个方法复制粘贴就**不清楚**了。Bean Validation 把**约束声明**在字段上，**可组合、可复用、可测试**；Spring 通过 **`MethodValidationPostProcessor`** 还能做方法级校验。

**技术映射**：**JSR 380**（`jakarta.validation`）；Spring 负责把校验失败映射为 **`MethodArgumentNotValidException`** 等。

**小白**：`@Valid` 与 `@Validated`？

**大师**：`@Valid` 标准注解，**级联**（嵌套对象继续校验）；`@Validated` 是 Spring 的，**支持分组**（同一 DTO 在「创建」与「更新」两套规则）。

**技术映射**：**分组** = `groups = {Create.class}` + 方法上 `@Validated(Create.class)`。

**小胖**：校验失败返回啥？我给前端一个「字符串：不行」行不行？

**小白**：行，但很难自动化对接：测试、契约、监控都不好统计。至少做到 **HTTP 400 + 结构化字段错误**（`field + message`），更好是 **ProblemDetail + violations**。

**大师**：可以把校验看成「**门口安检**」：业务异常是「**登机口改签**」——别混在同一个 catch 里，否则排障的人会骂娘。

**技术映射**：**MethodArgumentNotValidException** → `BindingResult`；可映射为 **RFC 9457** 的 `invalid_params` 扩展（按团队规范）。

---

## 3 项目实战

本章延续 **订单下单 API**：用 **`curl` 负面用例** 验证 **400**；用 **`MockMvc`** 做回归。

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 依赖 | `spring-boot-starter-validation` |
| 前置 | 已有 Web（`spring-boot-starter-web`） |

```xml
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-validation</artifactId>
</dependency>
```

### 3.2 分步实现

**步骤 1 — 目标**：定义 **请求 DTO** 与 **POST** 接口。

**请求 DTO**

```java
package com.example.order;

import jakarta.validation.constraints.*;

public record PlaceOrderRequest(
    @NotBlank String skuId,
    @Min(1) @Max(999) int qty)
{ }
```

**Controller**

```java
package com.example.order;

import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/orders")
public class OrderController {

    @PostMapping
    public String place(@Valid @RequestBody PlaceOrderRequest req) {
        return "OK:" + req.skuId();
    }
}
```

**异常处理**

```java
package com.example.order;

import org.springframework.http.*;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.*;

@RestControllerAdvice
public class ValidationAdvice {

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ProblemDetail handle(MethodArgumentNotValidException ex) {
        String msg = ex.getBindingResult().getFieldErrors().stream()
                .findFirst().map(f -> f.getField() + ":" + f.getDefaultMessage())
                .orElse("validation failed");
        return ProblemDetail.forStatusAndDetail(HttpStatus.BAD_REQUEST, msg);
    }
}
```

**步骤 2 — 目标**：用 **`curl`** 构造两类负面用例：**字段为空**、**数值越界**。

**命令行（空 sku + qty=0）**

```bash
curl -i -X POST http://localhost:8080/api/orders ^
  -H "Content-Type: application/json" ^
  -d "{\"skuId\":\"\",\"qty\":0}"
```

（Linux/macOS 去掉 `^` 换行符即可。）

**期望（文字描述）**：`HTTP/1.1 400`；body 为 ProblemDetail 或 JSON，包含 `skuId`/`qty` 相关错误信息。

**步骤 3 — 目标（加深，可选）**：给 `PlaceOrderRequest` 增加 `groups`（`interface Create {}`），在 `POST` 上使用 `@Validated(Create.class)`，让「更新接口」复用 DTO 但规则不同。

### 3.3 完整代码清单与仓库

`chapter07-validation`。

### 3.4 测试验证

`MockMvc`：

```java
mvc.perform(post("/api/orders")
        .contentType(MediaType.APPLICATION_JSON)
        .content("{\"skuId\":\"\",\"qty\":0}"))
    .andExpect(status().isBadRequest());
```

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| `@Valid` 不生效 | 未引入 validation starter | 加依赖 |
| 嵌套对象未校验 | 子对象缺 `@Valid` | 级联加注解 |
| 全是 500 | 异常未被 `@RestControllerAdvice` 覆盖 | 检查异常类型与包扫描 |

---

## 4 项目总结

### 优点与缺点

| 维度 | Bean Validation | 手写 if |
|------|-----------------|--------|
| 复用 | 高 | 低 |
| 学习成本 | 需理解约束与分组 | 低 |

### 常见踩坑经验

1. **未引入 validation starter** 导致 `@Valid` 不生效。  
2. **嵌套对象** 忘记 `@Valid`。  
3. **GET 参数** 校验需 `@Validated` 在类或方法上启用方法校验。

---

## 思考题

1. AOP **JDK 动态代理**与 **CGLIB** 适用条件？（第 08 章。）  
2. `@Transactional` 打在 private 方法上有效吗？（第 09 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **测试** | 用参数化测试覆盖边界值。 |
| **产品** | 错误码与文案对齐。 |

**下一章预告**：AOP 切面、切面顺序、日志与审计。
