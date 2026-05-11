# 第20章：HTTP节点与外部系统集成

> **定位**：让DolphinScheduler成为"万能插座"，通过HTTP节点将任何REST API外部系统编排进工作流。
> **核心内容**：HTTP方法支持、请求配置、认证策略、动态参数注入、响应解析与参数捕获、超时与重试、常见集成模式。
> **实战目标**：搭建一条调用天气API、触发Jenkins构建、编排ML平台训练的全链路HTTP集成工作流。

---

## 1. 项目背景

大麦电商的数据平台需要与多个外部系统协同工作：(1) Jenkins CI/CD平台——当上游数据仓库产出新分区数据后，需要触发数据管道构建镜像；(2) 第三方天气数据API——销售预测模型需要调用OpenWeatherMap获取城市的实时天气特征；(3) 内部ML平台——模型训练和推理通过REST API暴露，需要调度器触发训练并轮询训练状态；(4) 内部Slack-like即时通讯系统——需要将非关键通知推送到群聊频道。

目前，数据工程师老张是这样操作的：每天早晨先打开Postman，调用天气API确认数据可达，再切换到curl命令行触发Jenkins构建，然后打开ML平台的Web UI手动点击"开始训练"，最后再回到Postman查询训练状态——等到训练完成后再手动部署模型。上周天气API升级了endpoint，v2换成v3，老张正好休假，替班同事不知道这个变更，导致5条依赖该API的调度任务全部失败，销售预测报告推迟了半天才发出。

更麻烦的是：这些HTTP调用之间其实有严格的依赖关系——天气数据就绪后才可以开始预测模型训练，训练完成才能部署，部署完才能发通知。但因为是纯手工操作，每一步的耗时无法预估，衔接全凭老张经验。本质问题在于：**外部系统被排斥在调度体系之外**，数据平台内部用DolphinScheduler编排，但一旦逻辑延伸到了外部API，就只能靠人工和散落脚本拼凑。DolphinScheduler的HTTP任务节点正好填补这个缺口——它让DS变成一个"万能插座"，任何提供REST API的外部系统都能被纳入工作流DAG，实现端到端的自动化编排。

---

## 2. 项目设计——剧本式交锋对话

周一晨会后，老张把外部API集成的痛点在组会上吐槽了一番。大师让小白和小胖一起设计解决方案。

**小胖**（一边咬着包子一边翻DS文档）：

> "HTTP节点嘛，不就是个curl命令的外壳！填个URL，选个方法——GET还是POST——参数往里一塞就完了呗。同步调用，等返回就继续跑下一个节点，有啥好设计的？"

**小白**（放下手中的美式咖啡，眉头微皱）：

> "没这么简单。第一，API的认证token是会过期的——如果工作流跑了一半token失效了怎么办？第二，POST一个超大的JSON body，比如训练参数加特征列表上千行，HTTP节点有body大小限制吗？第三，API返回的结果怎么传递给下游任务？比如Jenkins构建触发后返回了一个build_number，我后面需要用它查询构建状态，这个值怎么在DAG里流转？第四，如果API返回500，HTTP节点是会立即失败，还是会重试？POST请求重试安不安全——万一第一次请求已经生效了，重试导致重复触发怎么办？第五，HTTPS证书过期了DS会怎么报错？报错信息友好吗？"

**小胖**（包子停在半空，愣住了）：

> "这……我就想填个URL，你扯出一串容错问题。这HTTP节点就是个花架子吧？"

**大师**（从工位起身，走到白板前，用马克笔画了几个方块）：

> "小白的每个问题都打在七寸上。我打个比方——HTTP节点就是DS的**万能电源插座**。你家客厅墙上那个三孔插座，插电视机能用，插落地灯能用，插吸尘器也能用——它不关心电器是什么，只提供标准的220V输出。HTTP节点同理：Jenkins是一台'电器'，天气API是一台'电器'，ML平台也是一台'电器'，它们插到同一个HTTP插座上，DS负责供电和通断控制。"

