# JMeter学习专栏大纲（36章）

## 说明
- 本目录按每章一个Markdown文件组织。
- 每章包含固定四段：项目背景、项目设计、项目实战、项目总结。
- 每章建议字数：3000-5000字。

## 基础篇
- 第1章：负载测试为什么总“测不准”：JMeter在性能工程中的定位（chapters/01-负载测试为什么总“测不准”：JMeter在性能工程中的定位.md）
- 第2章：10分钟跑通第一个压测：安装、目录结构、GUI与CLI（chapters/02-10分钟跑通第一个压测：安装、目录结构、GUI与CLI.md）
- 第3章：Test Plan骨架：线程组、Sampler、Listener的最小闭环（chapters/03-TestPlan骨架：线程组、Sampler、Listener的最小闭环.md）
- 第4章：HTTP接口压测入门：请求方法、参数化、断言（chapters/04-HTTP接口压测入门：请求方法、参数化、断言.md）
- 第5章：登录态与会话保持：Cookie、Header、关联参数提取（chapters/05-登录态与会话保持：Cookie、Header、关联参数提取.md）
- 第6章：数据驱动测试：CSV Data Set Config与变量作用域（chapters/06-数据驱动测试：CSVDataSetConfig与变量作用域.md）
- 第7章：控制器实战：Loop/If/While/Transaction组织复杂流程（chapters/07-控制器实战：Loop-If-While-Transaction组织复杂流程.md）
- 第8章：定时器与节奏控制：吞吐、并发、Think Time的区别（chapters/08-定时器与节奏控制：吞吐、并发、ThinkTime的区别.md）
- 第9章：结果怎么看：聚合报告、响应时间分布、错误率（chapters/09-结果怎么看：聚合报告、响应时间分布、错误率.md）
- 第10章：从GUI到CLI：无界面压测脚本化与基础自动化（chapters/10-从GUI到CLI：无界面压测脚本化与基础自动化.md）
- 第11章：典型业务场景一：电商下单链路压测（登录-加购-下单）（chapters/11-典型业务场景一：电商下单链路压测（登录-加购-下单）.md）
- 第12章：基础篇收官：常见误区、压测口径、团队协作最小规范（chapters/12-基础篇收官：常见误区、压测口径、团队协作最小规范.md）

## 中级篇
- 第13章：性能模型建立：并发用户、到达率、SLA与容量目标（chapters/13-性能模型建立：并发用户、到达率、SLA与容量目标.md）
- 第14章：分层压测设计：单接口、业务流、全链路如何配合（chapters/14-分层压测设计：单接口、业务流、全链路如何配合.md）
- 第15章：分布式压测实战：Remote Testing架构与节点治理（chapters/15-分布式压测实战：RemoteTesting架构与节点治理.md）
- 第16章：连接与资源瓶颈：连接池、端口、线程、DNS缓存（chapters/16-连接与资源瓶颈：连接池、端口、线程、DNS缓存.md）
- 第17章：关联提取进阶：正则/JSON/XPath边界与稳定性设计（chapters/17-关联提取进阶：正则-JSON-XPath边界与稳定性设计.md）
- 第18章：JDBC压测专题：连接管理、事务、慢SQL与回滚策略（chapters/18-JDBC压测专题：连接管理、事务、慢SQL与回滚策略.md）
- 第19章：协议扩展视角：FTP/JMS/TCP采样器选型与场景（chapters/19-协议扩展视角：FTP-JMS-TCP采样器选型与场景.md）
- 第20章：报告工程化：Dashboard生成、指标解释、对比基线（chapters/20-报告工程化：Dashboard生成、指标解释、对比基线.md）
- 第21章：可观测性打通：JMeter指标与APM/日志/主机监控联动（chapters/21-可观测性打通：JMeter指标与APM-日志-主机监控联动.md）
- 第22章：故障注入与鲁棒性：超时、重试、熔断场景验证（chapters/22-故障注入与鲁棒性：超时、重试、熔断场景验证.md）
- 第23章：CI/CD集成：在流水线中自动执行与阈值门禁（chapters/23-CI-CD集成：在流水线中自动执行与阈值门禁.md）
- 第24章：中级篇收官：压测平台化雏形（脚本规范+任务编排）（chapters/24-中级篇收官：压测平台化雏形（脚本规范+任务编排）.md）

## 高级篇
- 第25章：源码导航总图：core/components/protocol模块职责（chapters/25-源码导航总图：core-components-protocol模块职责.md）
- 第26章：执行引擎原理：线程模型、调度与采样执行链（chapters/26-执行引擎原理：线程模型、调度与采样执行链.md）
- 第27章：Test Element体系：属性模型、作用域与序列化（chapters/27-TestElement体系：属性模型、作用域与序列化.md）
- 第28章：HTTP采样器源码剖析：请求构建、连接复用、重定向（chapters/28-HTTP采样器源码剖析：请求构建、连接复用、重定向.md）
- 第29章：控制器与流程编排源码：逻辑分支与事务边界（chapters/29-控制器与流程编排源码：逻辑分支与事务边界.md）
- 第30章：后置处理与函数引擎：提取器、函数解析、变量生命周期（chapters/30-后置处理与函数引擎：提取器、函数解析、变量生命周期.md）
- 第31章：报告与监听器源码：样本采集、聚合计算、图表生成（chapters/31-报告与监听器源码：样本采集、聚合计算、图表生成.md）
- 第32章：Open Model Thread Group：开环压力模型与到达率控制（chapters/32-OpenModelThreadGroup：开环压力模型与到达率控制.md）
- 第33章：自定义扩展一：开发自定义Sampler/Processor/Assertion（chapters/33-自定义扩展一：开发自定义Sampler-Processor-Assertion.md）
- 第34章：自定义扩展二：插件打包、发布、版本兼容策略（chapters/34-自定义扩展二：插件打包、发布、版本兼容策略.md）
- 第35章：极限场景优化：百万请求级别下的资源与稳定性治理（chapters/35-极限场景优化：百万请求级别下的资源与稳定性治理.md）
- 第36章：SRE落地终章：容量评估、性能回归体系、生产守护机制（chapters/36-SRE落地终章：容量评估、性能回归体系、生产守护机制.md）


