# 第9章：HTTP 请求节点与外部系统集成

## 1. 项目背景

"AI 生成的回复很精彩，但全是 ChatGPT 脑子里的知识——我们内部的订单系统、CRM、数据库里的数据，AI 完全不知道。"这是大多数企业落地 AI 应用时的第一道坎。LLM 擅长语言理解和生成，但它无法直接访问企业内部系统。你需要一个"桥梁"——让 AI 流程在需要的时候，自动去查询内部的订单 API、调用 CRM 的客户接口、读取数据库的报表数据，然后把结果传给 LLM 进行理解和格式化。

Dify Workflow 中的 HTTP 请求节点就是这座桥梁。它让你在 Workflow 画布中直接发起 HTTP 请求，调用任何 REST API——不管是 OpenAI 的最新模型、公司内部的微服务、第三方的天气/地图服务，还是 Jira/TAPD 等项目管理工具。请求的 URL、Headers、Body 可以动态使用 Workflow 中的变量，返回的数据可以解析到变量池中供下游节点使用。

但 HTTP 节点看似简单，踩坑点不少：认证头怎么写？返回的 JSON 怎么解析给 LLM？请求超时了怎么处理？Docker 容器里访问 `localhost` 为什么不通？本章通过三个真实场景——查询订单信息、调用后台 API、集成第三方服务——带你彻底掌握 HTTP 节点，打通 Dify 和现有业务系统的数据通道。

## 2. 项目设计——剧本式交锋对话

**小胖**：（满脸困惑）"大师，我在 Workflow 里拖了一个 HTTP 请求节点，想查我们内部订单 API。但填 URL 的时候我就卡住了——在浏览器里我用 `http://localhost:8080/api/orders` 能访问，但 Workflow 里怎么配？"

**大师**："关键认知：Workflow 里的 HTTP 节点是在 **Dify API 容器**里执行的，不是在你的浏览器里。容器内的 `localhost` 指向的是容器自己，不是你的宿主机。所以如果你公司的订单 API 跑在宿主机上，你得用 `http://host.docker.internal:8080`（Windows/macOS）或者 `http://172.17.0.1:8080`（Linux）。"

**技术映射**：Docker 网络隔离 → 容器内地址 ≠ 宿主机地址，跨网络调用需要选择正确的网络出口。

**小白**："那 Dify 的 API 容器访问外网呢？比如调 OpenAI 的 API 不用特殊配置对吧？"

**大师**："对，外网不需要特殊配置——Docker 容器默认能访问外网（通过宿主机的 NAT）。但这里有个安全设计：Dify 的 API 容器访问外网时，**实际上会先经过一个 SSRF 代理（Squid）**。这是一个安全措施——防止有人通过 HTTP 节点访问内网的敏感地址（比如 `http://169.254.169.254` 获取云服务器的元数据）。"

**小胖**："哦！所以如果我的订单 API 在本地内网，也可能被代理拦截？"

**大师**："有可能。Dify 的 SSRF 代理默认会拒绝访问内网地址段（10.0.0.0/8、172.16.0.0/12、192.168.0.0/16）。如果你确实需要访问内网 API，需要在 SSRF 代理配置中添加白名单。但在生产环境做这件事要非常小心——我们到高级篇第 38 章会深入讲。"

**技术映射**：SSRF 代理 = 请求防火墙，防止恶意用户通过 Dify 的 HTTP 节点攻击内网。

**小白**："那认证怎么处理？我们内部 API 用的是 Bearer Token。"

**大师**："HTTP 节点的 Header 配置支持变量注入。你可以这样配：

```
Headers:
  Authorization: Bearer {{#api_token_node.text#}}
  Content-Type: application/json
```

这样 Token 可以从上游节点动态获取——比如用 Token 刷新节点先获取最新 Token，再传给 HTTP 节点。但注意：不要在 Workflow DSL 里硬编码 Token！因为 DSL 可能被导出分享。"