> "小胖说的没错，HTTP节点本体确实简单——选方法、填URL、配参数。但小白关心的那些问题，答案全在'插座的安全规范'里。先说token过期：DS支持用`${variable}`占位符做动态参数注入——你可以在上游节点里先调一次认证接口拿到token，用setVar输出，下游HTTP节点在Header里写`Authorization: Bearer ${access_token}`就行。这就是'先打电话确认身份，再插电'。再说body大小：HTTP节点底层用的是Apache HttpClient，body理论上没有硬上限，但Worker的JVM堆内存决定了实际边界——一条body占几百MB显然不合理。大数据传输应该走文件，不是HTTP body。"

**技术映射**：HTTP节点封装了Apache HttpClient，支持GET/POST/PUT/DELETE/HEAD/PATCH六种方法。请求配置包括URL、Headers、Body（JSON/form-data/x-www-form-urlencoded/raw text）四种格式。认证方面内置了Basic Auth和Bearer Token配置项，也支持在Headers中自定义任何认证方案。

**小白**（在笔记本上快速记录）：

> "那响应解析呢？我看文档说HTTP节点可以把响应体的字段提取出来作为OUT参数——这是怎么做到的？如果响应是个嵌套JSON，比如`{'data': {'job_id': 123}}`，我能只取`data.job_id`吗？还有，如果API调用成功但业务上返回了一个错误码——比如HTTP 200 OK但body里`{'status': 'error', 'msg': 'model not found'}`——DS能识别出这是业务失败吗？"

**大师**：

> "两个好问题。先说JSON路径提取：DS在HTTP节点的'响应参数'配置里支持类似JsonPath的表达式。你的例子里，填`$.data.job_id`就能精确取出嵌套字段，把它映射到一个自定义参数名比如`training_job_id`，这个参数就变成OUT参数，下游节点直接用`${training_job_id}`引用。再说业务状态码：DS默认只检查HTTP状态码——200~299算成功，其他算失败。但你说的情况——200 OK但业务报错，需要用到'响应检查条件'功能：你可以配置一条规则，检查`${response_body.status}`是否等于`'success'`，不满足就标记为失败，走告警分支。"

**技术映射**：响应参数提取支持`httpParametersType`取值为`BODY`模式，value字段填写JsonPath表达式（如`$.training_job_id`、`$.data.items[0].name`）。响应检查条件可配置`STATUS_CODE_DEFAULT`（仅检查HTTP状态码）或自定义条件（检查响应体字段）。

**小胖**（终于找到机会插嘴）：

> "那这几个外部系统——Jenkins、天气API、ML平台——能串成一条工作流吗？天气数据拿出来之后训练模型，模型训练完了自动部署，部署好了通知群里？这不就是把老张的手工活全自动化了嘛！"

**大师**：

> "正是。这就是HTTP节点最核心的价值——**外部系统的调度编排**。你想想，以前老张那套操作，每一步之间全靠他的记忆和责任感维系。现在是DAG来维系：天气API是第一个HTTP节点，ML训练触发是第二个HTTP节点（用天气数据做输入），训练状态轮询是第三个HTTP节点（配好重试和超时），模型部署是第四个，最后Slack通知收尾。失败时自动切告警分支，不需要人盯着。这就是'万能插座'的威力——它能把任何提供REST API的系统变成DS工作流中的一个标准步骤。"

---

## 3. 项目实战

### 环境准备

- DolphinScheduler 3.x 集群已部署运行
- 至少一个Worker节点在线
- 一个测试用Jenkins实例或可mock的HTTP服务
- 一个OpenWeatherMap API Key（免费注册即可获取）
- 工作流项目名称：`http_integration_demo`

### Step 1：调用天气API（GET请求）

**目标**：从OpenWeatherMap获取北京实时天气数据，为销售预测模型提供天气特征。

创建HTTP任务节点，命名为`fetch_weather`：

| 配置项 | 值 |
|--------|-----|
| 请求方法 | GET |
| 请求地址 | `https://api.openweathermap.org/data/2.5/weather?q=Beijing&appid=${WEATHER_API_KEY}&units=metric` |
| 请求头 | `Accept: application/json` |
| 连接超时 | 10000ms |
| 读取超时 | 30000ms |

在「自定义参数」中定义WORKFLOW级别参数`WEATHER_API_KEY`（或设为全局参数避免硬编码）。

