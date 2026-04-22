# 第21章：EmbedServer 与 ExecutorBiz——执行器如何接收并处理调度指令

## 1. 项目背景

Admin 触发任务后，指令会进入 Executor 端。很多人以为“执行器就是一个注解方法”，但中间还有一条完整的通信链：内嵌服务端接收请求、协议解码、业务分发到 `ExecutorBiz`、再由线程模型执行具体 JobHandler。  

当线上出现“Admin 显示触发成功但执行器没跑”时，问题大概率就在这条链上：网络连通、鉴权 accessToken、协议兼容、JobHandler 注册、线程繁忙、返回码语义都可能出错。  

本章聚焦以下源码路径（文本路径）：
- `xxl-job-core/src/main/java/com/xxl/job/core/server/EmbedServer.java`
- `xxl-job-core/src/main/java/com/xxl/job/core/biz/impl/ExecutorBizImpl.java`
- `xxl-job-core/src/main/java/com/xxl/job/core/executor/XxlJobExecutor.java`
- `xxl-job-core/src/main/java/com/xxl/job/core/handler/IJobHandler.java`

目标：看懂“触发请求进入 Executor 后”到底经历了哪些环节，并做一组可复现联调验证。

---

## 2. 项目设计（剧本式交锋对话）

### 第一轮：谁在监听调度请求

**小胖**：执行器不是 Spring Boot 启动就行吗，怎么还冒出个 EmbedServer？  
**小白**：Spring Boot 是业务进程，XXL-JOB 还要有自己的通信入口来接收 Admin 指令。  
**大师**：`EmbedServer` 就是 Executor 内嵌的通信服务，负责暴露触发、心跳、日志查询等接口。  
**技术映射**：应用进程是商场，EmbedServer 是配送收货口。  

### 第二轮：ExecutorBiz 在做什么

**小胖**：请求到了就直接反射执行 handler？  
**小白**：还要做 accessToken 校验、参数解析、任务线程路由和运行态判断。  
**大师**：`ExecutorBizImpl` 是执行器入口门面，统一处理触发协议并把任务交给执行线程。  
**技术映射**：ExecutorBiz 是总调度台，不是单纯转发器。  

### 第三轮：为什么 Admin 触发成功但任务没跑

**小胖**：界面都显示成功了，怎么会没执行？  
**小白**：可能是“请求送达成功”，但“执行排队或拒绝”。  
**大师**：看返回码语义和执行日志。触发成功不等于业务成功，必须关联 Executor 侧日志和 job log 明细。  
**技术映射**：链路成功分“传输成功”和“业务成功”两层。  

### 第四轮：鉴权问题怎么查

**小胖**：我本地能跑，线上偶发 401。  
**小白**：先看 Admin 与 Executor 的 accessToken 是否一致，再看环境变量覆盖。  
**大师**：把配置来源梳理清楚：配置文件、启动参数、容器注入，避免“看起来一致，实际生效值不同”。  
**技术映射**：鉴权问题常是配置漂移，不是代码 bug。  

---

## 3. 项目实战

### 3.1 环境准备

- 启动 Admin 与一个 Executor 样例应用  
- 确认 `xxl.job.accessToken` 在两端一致（即 accessToken 一致）  
- 在 Executor 端准备一个简单 `@XxlJob` JobHandler  

### 3.2 可运行联调步骤

**步骤目标 1：验证请求可达**  
在 Admin 创建并触发任务。  
验证点：Executor 日志出现接收触发请求与 JobHandler 执行日志。

**步骤目标 2：验证鉴权失败路径**  
故意把 Executor accessToken 改错并重启。  
验证点：Admin 触发失败，日志出现鉴权相关错误；恢复 accessToken 后可正常触发。

**步骤目标 3：验证 handler 不存在路径**  
在任务配置中填写不存在的 JobHandler 名称。  
验证点：Executor 返回 handler not found 类错误，Admin 任务日志可见失败原因。

### 3.3 启动命令示例

```bash
mvn -pl xxl-job-admin spring-boot:run "-Dspring-boot.run.arguments=--server.port=8080 --spring.datasource.url=jdbc:mysql://127.0.0.1:3306/xxl_job?useUnicode=true&characterEncoding=UTF-8&autoReconnect=true&serverTimezone=Asia/Shanghai"
```

```bash
mvn -pl xxl-job-executor-samples/xxl-job-executor-sample-springboot spring-boot:run "-Dspring-boot.run.arguments=--server.port=9999 --xxl.job.executor.appname=xxl-job-executor-sample --xxl.job.accessToken=default_token"
```

### 3.4 常见坑与处理

- 坑 1：以为是网络问题，实际是 accessToken 不一致  
  - 处理：先对齐配置，再排查网络。  
- 坑 2：handler 名称改了但管理台没同步  
  - 处理：统一命名规范并在变更时同步更新任务配置。  
- 坑 3：只看 Admin 日志忽略 Executor 日志  
  - 处理：联调必须双端日志对照。  

### 3.5 关键类职责与调用链（重点）