**小胖**："返回的 JSON 怎么给 LLM 用？我看返回体是一个巨大的 JSON，LLM 读得懂吗？"

**大师**："分两步：
1. HTTP 节点会自动把响应体解析为对象，存到三个输出变量：`body`（响应体）、`status_code`（HTTP 状态码）、`headers`（响应头）。
2. 下游 LLM 节点引用 `{{#http_node.body#}}`，LLM 完全能读懂 JSON——你可以在 Prompt 里告诉它'以下是订单 API 返回的 JSON 数据，请提取出订单状态和金额'。"

**技术映射**：HTTP 节点输出 → 变量池（body/status_code/headers）→ LLM Prompt 引用 → 非结构化数据到自然语言的转换。

## 3. 项目实战

### 环境准备

| 条件 | 说明 |
|------|------|
| Dify 已部署 | 第 2 章完成 |
| Workflow 基础掌握 | 第 6-7 章完成 |
| 一个可测试的 HTTP API | 使用 jsonplaceholder 或自建 Mock API |

### 分步实现

#### 步骤1：调用公共 Mock API——理解 HTTP 节点基础（目标：掌握节点配置）

场景：查询一个用户的信息，把返回的 JSON 交给 LLM 生成一段介绍。

1. 创建 Workflow → "用户信息展示"

2. 节点编排：

```
开始（输入：user_id, 默认值 "1"）
    ↓
HTTP 请求（调用 jsonplaceholder API）
    ↓
LLM（将 JSON 转为自然语言）
    ↓
结束
```

3. HTTP 节点配置：

```yaml
请求方式：GET
URL：https://jsonplaceholder.typicode.com/users/{{#start.user_id#}}
Headers：
  Content-Type: application/json
Authorization：（留空，这个 API 不需要认证）
超时时间：10 秒

输出变量：
  body（对象）   → 作为下游 LLM 的输入
  status_code（数字）
  headers（对象）
```

4. LLM 节点配置：

```yaml
系统提示词：
  你将收到一个 JSON 格式的用户信息，请将其转换为一句简短的自然语言介绍。
  
  JSON 数据：{{#http_node.body#}}
  
  请用中文输出，格式："[姓名]在[公司]工作，地址在[城市]，联系方式是[邮箱]。"
```

5. 运行测试，输入 `user_id=1`：

**HTTP 节点返回示例**：
```json
{
  "id": 1,
  "name": "Leanne Graham",
  "username": "Bret",
  "email": "Sincere@april.biz",
  "address": {"street": "Kulas Light", "city": "Gwenborough", "zipcode": "92998"},
  "company": {"name": "Romaguera-Crona"},
  "phone": "1-770-736-8031"
}
```

**LLM 节点输出**：
```
Leanne Graham 在 Romaguera-Crona 公司工作，所在城市为 Gwenborough，联系邮箱是 Sincere@april.biz。
```

**发现**：LLM 能自动从 JSON 中提取关键字段并组织成自然语言，不需要你手动解析 JSON 字段。

#### 步骤2：调用需要认证的内部 API（目标：处理认证头）

场景：模拟调用公司内部订单系统，需要 Bearer Token 认证。

由于真实的内网 API 不方便模拟，这里用 GitHub API 作为示例（它也需要认证）：

1. 创建 Workflow → "GitHub 仓库查询"

2. 先在 Dify 控制台 → **工具** 中创建一个 API Token 变量（或硬编码 Token）：

```bash
# 生成 GitHub Personal Access Token
# GitHub → Settings → Developer settings → Personal access tokens
# 勾选 repo 权限，生成 token：ghp_xxxxxxxxxxxxxxxxxxxx
```

3. HTTP 节点配置：

```yaml
请求方式：GET
URL：https://api.github.com/repos/langgenius/dify
Headers：
  Authorization: Bearer {{#start.github_token#}}
  Accept: application/vnd.github+json
  X-GitHub-Api-Version: 2022-11-28
超时时间：15 秒
```