> **要点**：`${WEATHER_API_KEY}`是动态参数占位符，运行时从DS全局参数或工作流传参中读取。API Key不应写在URL明文里，而是引用变量，实现配置与代码分离。

### Step 2：触发Jenkins构建（POST请求）

**目标**：数据分区就绪后，触发Jenkins执行数据管道构建（fire-and-forget模式）。

创建HTTP任务节点，命名为`trigger_jenkins_build`：

| 配置项 | 值 |
|--------|-----|
| 请求方法 | POST |
| 请求地址 | `https://jenkins.damai.com/job/data-pipeline/buildWithParameters` |
| 认证方式 | Basic Auth |
| 用户名 | `jenkins_user` |
| 密码 | `***` |
| 请求体格式 | FORM-DATA |

请求体参数：

| 参数名 | 值 |
|--------|-----|
| token | `my-trigger-token` |
| BIZ_DATE | `${biz_date}` |
| ENV | `production` |

连接超时和读取超时均设为5000ms——Jenkins的`buildWithParameters`接口是异步的，收到201即表示构建已入队，不需要等待构建完成，属于"发后即忘"模式。

> **注意事项**：Basic Auth的密码在DS控制台的传输和存储中应走HTTPS加密。生产环境建议使用Jenkins的API Token替代真实密码，并定期轮换。

### Step 3：调用ML平台触发训练（POST with JSON body）

**目标**：将天气数据和日期传递给ML平台的训练接口，启动销售预测模型训练。

创建HTTP任务节点，命名为`trigger_training`。请求body选择JSON格式：

```json
{
  "model_name": "sales_predictor_v2",
  "training_date": "${biz_date}",
  "features": [
    "order_count_7d",
    "avg_order_amount_7d",
    "weather_temp",
    "is_holiday"
  ],
  "callback_url": "http://ds-api:12345/dolphinscheduler/projects/ml_project/callback"
}
```

> **说明**：`${biz_date}`是DS内置的系统变量，表示工作流实例的业务日期，格式为`yyyy-MM-dd`。`callback_url`是训练完成后ML平台回调DS的回调地址——这是一个高级模式，让DS不仅"推"请求，还能"收"回调，实现双向集成。

### Step 4：捕获训练任务ID并传递给下游

**目标**：ML平台在HTTP 201响应中返回`{"training_job_id": "job_20250507_001", "status": "QUEUED"}`，从中提取`training_job_id`和`status`供下游使用。

在`trigger_training`节点的「响应参数」标签页中配置：

| 参数名 | 来源类型 | 表达式 |
|--------|---------|--------|
| training_job_id | BODY | `$.training_job_id` |
| status | BODY | `$.status` |

同时在「自定义参数」中将这两个参数声明为OUT方向，下游任务即可通过`${training_job_id}`和`${status}`引用。

> **原理**：DS Worker在收到HTTP响应后，使用JsonPath解析响应体，按表达式取值并注入到工作流参数上下文中，参数生命周期覆盖该工作流实例的后续所有节点。

### Step 5：轮询ML训练状态（长轮询模式）

**目标**：ML训练通常耗时几分钟到几小时不等，需要周期性地查询训练状态，而非阻塞等待。

创建HTTP任务节点，命名为`poll_training_status`：

| 配置项 | 值 |
|--------|-----|
| 请求方法 | GET |
| 请求地址 | `https://ml-platform.damai.com/api/jobs/${training_job_id}/status` |
| 请求头 | `Authorization: Bearer ${access_token}` |

在HTTP节点的「响应检查条件」中配置自定义条件：
- 条件表达式：`${response_body.status}` 等于 `COMPLETED`

同时配置该节点的任务重试策略：
- 重试次数：12次
- 重试间隔：300000ms（5分钟）
- 总超时窗口：1小时

> **逻辑**：如果状态不是COMPLETED，节点标记为失败，触发重试——5分钟后再查一次。最多重试12次（1小时）。如果1小时后仍未完成，走告警分支。

### Step 6：条件分支处理训练结果

在`poll_training_status`节点后添加Conditions节点，命名为`check_result`：

