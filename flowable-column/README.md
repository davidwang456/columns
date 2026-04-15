# Flowable 专栏（约 36 章）

本目录为专栏**工作区**：统一模板见 [TEMPLATE.md](TEMPLATE.md)，读者分层见 [AUDIENCE.md](AUDIENCE.md)，依赖版本见 [VERSION_MATRIX.md](VERSION_MATRIX.md)。

**正文**：第 1～36 章均按 [TEMPLATE.md](TEMPLATE.md) 成稿；**项目设计**一节为 **小胖 × 小白 × 大师** 三人对话（开球—追问—收束），便于内训与跨部门共读。

---

## 章节目录与文件命名

文件名与章首 **H1** 一致，格式：**第 n 章：主题——副标题**（`.md`）。为兼容 Windows，路径中不宜出现的半角 `/` 等在文件名里已替换为全角 **`／`**（如 `CI／CD`、`Start／End`）。

| 章 | 文件（点击打开） |
|----|------------------|
| 1 | [第 1 章：认识 BPMN 与 Flowable——为什么需要工作流引擎.md](chapters/%E7%AC%AC%201%20%E7%AB%A0%EF%BC%9A%E8%AE%A4%E8%AF%86%20BPMN%20%E4%B8%8E%20Flowable%E2%80%94%E2%80%94%E4%B8%BA%E4%BB%80%E4%B9%88%E9%9C%80%E8%A6%81%E5%B7%A5%E4%BD%9C%E6%B5%81%E5%BC%95%E6%93%8E.md) |
| 2 | [第 2 章：工程脚手架——Spring Boot + Flowable + 数据库选型.md](chapters/%E7%AC%AC%202%20%E7%AB%A0%EF%BC%9A%E5%B7%A5%E7%A8%8B%E8%84%9A%E6%89%8B%E6%9E%B6%E2%80%94%E2%80%94Spring%20Boot%20%2B%20Flowable%20%2B%20%E6%95%B0%E6%8D%AE%E5%BA%93%E9%80%89%E5%9E%8B.md) |
| 3 | [第 3 章：第一条流程——从 Modeler 到部署运行（含版本）.md](chapters/%E7%AC%AC%203%20%E7%AB%A0%EF%BC%9A%E7%AC%AC%E4%B8%80%E6%9D%A1%E6%B5%81%E7%A8%8B%E2%80%94%E2%80%94%E4%BB%8E%20Modeler%20%E5%88%B0%E9%83%A8%E7%BD%B2%E8%BF%90%E8%A1%8C%EF%BC%88%E5%90%AB%E7%89%88%E6%9C%AC%EF%BC%89.md) |
| 4 | [第 4 章：事件与顺序流——Start／End、SequenceFlow、默认流.md](chapters/%E7%AC%AC%204%20%E7%AB%A0%EF%BC%9A%E4%BA%8B%E4%BB%B6%E4%B8%8E%E9%A1%BA%E5%BA%8F%E6%B5%81%E2%80%94%E2%80%94Start%EF%BC%8FEnd%E3%80%81SequenceFlow%E3%80%81%E9%BB%98%E8%AE%A4%E6%B5%81.md) |
| 5 | [第 5 章：用户任务 I——UserTask、办理、候选人／候选组.md](chapters/%E7%AC%AC%205%20%E7%AB%A0%EF%BC%9A%E7%94%A8%E6%88%B7%E4%BB%BB%E5%8A%A1%20I%E2%80%94%E2%80%94UserTask%E3%80%81%E5%8A%9E%E7%90%86%E3%80%81%E5%80%99%E9%80%89%E4%BA%BA%EF%BC%8F%E5%80%99%E9%80%89%E7%BB%84.md) |
| 6 | [第 6 章：服务任务——JavaDelegate 与 Spring Bean 委托.md](chapters/%E7%AC%AC%206%20%E7%AB%A0%EF%BC%9A%E6%9C%8D%E5%8A%A1%E4%BB%BB%E5%8A%A1%E2%80%94%E2%80%94JavaDelegate%20%E4%B8%8E%20Spring%20Bean%20%E5%A7%94%E6%89%98.md) |
| 7 | [第 7 章：变量体系——全局／局部、JSON／POJO 序列化陷阱.md](chapters/%E7%AC%AC%207%20%E7%AB%A0%EF%BC%9A%E5%8F%98%E9%87%8F%E4%BD%93%E7%B3%BB%E2%80%94%E2%80%94%E5%85%A8%E5%B1%80%EF%BC%8F%E5%B1%80%E9%83%A8%E3%80%81JSON%EF%BC%8FPOJO%20%E5%BA%8F%E5%88%97%E5%8C%96%E9%99%B7%E9%98%B1.md) |
| 8 | [第 8 章：网关入门——ExclusiveGateway（排他）.md](chapters/%E7%AC%AC%208%20%E7%AB%A0%EF%BC%9A%E7%BD%91%E5%85%B3%E5%85%A5%E9%97%A8%E2%80%94%E2%80%94ExclusiveGateway%EF%BC%88%E6%8E%92%E4%BB%96%EF%BC%89.md) |
| 9 | [第 9 章：并行与包容——ParallelGateway、InclusiveGateway.md](chapters/%E7%AC%AC%209%20%E7%AB%A0%EF%BC%9A%E5%B9%B6%E8%A1%8C%E4%B8%8E%E5%8C%85%E5%AE%B9%E2%80%94%E2%80%94ParallelGateway%E3%80%81InclusiveGateway.md) |
| 10 | [第 10 章：History——流程／活动／变量／任务审计怎么查.md](chapters/%E7%AC%AC%2010%20%E7%AB%A0%EF%BC%9AHistory%E2%80%94%E2%80%94%E6%B5%81%E7%A8%8B%EF%BC%8F%E6%B4%BB%E5%8A%A8%EF%BC%8F%E5%8F%98%E9%87%8F%EF%BC%8F%E4%BB%BB%E5%8A%A1%E5%AE%A1%E8%AE%A1%E6%80%8E%E4%B9%88%E6%9F%A5.md) |
| 11 | [第 11 章：REST 集成——引擎 REST 与前后端职责切分.md](chapters/%E7%AC%AC%2011%20%E7%AB%A0%EF%BC%9AREST%20%E9%9B%86%E6%88%90%E2%80%94%E2%80%94%E5%BC%95%E6%93%8E%20REST%20%E4%B8%8E%E5%89%8D%E5%90%8E%E7%AB%AF%E8%81%8C%E8%B4%A3%E5%88%87%E5%88%86.md) |
| 12 | [第 12 章：表达式与 UEL——`${}`、`execution` 与 `task` 里能用什么.md](chapters/%E7%AC%AC%2012%20%E7%AB%A0%EF%BC%9A%E8%A1%A8%E8%BE%BE%E5%BC%8F%E4%B8%8E%20UEL%E2%80%94%E2%80%94%60%24%7B%7D%60%E3%80%81%60execution%60%20%E4%B8%8E%20%60task%60%20%E9%87%8C%E8%83%BD%E7%94%A8%E4%BB%80%E4%B9%88.md) |
| 13 | [第 13 章：ExecutionListener 与 TaskListener——切面式扩展点.md](chapters/%E7%AC%AC%2013%20%E7%AB%A0%EF%BC%9AExecutionListener%20%E4%B8%8E%20TaskListener%E2%80%94%E2%80%94%E5%88%87%E9%9D%A2%E5%BC%8F%E6%89%A9%E5%B1%95%E7%82%B9.md) |
| 14 | [第 14 章：子流程与 CallActivity——流程复用与参数传递.md](chapters/%E7%AC%AC%2014%20%E7%AB%A0%EF%BC%9A%E5%AD%90%E6%B5%81%E7%A8%8B%E4%B8%8E%20CallActivity%E2%80%94%E2%80%94%E6%B5%81%E7%A8%8B%E5%A4%8D%E7%94%A8%E4%B8%8E%E5%8F%82%E6%95%B0%E4%BC%A0%E9%80%92.md) |
| 15 | [第 15 章：定时器与边界事件——超时、提醒、升级.md](chapters/%E7%AC%AC%2015%20%E7%AB%A0%EF%BC%9A%E5%AE%9A%E6%97%B6%E5%99%A8%E4%B8%8E%E8%BE%B9%E7%95%8C%E4%BA%8B%E4%BB%B6%E2%80%94%E2%80%94%E8%B6%85%E6%97%B6%E3%80%81%E6%8F%90%E9%86%92%E3%80%81%E5%8D%87%E7%BA%A7.md) |
| 16 | [第 16 章：消息与信号——跨流程／跨实例协作.md](chapters/%E7%AC%AC%2016%20%E7%AB%A0%EF%BC%9A%E6%B6%88%E6%81%AF%E4%B8%8E%E4%BF%A1%E5%8F%B7%E2%80%94%E2%80%94%E8%B7%A8%E6%B5%81%E7%A8%8B%EF%BC%8F%E8%B7%A8%E5%AE%9E%E4%BE%8B%E5%8D%8F%E4%BD%9C.md) |
| 17 | [第 17 章：作业与异步——JobExecutor、失败、死信.md](chapters/%E7%AC%AC%2017%20%E7%AB%A0%EF%BC%9A%E4%BD%9C%E4%B8%9A%E4%B8%8E%E5%BC%82%E6%AD%A5%E2%80%94%E2%80%94JobExecutor%E3%80%81%E5%A4%B1%E8%B4%A5%E3%80%81%E6%AD%BB%E4%BF%A1.md) |
| 18 | [第 18 章：多实例——会签、或签、动态集合.md](chapters/%E7%AC%AC%2018%20%E7%AB%A0%EF%BC%9A%E5%A4%9A%E5%AE%9E%E4%BE%8B%E2%80%94%E2%80%94%E4%BC%9A%E7%AD%BE%E3%80%81%E6%88%96%E7%AD%BE%E3%80%81%E5%8A%A8%E6%80%81%E9%9B%86%E5%90%88.md) |
| 19 | [第 19 章：错误边界与补偿——Error Boundary、Compensation.md](chapters/%E7%AC%AC%2019%20%E7%AB%A0%EF%BC%9A%E9%94%99%E8%AF%AF%E8%BE%B9%E7%95%8C%E4%B8%8E%E8%A1%A5%E5%81%BF%E2%80%94%E2%80%94Error%20Boundary%E3%80%81Compensation.md) |
| 20 | [第 20 章：DMN 决策表——规则外置与流程联动.md](chapters/%E7%AC%AC%2020%20%E7%AB%A0%EF%BC%9ADMN%20%E5%86%B3%E7%AD%96%E8%A1%A8%E2%80%94%E2%80%94%E8%A7%84%E5%88%99%E5%A4%96%E7%BD%AE%E4%B8%8E%E6%B5%81%E7%A8%8B%E8%81%94%E5%8A%A8.md) |
| 21 | [第 21 章：Form 引擎与表单场景——内置与外链.md](chapters/%E7%AC%AC%2021%20%E7%AB%A0%EF%BC%9AForm%20%E5%BC%95%E6%93%8E%E4%B8%8E%E8%A1%A8%E5%8D%95%E5%9C%BA%E6%99%AF%E2%80%94%E2%80%94%E5%86%85%E7%BD%AE%E4%B8%8E%E5%A4%96%E9%93%BE.md) |
| 22 | [第 22 章：Idm 与身份对接——组、用户与 LDAP／自定义目录.md](chapters/%E7%AC%AC%2022%20%E7%AB%A0%EF%BC%9AIdm%20%E4%B8%8E%E8%BA%AB%E4%BB%BD%E5%AF%B9%E6%8E%A5%E2%80%94%E2%80%94%E7%BB%84%E3%80%81%E7%94%A8%E6%88%B7%E4%B8%8E%20LDAP%EF%BC%8F%E8%87%AA%E5%AE%9A%E4%B9%89%E7%9B%AE%E5%BD%95.md) |
| 23 | [第 23 章：Spring 深度集成——事务边界、自注入与引擎生命周期.md](chapters/%E7%AC%AC%2023%20%E7%AB%A0%EF%BC%9ASpring%20%E6%B7%B1%E5%BA%A6%E9%9B%86%E6%88%90%E2%80%94%E2%80%94%E4%BA%8B%E5%8A%A1%E8%BE%B9%E7%95%8C%E3%80%81%E8%87%AA%E6%B3%A8%E5%85%A5%E4%B8%8E%E5%BC%95%E6%93%8E%E7%94%9F%E5%91%BD%E5%91%A8%E6%9C%9F.md) |
| 24 | [第 24 章：测试入门——流程断言、场景数据、稳定回归.md](chapters/%E7%AC%AC%2024%20%E7%AB%A0%EF%BC%9A%E6%B5%8B%E8%AF%95%E5%85%A5%E9%97%A8%E2%80%94%E2%80%94%E6%B5%81%E7%A8%8B%E6%96%AD%E8%A8%80%E3%80%81%E5%9C%BA%E6%99%AF%E6%95%B0%E6%8D%AE%E3%80%81%E7%A8%B3%E5%AE%9A%E5%9B%9E%E5%BD%92.md) |
| 25 | [第 25 章：引擎配置全景——ProcessEngineConfiguration 关键项.md](chapters/%E7%AC%AC%2025%20%E7%AB%A0%EF%BC%9A%E5%BC%95%E6%93%8E%E9%85%8D%E7%BD%AE%E5%85%A8%E6%99%AF%E2%80%94%E2%80%94ProcessEngineConfiguration%20%E5%85%B3%E9%94%AE%E9%A1%B9.md) |
| 26 | [第 26 章：流程定义升级与迁移——版本策略、兼容性与变更剧本.md](chapters/%E7%AC%AC%2026%20%E7%AB%A0%EF%BC%9A%E6%B5%81%E7%A8%8B%E5%AE%9A%E4%B9%89%E5%8D%87%E7%BA%A7%E4%B8%8E%E8%BF%81%E7%A7%BB%E2%80%94%E2%80%94%E7%89%88%E6%9C%AC%E7%AD%96%E7%95%A5%E3%80%81%E5%85%BC%E5%AE%B9%E6%80%A7%E4%B8%8E%E5%8F%98%E6%9B%B4%E5%89%A7%E6%9C%AC.md) |
| 27 | [第 27 章：数据库与性能——热点表、索引、归档、慢查询.md](chapters/%E7%AC%AC%2027%20%E7%AB%A0%EF%BC%9A%E6%95%B0%E6%8D%AE%E5%BA%93%E4%B8%8E%E6%80%A7%E8%83%BD%E2%80%94%E2%80%94%E7%83%AD%E7%82%B9%E8%A1%A8%E3%80%81%E7%B4%A2%E5%BC%95%E3%80%81%E5%BD%92%E6%A1%A3%E3%80%81%E6%85%A2%E6%9F%A5%E8%AF%A2.md) |
| 28 | [第 28 章：集群与高可用——多副本、作业协调与数据库 HA.md](chapters/%E7%AC%AC%2028%20%E7%AB%A0%EF%BC%9A%E9%9B%86%E7%BE%A4%E4%B8%8E%E9%AB%98%E5%8F%AF%E7%94%A8%E2%80%94%E2%80%94%E5%A4%9A%E5%89%AF%E6%9C%AC%E3%80%81%E4%BD%9C%E4%B8%9A%E5%8D%8F%E8%B0%83%E4%B8%8E%E6%95%B0%E6%8D%AE%E5%BA%93%20HA.md) |
| 29 | [第 29 章：可观测性——日志、指标、分布式追踪.md](chapters/%E7%AC%AC%2029%20%E7%AB%A0%EF%BC%9A%E5%8F%AF%E8%A7%82%E6%B5%8B%E6%80%A7%E2%80%94%E2%80%94%E6%97%A5%E5%BF%97%E3%80%81%E6%8C%87%E6%A0%87%E3%80%81%E5%88%86%E5%B8%83%E5%BC%8F%E8%BF%BD%E8%B8%AA.md) |
| 30 | [第 30 章：运维排障——卡住、死信、时钟、悬挂与人工介入.md](chapters/%E7%AC%AC%2030%20%E7%AB%A0%EF%BC%9A%E8%BF%90%E7%BB%B4%E6%8E%92%E9%9A%9C%E2%80%94%E2%80%94%E5%8D%A1%E4%BD%8F%E3%80%81%E6%AD%BB%E4%BF%A1%E3%80%81%E6%97%B6%E9%92%9F%E3%80%81%E6%82%AC%E6%8C%82%E4%B8%8E%E4%BA%BA%E5%B7%A5%E4%BB%8B%E5%85%A5.md) |
| 31 | [第 31 章：自定义 BPMN 行为与解析（进阶）.md](chapters/%E7%AC%AC%2031%20%E7%AB%A0%EF%BC%9A%E8%87%AA%E5%AE%9A%E4%B9%89%20BPMN%20%E8%A1%8C%E4%B8%BA%E4%B8%8E%E8%A7%A3%E6%9E%90%EF%BC%88%E8%BF%9B%E9%98%B6%EF%BC%89.md) |
| 32 | [第 32 章：源码阅读路线——Command、Interceptor 与服务门面.md](chapters/%E7%AC%AC%2032%20%E7%AB%A0%EF%BC%9A%E6%BA%90%E7%A0%81%E9%98%85%E8%AF%BB%E8%B7%AF%E7%BA%BF%E2%80%94%E2%80%94Command%E3%80%81Interceptor%20%E4%B8%8E%E6%9C%8D%E5%8A%A1%E9%97%A8%E9%9D%A2.md) |
| 33 | [第 33 章：安全、权限与多租户——tenantId、最小权限与数据隔离.md](chapters/%E7%AC%AC%2033%20%E7%AB%A0%EF%BC%9A%E5%AE%89%E5%85%A8%E3%80%81%E6%9D%83%E9%99%90%E4%B8%8E%E5%A4%9A%E7%A7%9F%E6%88%B7%E2%80%94%E2%80%94tenantId%E3%80%81%E6%9C%80%E5%B0%8F%E6%9D%83%E9%99%90%E4%B8%8E%E6%95%B0%E6%8D%AE%E9%9A%94%E7%A6%BB.md) |
| 34 | [第 34 章：CI／CD——BPMN／DMN 制品、环境晋升与质量门禁.md](chapters/%E7%AC%AC%2034%20%E7%AB%A0%EF%BC%9ACI%EF%BC%8FCD%E2%80%94%E2%80%94BPMN%EF%BC%8FDMN%20%E5%88%B6%E5%93%81%E3%80%81%E7%8E%AF%E5%A2%83%E6%99%8B%E5%8D%87%E4%B8%8E%E8%B4%A8%E9%87%8F%E9%97%A8%E7%A6%81.md) |
| 35 | [第 35 章：CMMN 案例管理（选修）——何时不用 BPMN 硬拧.md](chapters/%E7%AC%AC%2035%20%E7%AB%A0%EF%BC%9ACMMN%20%E6%A1%88%E4%BE%8B%E7%AE%A1%E7%90%86%EF%BC%88%E9%80%89%E4%BF%AE%EF%BC%89%E2%80%94%E2%80%94%E4%BD%95%E6%97%B6%E4%B8%8D%E7%94%A8%20BPMN%20%E7%A1%AC%E6%8B%A7.md) |
| 36 | [第 36 章：专栏总复盘——从能跑到能运维、能讲清原理.md](chapters/%E7%AC%AC%2036%20%E7%AB%A0%EF%BC%9A%E4%B8%93%E6%A0%8F%E6%80%BB%E5%A4%8D%E7%9B%98%E2%80%94%E2%80%94%E4%BB%8E%E8%83%BD%E8%B7%91%E5%88%B0%E8%83%BD%E8%BF%90%E7%BB%B4%E3%80%81%E8%83%BD%E8%AE%B2%E6%B8%85%E5%8E%9F%E7%90%86.md) |

---

## 子目录

| 路径 | 说明 |
|------|------|
| [samples/README.md](samples/README.md) | 示例工程约定（与章节约定的版本矩阵对齐） |

---

## 扩写说明

新增或修订章节时：文件名与章首 **H1** 保持一致；按 [TEMPLATE.md](TEMPLATE.md) 四段补全，并跑一次 [VERSION_MATRIX.md](VERSION_MATRIX.md) 自检清单。