- `EmbedServer`：执行器内嵌通信服务，负责接收 Admin 发来的触发、心跳、日志查询等请求。  
- `ExecutorBizImpl`：执行器业务门面，处理鉴权、参数校验、触发分发、执行结果返回。  
- `XxlJobExecutor`：执行器生命周期管理器，负责 JobHandler 注册、线程管理、启动与销毁。  
- `IJobHandler` / 自定义 `@XxlJob` 方法：业务执行单元，真正承载任务逻辑。

触发链路：  
`Admin XxlJobTrigger -> HTTP(Remoting) -> EmbedServer -> ExecutorBizImpl.run -> JobThread -> JobHandler.execute`

回传链路：  
`IJobHandler result -> ExecutorBizImpl return -> Admin callback/completer -> xxl_job_log update`

### 3.6 源码路径映射表

| 组件 | 源码路径 | 作用 |
| --- | --- | --- |
| 内嵌服务端 | `xxl-job-core/src/main/java/com/xxl/job/core/server/EmbedServer.java` | 接收调度端请求 |
| 执行器业务实现 | `xxl-job-core/src/main/java/com/xxl/job/core/biz/impl/ExecutorBizImpl.java` | 请求分发与执行控制 |
| 执行器核心 | `xxl-job-core/src/main/java/com/xxl/job/core/executor/XxlJobExecutor.java` | 生命周期与 JobHandler 注册 |
| 任务处理抽象 | `xxl-job-core/src/main/java/com/xxl/job/core/handler/IJobHandler.java` | 业务任务统一执行接口 |
| 任务线程模型 | `xxl-job-core/src/main/java/com/xxl/job/core/thread/JobThread.java` | 任务执行线程与队列 |

### 3.7 验证命令块（最小可验证）

```bash
mvn -pl xxl-job-admin spring-boot:run "-Dspring-boot.run.arguments=--server.port=8080 --spring.datasource.url=jdbc:mysql://127.0.0.1:3306/xxl_job?useUnicode=true&characterEncoding=UTF-8&autoReconnect=true&serverTimezone=Asia/Shanghai"
```

```bash
mvn -pl xxl-job-executor-samples/xxl-job-executor-sample-springboot spring-boot:run "-Dspring-boot.run.arguments=--server.port=9999 --xxl.job.executor.appname=xxl-job-executor-sample --xxl.job.accessToken=default_token"
```

```bash
# 查询最近触发记录，验证执行器地址与处理码
mysql -h127.0.0.1 -uroot -p -e "select id,job_id,executor_address,trigger_code,handle_code from xxl_job_log order by id desc limit 10;" xxl_job
```

```bash
# 验证执行器注册在线
mysql -h127.0.0.1 -uroot -p -e "select registry_value,update_time from xxl_job_registry order by id desc limit 10;" xxl_job
```

### 3.8 故障复盘表格

| 现象 | 根因定位 | 应急处理 | 长期方案 |
| --- | --- | --- | --- |
| Admin 显示触发成功但无执行 | handler 名称错误或线程拒绝 | 修正 handler 并重触发 | 引入配置校验与发布检查 |
| 偶发 401 鉴权失败 | accessToken 配置漂移 | 对齐 accessToken 并重启节点 | 配置中心统一管理与启动自检 |
| 执行器在线但任务延迟高 | 任务线程拥塞 | 临时降频、限流 | 按任务类型拆分执行器组 |

---

## 4. 项目总结

本章把 Executor 端核心入口串起来了：`EmbedServer` 负责接收，`ExecutorBizImpl` 负责分发，`XxlJobExecutor` 负责生命周期和 JobHandler 注册。当你再遇到“触发成功但不执行”问题，可以按“通信 -> 鉴权 -> JobHandler -> 执行线程”顺序定位。  

这是你从“会用平台”走向“会诊断平台”的关键一步。下一章我们继续深入执行线程模型，弄清楚任务真正运行时如何创建、复用与终止线程。  

思考题：
1) 如果要支持自定义鉴权头，改动点主要在哪些类？  
2) 你会如何设计一个健康检查，提前发现 Executor 无法处理触发请求？  

## 本章一页纸速记

### 3 个结论
- Admin 到 Executor 的触发成功，只代表链路送达成功，不等于业务执行成功。
- accessToken、JobHandler 与线程模型是定位触发异常的三条主线。
- 联调必须双端对照：Admin 日志、Executor 日志与 `xxl_job` 库日志缺一不可。

### 5 项清单
- 启动参数固定：Admin `8080`、Executor `9999`、数据库 `xxl_job`、AppName `xxl-job-executor-sample`。
- 核对术语与配置：XXL-JOB、Admin、Executor、JobHandler、accessToken 全链路一致。
- 验证链路：触发请求、鉴权校验、JobHandler 路由、回调落库逐步确认。
- 固化排障证据：保留源码映射表、验证命令块、故障复盘表格。

### 3 个误区
- 误区一：Admin 上“触发成功”就代表任务执行成功。
- 误区二：只看网络连通，不看 accessToken 与 JobHandler 配置一致性。
- 误区三：只看单端日志，不做 Admin/Executor/数据库三方对账。
