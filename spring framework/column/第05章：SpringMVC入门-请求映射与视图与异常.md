# 第 05 章：Spring MVC 入门——请求映射、视图与异常

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

## 上一章思考题回顾

1. **`@PropertySource` 与 YAML**：标准 `@PropertySource` **默认只支持 properties**；YAML 需 **Spring Boot** 的 `ConfigData` 或第三方 `spring-context` 扩展。  
2. **`@Controller` vs `@RestController`**：`@RestController` = `@Controller` + `@ResponseBody`，方法返回值默认序列化为 HTTP body；纯 MVC 视图跳转用 `@Controller` + 视图名。

---

## 1 项目背景

运营端需要「订单列表页」与「JSON 接口」并存：早期 JSP/Thymeleaf 页面给内部使用，**移动端**要 REST。若两套框架混用无统一异常处理，**404/500** 返回格式不一致，**前端联调成本**高。

**痛点**：  
- 映射冲突：`GET /orders` 与 `/orders/{id}` 顺序错误。  
- 异常泄漏到 Servlet 容器，**无结构化错误体**。  
- 视图解析与 API 混在一个 Controller，职责不清。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：从「裸 Servlet」痛点 → 映射与异常链 → 统一错误契约。

**小胖**：为啥不直接 Servlet 写 `doGet`？我大学实验课就这么写的，稳得很。

**大师**：稳的是**作业规模**；企业里你要处理 **编码、内容协商、参数绑定、异常映射、Locale、静态资源**，Servlet 里会迅速变成「**一千行 if-else 路由器**」。Spring MVC 把 **URL → Handler → 视图/Body** 这条链标准化，并提供 **拦截器、参数解析、异常解析**。

**技术映射**：**DispatcherServlet** 是前端控制器；**HandlerMapping / HandlerAdapter** 负责「找谁处理、怎么调用」。

**小白**：`@RequestMapping` 与 `@GetMapping` 关系？

**大师**：`@GetMapping` 是 `@RequestMapping(method=GET)` 的组合注解，**语义更清晰**；团队规范常要求 **读操作用 GET**，避免「用 GET 改状态」。

**技术映射**：**HTTP 动词语义** 影响缓存与幂等；别为了省事把下单写成 GET。

**小胖**：404 和 405 我老混：不都是「找不到」吗？

**小白**：404 是 **URL 没映射**；405 是 **映射到了但方法不允许**（比如只注册了 POST，你偏要 GET）。

**大师**：联调时先把 **HTTP 方法、路径变量、Content-Type** 三件事对齐，比改十次业务代码管用。

**技术映射**：**RequestMappingInfo** 同时匹配 **path + method + consumes/produces**。

**小白**：异常直接抛给 Tomcat 行吗？

**大师**：行，但前端拿到的是 **HTML 错误页** 或 **不可解析 body**；对内对外 API 建议 **`@ControllerAdvice`** 统一成 **`ProblemDetail`**（RFC 9457 风格，Boot 3 原生支持）。

**技术映射**：**@RestControllerAdvice** = 全局异常 + 默认 `@ResponseBody`。

**小胖**：内部运营页要 Thymeleaf，移动端要 JSON，会不会打架？

**大师**：分层：**页面 Controller** 返回视图名；**API Controller** 用 `@RestController`。别在同一个方法里「有时 String 有时对象」——除非你能接受维护成本。

---

## 3 项目实战

本章用 **Boot 3** 起一个最小 Web 服务，并用 **`curl`** 与 **`MockMvc`** 两次验证「**路由 + 异常 + ProblemDetail**」。

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| JDK | 17+ |
| Boot | 3.2.x（示例） |
| 依赖 | `spring-boot-starter-web` |

```xml
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-web</artifactId>
</dependency>
```

**`application.properties`（可选）**

```properties
server.port=8080
```

### 3.2 分步实现

**步骤 1 — 目标**：定义 **REST 控制器** 与 **全局异常**（ProblemDetail）。

**REST 控制器**

```java
package com.example.web;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/orders")
public class OrderApiController {

    @GetMapping("/{id}")
    public String get(@PathVariable String id) {
        if ("bad".equalsIgnoreCase(id)) {
            throw new IllegalArgumentException("illegal id");
        }
        return "order:" + id;
    }
}
```

**步骤 2 — 目标**：统一把非法参数转为 **400 + ProblemDetail**。

**全局异常**

```java
package com.example.web;

import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.web.bind.annotation.*;

@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(IllegalArgumentException.class)
    public ProblemDetail bad(IllegalArgumentException ex) {
        ProblemDetail p = ProblemDetail.forStatusAndDetail(HttpStatus.BAD_REQUEST, ex.getMessage());
        p.setTitle("Bad Request");
        return p;
    }
}
```

**步骤 3 — 目标**：本地启动后，用 **`curl`** 验证**成功路径**与**异常路径**。

**命令行（成功）**

```bash
curl -i http://localhost:8080/api/orders/123
```

**期望（文字描述）**：`HTTP/1.1 200`，body 含 `order:123`。

**命令行（触发异常）**

```bash
curl -i http://localhost:8080/api/orders/bad
```

**期望（文字描述）**：`HTTP/1.1 400`，`Content-Type` 含 `application/problem+json`（或 `application/json`，取决于 Boot 版本与配置），body 含 `detail` 与 `title`。

### 3.3 完整代码清单与仓库

`chapter05-mvc`。

### 3.4 测试验证

`MockMvc`：

```java
mockMvc.perform(get("/api/orders/1")).andExpect(status().isOk());
mockMvc.perform(get("/api/orders/bad")).andExpect(status().isBadRequest());
```

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 404 | `context-path` 或 base package 未扫描 | 检查 `@SpringBootApplication` 包 |
| 异常返回 HTML | 非 `@RestControllerAdvice` 或未引入 `spring-boot-starter-web` 的异常处理 | 对齐依赖与注解 |
| CORS 预检失败 | 浏览器 OPTIONS 被安全拦截 | 第 18 章再展开 |

---

## 4 项目总结

### 优点与缺点

| 维度 | Spring MVC | 裸 Servlet |
|------|--------------|------------|
| 开发效率 | 高 | 低 |
| 学习曲线 | 需理解映射链 | 小 |

### 常见踩坑经验

1. **路径变量**与**通配符**冲突。  
2. **consumes/produces** 误配导致 415。  
3. **异常被吞** 未进入 `@ControllerAdvice`。

---

## 思考题

1. `Content-Type: application/json` 与 `HttpMessageConverter` 如何选？（第 06 章。）  
2. `@Valid` 校验失败默认返回什么？（第 07 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **测试** | 用 `MockMvc` 固化路由契约。 |
| **前端** | 对齐 ProblemDetail 字段。 |

**下一章预告**：Jackson 配置、日期格式、内容协商。
