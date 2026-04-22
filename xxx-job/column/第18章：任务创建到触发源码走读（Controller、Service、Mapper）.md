# 第18章：源码走读——Controller 到 Service 到 Mapper 的请求闭环

## 1. 项目背景

你在管理台点一次“新增任务”，页面立刻提示成功，但真正发生了什么？很多同学停留在“接口调用成功”层面，却不知道这次操作穿过了多少层：Web 请求解析、参数校验、权限判断、事务提交、SQL 落库、返回统一结果。  

如果不把这条链路走清楚，后续做改造时很容易踩坑：比如在 Controller 里写了业务逻辑导致不可测；在 Service 中遗漏权限校验导致越权；在 Mapper 层 SQL 条件写错导致全量更新。  

本章以“新增任务”场景为主线，带你做一条可复现、可调试、可验证的源码走读路径，重点聚焦以下路径（文本路径引用）：
- `xxl-job-admin/src/main/java/com/xxl/job/admin/controller/biz/JobInfoController.java`
- `xxl-job-admin/src/main/java/com/xxl/job/admin/service/impl/XxlJobServiceImpl.java`
- `xxl-job-admin/src/main/resources/mybatis-mapper/XxlJobInfoMapper.xml`
- `xxl-job-admin/src/main/java/com/xxl/job/admin/mapper/XxlJobInfoMapper.java`

目标不是“读完代码”，而是建立你自己的排障地图：任何任务创建、修改、启动异常，都能按层快速定位。

---

## 2. 项目设计（剧本式交锋对话）

### 第一轮：从入口开始

**小胖**：我在页面点“保存”不就发个 HTTP 请求吗？看 Controller 就够了吧？  
**小白**：Controller 只能看到入口，真正的规则在 Service。比如 Cron 校验、路由策略、重试次数合法性。  
**大师**：先定走读顺序：Controller 看参数和路由；Service 看业务编排和事务边界；Mapper 看 SQL 条件和字段映射。  
**技术映射**：入口层回答“谁调用”，业务层回答“怎么做”，数据层回答“写到哪”。  

### 第二轮：参数校验与防御

**小胖**：前端都校验了，后端还要再校验？  
**小白**：前端校验不可信，接口可能被脚本直调。  
**大师**：在 `JobInfoController` 做基础校验和错误返回，在 `XxlJobServiceImpl` 做业务校验。双层校验是防御式编程，不是重复劳动。  
**技术映射**：前端校验是体验，后端校验是安全边界。  

### 第三轮：为什么不能把 SQL 直接写 Controller

**小胖**：写快点的话，Controller 里直接调 Mapper 不是更省事？  
**小白**：那事务、复用、测试都乱了，后续改字段要全局找控制器。  
**大师**：Controller 只做协议转换，Service 负责流程，Mapper 负责持久化。解耦后才能做单测、集成测试和灰度发布。  
**技术映射**：短期“快”会换来长期“慢”。  

### 第四轮：异常链路怎么读

**小胖**：报错栈太长，看不懂。  
**小白**：那就按层截断：先看 Controller 返回码，再看 Service 里抛出的业务异常，最后看 Mapper 执行 SQL。  
**大师**：再加一条：对照任务日志和数据库状态，判断是“未执行”还是“执行失败回滚”。  
**技术映射**：异常定位靠“分层断点 + 状态对照”，不是盲猜。  

---

## 3. 项目实战

### 3.1 环境准备

- JDK 17、Maven 3.8+、MySQL 8.x  
- 启动模块：`xxl-job-admin`  
- 调试入口：浏览器操作“新增任务”，或用接口工具调用新增任务接口  

建议先在 IDE 打开以下文件并打断点：
- `JobInfoController#add`
- `XxlJobServiceImpl#add`
- `XxlJobInfoMapper.xml` 中 insert 语句对应方法

### 3.2 分步实现（可运行思路）

**步骤目标 1：复现一次真实新增任务请求**  
1) 启动 admin；2) 登录管理台；3) 在“任务管理”新增一条 demo 任务。  
期望：页面返回成功，列表出现新任务。

**步骤目标 2：按 Controller -> Service -> Mapper 单步调试**  
在 `JobInfoController` 观察入参对象是否完整。  
进入 `XxlJobServiceImpl` 核对关键校验：执行器是否存在、JobHandler 是否为空、Cron 是否有效。  
最后进入 Mapper 看实际入库字段是否和页面一致。

**步骤目标 3：制造一次失败路径并验证返回**  
把 Cron 改成非法表达式后提交。  
期望：Controller 返回失败信息；数据库不新增记录；日志包含业务校验失败描述。

### 3.3 验证命令与观察点

- 页面验证：任务列表是否新增  
- 数据库验证（示例思路）：按 job 描述关键词查询新增记录是否存在  
- 日志验证：查看 admin 日志里是否出现 Service 校验信息  

你可以按下面命令运行 admin（Windows PowerShell）：

```bash
mvn -pl xxl-job-admin spring-boot:run
```

### 3.4 常见坑与解决