4. 开始节点添加变量：

```yaml
变量 1：github_token（类型：文本，在运行面板填写）
变量 2：repo_name（类型：文本，默认值："langgenius/dify"）
```

5. LLM 节点：

```yaml
系统提示词：
  分析以下 GitHub 仓库信息，用中文总结该项目的核心数据。
  
  仓库数据：
  {{#http_node.body#}}
  
  请提取并展示：项目名称、星标数、Fork 数、主要语言、开源协议、项目描述。
```

6. 运行测试：

**HTTP 响应**（截取）：
```json
{
  "name": "dify",
  "full_name": "langgenius/dify",
  "stargazers_count": 58000,
  "forks_count": 8500,
  "language": "TypeScript",
  "license": {"spdx_id": "Apache-2.0"},
  "description": "Dify is an open-source LLM app development platform..."
}
```

**LLM 输出**：
```
项目：Dify（langgenius/dify）
⭐ Star：58000+ | 🍴 Fork：8500+
语言：TypeScript | 协议：Apache-2.0
简介：Dify 是一个开源的 LLM 应用开发平台...
```

#### 步骤3：错误处理——超时、非 200 响应（目标：健壮的 HTTP 调用）

场景：API 可能挂掉或返回错误，Workflow 需要优雅降级。

在 Workflow 中添加错误处理分支：

```
HTTP 请求（可能失败）
    ↓
IF/ELSE（根据 status_code 判断）
    ├── status_code == 200 → LLM_正常（正常处理）
    ├── status_code == 401 → LLM_认证失败（"请检查 Token 是否有效"）
    ├── status_code == 404 → LLM_未找到（"未找到该资源"）
    └── ELSE → LLM_未知错误（"系统繁忙，请稍后重试"）
```

**IF/ELSE 节点配置**：

```yaml
条件 1：
  变量：{{#http_node.status_code#}}
  条件：=
  值：200

条件 2：
  变量：{{#http_node.status_code#}}
  条件：=
  值：401

条件 3：
  变量：{{#http_node.status_code#}}
  条件：=
  值：404
```

**注意**：`status_code` 是 HTT节点输出变量中的数字类型，所以条件值不要加引号。

#### 步骤4：高级用法——POST 请求与动态 Body（目标：掌握复杂请求）

场景：调用内部 CRM 创建客户记录。

HTTP 节点配置：

```yaml
请求方式：POST
URL：https://crm.internal.acme.com/api/v1/customers
Headers：
  Authorization: Bearer {{#start.api_token#}}
  Content-Type: application/json
请求体（JSON）：
{
  "name": "{{#start.customer_name#}}",
  "email": "{{#start.customer_email#}}",
  "source": "Dify Workflow",
  "tags": {{#llm_extract_tags.text#}}
}
超时时间：15 秒
```

**关键点**：
- Body 中的变量 `{{#llm_extract_tags.text#}}` 必须是合法的 JSON 数组格式（如 `["VIP", "高意向"]`）
- 如果变量值是字符串，需要用双引号包裹：`"{{#start.name#}}"`
- 如果变量值是数字/数组/对象，不要用引号：`{{#start.age#}}`

### 测试验证

```bash
# 测试 1：验证 HTTP 节点基础调用
curl -X POST http://localhost/v1/workflows/run \
  -H "Authorization: Bearer app-xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": {"user_id": "1"},
    "response_mode": "blocking",
    "user": "test"
  }'

# 预期返回：最终输出中包含 jsonplaceholder 的用户信息

# 测试 2：模拟 API 故障（故意用错误 URL）
# 修改 HTTP 节点 URL 为 https://jsonplaceholder.typicode.com/users/999999
# 预期：返回 404，IF/ELSE 路由到"未找到"分支

# 测试 3：验证 SSRF 防护
# 在 HTTP 节点 URL 中填写：http://169.254.169.254/latest/meta-data/
# 预期：请求被 SSRF 代理拦截，返回错误
```