| 条件 | 运算符 | 比较值 |
|------|--------|--------|
| `${status}` | = | `COMPLETED` |

- **成功分支**标签：`模型部署`——走向下游模型部署HTTP节点
- **失败分支**标签：`训练告警`——走向告警通知节点

在条件节点之后分别创建对应下游任务。

### Step 7：完整工作流DAG拓扑

```
Shell "prepare_features" 
  → HTTP "fetch_weather" (GET 天气API)
  → HTTP "trigger_training" (POST 触发训练，捕获job_id)
  → HTTP "poll_training_status" (GET 轮询，最多重试12次)
  → Conditions "check_result"
      → SUCCESS分支: HTTP "deploy_model" (POST /api/models/deploy)
      → FAILURE分支: Alert "training_failed" (微信 + 邮件)
  → HTTP "notify_slack" (POST 到内部即时通讯webhook)
```

> **提示**：Alert节点和HTTP节点可以并存于同一工作流。Alert通知是DS内置告警渠道的出口，HTTP通知（如Slack webhook）提供了更大的灵活性——支持自定义消息体、@指定成员、附加Markdown格式等。

### Step 8：常见认证模式配置实战

不同外部系统采用不同的认证机制，HTTP节点对应配置如下：

| 认证模式 | 配置方式 | 示例 |
|----------|---------|------|
| API Key in Query | URL中拼接`?api_key=${API_KEY}` | `https://api.service.com/data?key=${SECRET_KEY}` |
| API Key in Header | 自定义Header | `X-API-Key: ${API_KEY}` |
| Bearer Token | Header或内置Bearer配置 | `Authorization: Bearer ${JWT_TOKEN}` |
| Basic Auth | 内置用户名/密码字段 | 用户名/密码填写在DS UI的认证区域 |
| OAuth2 Client Credentials | 两步法 | Step1: POST `/oauth/token` → 捕获`access_token`；Step2: 下游HTTP节点Header中引用`${access_token}` |

> **OAuth2关键细节**：access_token通常有过期时间（如3600秒）。如果工作流总运行时长可能超过token有效期，应考虑在长时间运行的工作流中插入token刷新节点，或使用DS的全局参数+定时刷新机制。

### Step 9：健康检查前置节点

**目标**：在关键外部调用之前，快速验证目标服务是否可达。

创建HTTP任务节点，命名为`health_check_ml`：

| 配置项 | 值 |
|--------|-----|
| 请求方法 | GET |
| 请求地址 | `https://ml-platform.damai.com/health` |
| 连接超时 | 5000ms |
| 读取超时 | 5000ms |
| 响应检查 | STATUS_CODE_DEFAULT |

该节点放置在实际训练触发节点之前。如果健康检查返回非200，工作流直接通过失败分支触发告警——在源头发现故障，避免下游多个节点浪费资源和时间。

### Step 10：响应体作为数据源的边界处理

**场景**：第三方合作伙伴API返回的是一个JSON数组（如`[{order_id: 1, ...}, ...]`），需要传递给下游Shell任务做数据处理。

方案：在下游Shell任务中，将上游HTTP节点的响应体作为参数注入后落地为文件处理：

```bash
#!/bin/bash
echo "${RESPONSE_BODY}" > /tmp/external_sales_${biz_date}.json
python process_external_sales.py /tmp/external_sales_${biz_date}.json
```

> **重要限制**：HTTP节点的响应体默认存储在DS元数据库的任务日志中，不适合大数据量传输（建议控制在1MB以内）。如果需要传输大量数据，应采用以下替代方案：(1) 让外部系统将数据写到共享文件系统（HDFS/S3/NFS），HTTP节点只传递文件路径；(2) 使用DS的Shell节点直接调用curl并将响应写入文件，绕过HTTP节点的body存储限制。

---

### 常见踩坑清单