- 坑 1：只看 Controller 不看 Service，误判“参数都对”  
  - 解决：必须跟到 `XxlJobServiceImpl` 的业务校验分支。  
- 坑 2：Mapper XML 与接口方法名不一致  
  - 解决：核对 `XxlJobInfoMapper.java` 与 XML 的 statement id。  
- 坑 3：数据库字符集或字段长度不匹配导致插入失败  
  - 解决：对照表结构与实体字段，检查 SQL 异常详细信息。  

### 3.5 关键类职责与调用链（重点）

- `JobInfoController`：负责 HTTP 协议层，做参数接收、基础校验、权限上下文处理与统一返回。  
- `XxlJobServiceImpl`：负责业务规则层，执行执行器存在性校验、调度类型校验、任务字段规范化、事务编排。  
- `XxlJobInfoMapper` / `XxlJobInfoMapper.xml`：负责持久化层，执行 insert/update/select SQL，并将实体字段映射到表结构。  

“新增任务”主调用链：  
`JobInfoController#add -> XxlJobServiceImpl#add -> XxlJobInfoMapper#save -> MyBatis XML insert statement -> xxl_job_info`

“修改任务”主调用链：  
`JobInfoController#update -> XxlJobServiceImpl#update -> XxlJobInfoMapper#update -> SQL update`

### 3.6 源码路径映射表

| 分层 | 源码路径 | 核心职责 |
| --- | --- | --- |
| Controller | `xxl-job-admin/src/main/java/com/xxl/job/admin/controller/biz/JobInfoController.java` | 请求入口与返回出口 |
| Service | `xxl-job-admin/src/main/java/com/xxl/job/admin/service/impl/XxlJobServiceImpl.java` | 业务校验与流程编排 |
| Mapper 接口 | `xxl-job-admin/src/main/java/com/xxl/job/admin/mapper/XxlJobInfoMapper.java` | 数据访问抽象 |
| Mapper SQL | `xxl-job-admin/src/main/resources/mybatis-mapper/XxlJobInfoMapper.xml` | 具体 SQL 语句与字段映射 |
| 数据模型 | `xxl-job-admin/src/main/java/com/xxl/job/admin/model/XxlJobInfo.java` | 任务实体定义 |

### 3.7 验证命令块（最小可验证）

```bash
# 启动 admin
mvn -pl xxl-job-admin spring-boot:run
```

```bash
# 新增任务后，确认任务记录落库
mysql -h127.0.0.1 -uroot -p -e "select id,job_group,executor_handler,schedule_conf from xxl_job_info order by id desc limit 5;" xxl_job
```

```bash
# 触发一次任务后，确认日志记录存在
mysql -h127.0.0.1 -uroot -p -e "select id,job_id,trigger_code,handle_code from xxl_job_log order by id desc limit 5;" xxl_job
```

### 3.8 故障复盘表格

| 故障表现 | 失效层 | 根因 | 修复 |
| --- | --- | --- | --- |
| 页面新增成功但库中无记录 | Service 层 | 业务校验失败被吞掉 | 统一错误码并补日志 |
| 新增任务后触发报 handler 不存在 | 配置层 | `executor_handler` 填写错误 | 控制台输入校验+模板约束 |
| SQL 报字段不存在 | Mapper 层 | XML 与表结构版本不一致 | 升级脚本与版本管理 |

---

## 4. 项目总结

本章建立了“新增任务”请求的完整源码阅读闭环：入口在 `JobInfoController`，规则在 `XxlJobServiceImpl`，落库在 `XxlJobInfoMapper`。你以后排查任务创建类问题，不再需要全局搜索，而是按固定路径推进。  

这套走读方法同样适用于“编辑任务、启动任务、停止任务、删除任务”。下一步建议你把同样的方法迁移到调度链路，理解“任务被创建后，如何被调度线程扫描并触发执行”。  

思考题：
1) 如果要新增一个“业务标签”字段，最少要改哪几层？  
2) 你会把“权限校验”放在 Controller 还是 Service，为什么？  

## 本章一页纸速记

**3条结论**
- 源码走读要遵循分层闭环：Controller 管协议，Service 管规则，Mapper 管持久化。
- 问题定位优先按层截断，能显著降低“全局盲搜”成本。
- 请求链路稳定性的关键是参数校验一致、事务边界清晰、SQL 条件可审计。

**5条落地清单**
- 统一术语：统一使用“入口层、业务层、数据层”命名，不混用职责描述。
- 统一参数：Controller 与 Service 使用同一字段命名，禁止同义不同名。
- 统一命令：固定“启动 Admin -> 页面新增任务 -> SQL 验证入库 -> 触发并查日志”闭环。
- 统一注释：命令块注释写清“动作 + 预期结果”，避免模糊描述。
- 统一审查：新增字段变更必须同步检查 Controller DTO、Service 校验、Mapper XML 三处一致性。

**3条误区**
- 误区一：在 Controller 直接拼装业务逻辑或 SQL，破坏分层边界。
- 误区二：只做前端校验，不做后端防御式校验。
- 误区三：Mapper XML 与接口签名不对齐，导致运行时隐式失败。
