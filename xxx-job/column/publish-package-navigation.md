# XXL-JOB 专栏发布打包版（导航总览）

## 专栏信息

- 专栏名：`XXL-JOB 学习专栏（38章）`
- 读者对象：开发、测试、运维、架构师
- 内容结构：基础篇（1~16）+ 中级篇（17~30）+ 高级篇（31~38）
- 章节规格：每章 3000~5000 字，统一四段结构，含源码映射、验证命令、故障复盘、一页纸速记

## 发布建议

- 发布节奏：每周 2 章（工作日 1 章 + 周末 1 章复盘）
- 发布形式：正文 + 章节配图 + 命令清单 + 练习作业
- 推荐栏目结构：`引子 -> 正文 -> 实战 -> 速记 -> 作业 -> 预告`

## 章节导航（可直接用于目录页）

### 基础篇（1~16）

1. [01 术语地图与工作原理](./01-xxl-job-terms-and-principles.md)  
2. [02 为什么需要分布式任务调度](./02-why-distributed-scheduling.md)  
3. [03 本地搭建最小闭环](./03-local-setup-minimal-loop.md)  
4. [04 管理台总览](./04-admin-console-overview.md)  
5. [05 调度类型：Cron/FixRate/一次性](./05-schedule-types-cron-fixrate-once.md)  
6. [06 第一个 JobHandler（BEAN）](./06-first-jobhandler-bean-mode.md)  
7. [07 GLUE 模式全景](./07-glue-mode-overview.md)  
8. [08 参数、分片与上下文](./08-job-param-sharding-context.md)  
9. [09 路由策略入门](./09-route-strategy-basics.md)  
10. [10 阻塞策略与并发控制](./10-block-strategy-concurrency-control.md)  
11. [11 失败重试、超时与告警](./11-retry-timeout-alert-basics.md)  
12. [12 日志体系入门](./12-log-system-basics.md)  
13. [13 启动故障排查](./13-startup-troubleshooting.md)  
14. [14 任务正确性测试](./14-testing-for-job-correctness.md)  
15. [15 运维部署规范](./15-ops-deployment-standards.md)  
16. [16 综合实战 I：订单超时关单](./16-comprehensive-practice-order-timeout-close.md)  

### 中级篇（17~30）

17. [17 调度核心流程全景](./17-scheduler-core-flow.md)  
18. [18 Controller/Service/Mapper 源码走读](./18-sourcewalk-controller-service-mapper.md)  
19. [19 JobScheduleHelper 深入](./19-jobschedulehelper-deepdive.md)  
20. [20 JobTrigger 与触发线程池](./20-jobtrigger-triggerpool.md)  
21. [21 EmbedServer 与 ExecutorBiz](./21-embedserver-executorbiz.md)  
22. [22 JobThread 执行模型](./22-jobthread-execution-model.md)  
23. [23 注册发现与心跳机制](./23-registry-discovery-heartbeat.md)  
24. [24 回调闭环](./24-callback-closure.md)  
25. [25 数据模型与索引优化](./25-data-model-index-optimization.md)  
26. [26 性能调优](./26-performance-tuning.md)  
27. [27 高可用部署与演练](./27-ha-deployment-drill.md)  
28. [28 可观测性增强](./28-observability-enhancement.md)  
29. [29 安全治理](./29-security-governance.md)  
30. [30 综合实战 II：对账任务集群化](./30-practice-ii-reconciliation-cluster.md)  

### 高级篇（31~38）

31. [31 Bootstrap 启动总装配](./31-bootstrap-source-assembly.md)  
32. [32 调度一致性与锁机制](./32-scheduling-consistency-lock.md)  
33. [33 路由算法深挖](./33-routing-algorithms-deepdive.md)  
34. [34 任务依赖与编排](./34-job-dependency-orchestration.md)  
35. [35 自定义扩展点](./35-custom-extension-points.md)  
36. [36 极端场景与韧性治理](./36-extreme-scenarios-resilience.md)  
37. [37 SRE 落地](./37-sre-implementation.md)  
38. [38 综合实战 III：企业级改造](./38-practice-iii-enterprise-transformation.md)  

## 打包附件（配套阅读）

- [分级阅读顺序与练习路线](./appendix-reading-order-and-practice-routes.md)
- [周更连载发布表](./publish-package-weekly-release.md)
- [读者作业清单（按章节）](./publish-package-reader-homework.md)