| 陷阱 | 现象 | 根因与解决 |
|------|------|-----------|
| **SSL证书异常** | HTTP节点报`PKIX path building failed` | 目标服务器HTTPS证书不受JDK cacerts信任。解决：将证书导入Worker的JVM truststore，或添加JVM参数`-Djsse.enableSNIExtension=false`（不推荐生产环境） |
| **响应体过大** | Worker节点OOM或任务日志截断 | HTTP节点将响应体完整加载到内存中。响应超过10MB时考虑改用Shell节点curl方案，或让API分页返回 |
| **POST非幂等重试** | 重试导致Jenkins构建被触发多次 | `buildWithParameters`等POST接口通常非幂等。解决方案：(1) 将自动重试次数设为0；(2) 在Jenkins端做幂等校验（如基于BIZ_DATE去重） |
| **Token过期导致下游链失败** | 第一个HTTP节点成功，但20分钟后第二个HTTP节点报401 | Token默认有效期短于工作流总运行时长。解决：预估最大运行时长，使用Session级别的token或插入refresh_token节点 |
| **字符编码乱码** | 响应体中文显示为`???`或乱码 | 服务器返回的`Content-Type`未声明charset。解决：HTTP节点Content-Type中显式指定`application/json; charset=utf-8` |
| **连接超时与任务超时混淆** | 配置了10分钟任务超时，但HTTP节点30秒就失败了 | 连接超时（connectTimeout）和读取超时（readTimeout）是HTTP客户端层面的超时，独立于DS的任务执行超时。需分别配置 |

---

## 4. 项目总结

### HTTP节点 vs 其他外部集成方案

| 对比维度 | DS HTTP节点 | 内置告警渠道 | Shell节点curl | 自定义插件 |
|----------|------------|-------------|--------------|-----------|
| 开发成本 | 零代码，UI配置 | 零代码 | 需编写Shell脚本 | 需Java开发 |
| 响应解析 | 内置JsonPath提取为参数 | 不适用 | 需手动用jq解析并setVar | 自定义逻辑 |
| 认证支持 | 内置Basic Auth/Bearer/Custom Header | 内置邮件/微信/钉钉等 | 需手动编码认证逻辑 | 自定义实现 |
| 可维护性 | UI配置即文档 | 高 | Shell脚本散落各处 | 需维护代码仓库 |
| 适用场景 | 通用REST API集成 | 事件告警通知 | 复杂HTTP逻辑/大文件传输 | 高频专用场景 |

### 典型适用场景

1. **触发CI/CD流水线**——如数据产出后自动触发Jenkins/GitHub Actions构建镜像
2. **调用外部数据API**——如天气、汇率、行业指数等第三方数据源的定时拉取
3. **编排微服务**——调度器作为服务编排引擎，按DAG顺序调用多个微服务API
4. **自定义通知推送**——向Slack/企业微信/飞书机器人发送自定义格式消息（超出内置告警模板的能力）
5. **依赖系统健康检查**——在跑批之前快速探测外部依赖的可用性

### 不适用场景

1. **大数据量传输**——HTTP节点body大小受Worker内存限制，不适合传输GB级文件，应使用共享存储+HDFS/Shell方案
2. **需要双向流式通信**——如WebSocket、gRPC streaming，HTTP节点仅支持标准的请求-响应模式，长连接场景应使用Shell节点+专用客户端

### 思考题

1. 如果一个HTTP节点需要同时调用两个不同的外部API（比如天气API和汇率API），而它们之间没有依赖关系，你会如何设计？是将它们拆成两个独立的HTTP节点并行执行，还是在一个Shell脚本中依次调用？两种方案的优缺点分别是什么？在并行方案中，如何将两个API的返回结果合并给下游使用？

2. 假设ML平台的训练接口支持回调通知（训练完成时主动POST到DS的webhook），替代当前的轮询模式。这种"回调触发式"与"轮询式"各有什么优劣？在DolphinScheduler中实现回调模式需要哪些额外组件（如DS的API接口或事件触发能力）？在防火墙策略严格、DS无法对外暴露端口的企业网络中，哪种模式更适用？

---

> **本章完成时间建议**：阅读30分钟 + 动手实践60分钟
> **本章关键词**：HTTP任务节点、REST API集成、动态参数注入、响应解析、JsonPath、认证策略、fire-and-forget、长轮询、幂等性、外部系统编排

---

*下一章预告：我们将聚焦DolphinScheduler的告警系统与通知渠道配置，学习如何搭建多维度的任务监控与预警体系。*