## 4. 项目总结

### 优点与缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| **集成能力** | 一行代码不写即可调用任意 REST API，打通内部系统 | 仅支持 REST/HTTP，不支持 gRPC、WebSocket、GraphQL（需自定义节点） |
| **变量注入** | URL/Headers/Body 全面支持变量动态拼接 | 复杂请求体（嵌套数组/条件字段）手动拼接易出错 |
| **响应解析** | 自动解析 JSON 为变量，LLM 可直接理解 | 超大响应体（如 10MB JSON）会消耗大量内存，缺少分页/流式处理 |
| **安全防护** | SSRF 代理阻止访问内网敏感地址 | 白名单配置不够灵活，内网 API 集成需要额外配置 |
| **错误处理** | status_code + IF/ELSE 实现基本的错误路由 | 不支持自动重试、超时退避，需要在 Workflow 层手动设计 |

### 适用场景

| 场景 | HTTP 节点使用方式 |
|------|-----------------|
| **内部数据查询** | GET 请求查订单/客户/库存数据 → LLM 格式化 |
| **外部服务集成** | 调用天气/地图/翻译等第三方 API 获取实时数据 |
| **数据写入** | POST/PUT 创建工单、更新 CRM、写入数据库 |
| **Webhook 触发** | Workflow 执行完成后调用外部系统的回调 URL |
| **数据同步** | 定时 Workflow 拉取外部数据更新知识库 |

**不适用场景**：
- 高频调用（如 QPS > 100），因为每个 HTTP 节点都会经过 SSRF 代理，延迟较高
- 超大文件上传/下载（应该用 Dify 的 File API 代替）

### 注意事项

1. **敏感信息保护**：Header 中的 Token 不要硬编码在 Workflow DSL 中，使用开始节点的输入变量或环境变量
2. **超时配置**：LLM 节点后的 HTTP 调用要考虑"总超时时间 = Workflow 超时 + HTTP 节点超时"，设置合理的值
3. **Docker 网络**：访问宿主机服务用 `host.docker.internal`，访问其他 Docker 服务用服务名
4. **HTTPS 证书**：访问自签名证书的内网 API 可能会报 SSL 错误，需要配置 Dify 的 CA 证书信任

### 常见踩坑经验

1. **坑：HTTP 返回 `502 Bad Gateway`** → 根因：URL 写错了（多写了空格、协议头写成了 `https//` 少冒号）。解决：先在容器内用 curl 测试：`docker exec docker-api-1 curl -v "http://host.docker.internal:8080/api"`
2. **坑：POST 请求的 Body 没有被正确解析** → 根因：Content-Type 没设为 `application/json`，或者 JSON Body 中有变量值没有正确序列化。解决：确保数字/布尔值不加引号，文本值加引号
3. **坑：内网 API 通不了，但 curl 能通** → 根因：SSRF 代理拦截了内网地址。解决：查看 `docker/nginx/conf.d/dify.conf` 中的代理配置，或临时禁用 SSRF（仅开发环境）

### 思考题

1. **进阶题**：如果你的内部 API 返回的是分页数据（如 `{"data": [...], "total": 500, "page": 1}`），你如何在 Workflow 中自动翻页获取全部数据？

2. **进阶题**：Dify 的 HTTP 节点默认不允许访问 `127.0.0.1` 和 `169.254.169.254`，但如果在 K8s 环境中你需要访问同一个 Pod 内的 sidecar 容器，该如何做？（提示：思考 K8s Pod 内的网络共享机制）

> **参考答案**：见附录 D

---

> **推广计划提示**：本章是连接 Dify 与企业现有系统的关键。运维人员应重点关注 SSRF 代理和网络打通。开发人员建议完成本章后，将公司内部的 2-3 个核心 API 接入 Workflow，形成团队可复用的"API 连接器"模板。
