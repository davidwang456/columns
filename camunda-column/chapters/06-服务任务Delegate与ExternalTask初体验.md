# 第 6 章：服务任务（Delegate / External Task）初体验

## 元信息

| 项目 | 内容 |
|------|------|
| 章节编号 | 第 6 章 |
| 标题 | 服务任务（Delegate / External Task）初体验 |
| 难度 | 入门 |
| 预计阅读 | 30～35 分钟 |
| 受众侧重 | 开发 |
| 依赖章节 | 第 3、5 章 |
| 环境版本 | `baseline-2026Q1` |

---

## 1. 项目背景

用户任务需要人，**服务任务**让系统在毫秒到秒级内自动做一件事：校验额度、写业务表、调外部 HTTP。Camunda 里常见两种接法：**Java Delegate（同步，同进程）** 与 **External Task（异步拉取，独立 Worker）**。本章要解决的**一条主线问题**是：各用最小例子跑通一次，建立**重试、异常与事务**的直觉，并知道何时该换 External Task（第 14 章深化）。

---

## 2. 项目设计（三角色对话）

### 2.1 小胖开球

小胖说：「我在 Spring 里 `@Service` 写方法就好，为啥还要 `JavaDelegate`？」

因为引擎要在**流程事务**里回调你的代码：`execute(DelegateExecution)` 是稳定契约，便于引擎记录**失败、重试、作业**。直接乱调 Service 可能绕过预期的事务边界。

### 2.2 小白追问

小白问：「第一，Delegate 里抛异常，流程实例会怎样？第二，**异步延续**勾选后还是同步吗？第三，External Task 和消息队列消费者有什么本质区别？」

### 2.3 大师定调

大师归纳：

- **同步 Delegate**：与引擎线程模型、事务紧密；适合短、快、同事务操作。
- **异常**：未捕获时通常导致事务回滚、作业失败并可按策略重试；需要**业务异常 vs 技术异常**分层处理（第 20 章）。
- **异步延续**：将后续步骤放入作业表，由作业执行器异步推进，避免长阻塞——仍是「引擎语义」，不是 Kafka。
- **External Task**：引擎只负责创建外部任务记录，**Worker 拉取执行**，天然适合弹性扩缩、异构语言、与 K8s HPA 对齐。

### 2.4 超时与重试：别在 Delegate 里睡大觉

小胖问：「我 Thread.sleep 等下游可以吗？」大师答：**极不推荐**。会占用线程与事务时间；应使用 **异步延续 / 外部任务 / 消息等待**，把「等」变成显式模型。

---

## 3. 项目实战

### 3.1 环境前提

- 第 2 章工程；会写 Spring Bean。

### 3.2 步骤说明

1. 新建 `CheckInventoryDelegate` 实现 `JavaDelegate`，在 `execute` 中读取变量并写回结果。
2. BPMN 服务任务绑定 `delegateExpression` 指向 Spring Bean 名。
3. 部署后发起流程，确认自动通过该节点。
4. 在 Delegate 中故意抛 `BpmnError` 或运行时异常各一次，观察 Cockpit 中实例状态与日志（记录差异）。
5. （可选）将同一业务改为 External Task：BPMN 改为 external 类型，起一个最小 Worker 完成 topic（与第 14 章衔接）。

### 3.3 源码与说明

```java
@Component("checkInventoryDelegate")
public class CheckInventoryDelegate implements JavaDelegate {
  @Override
  public void execute(DelegateExecution execution) {
    Integer qty = (Integer) execution.getVariable("qty");
    execution.setVariable("inStock", qty != null && qty > 0);
  }
}
```

**为什么用 Spring Bean 名**：`delegateExpression` 与 IoC 容器对齐，便于注入仓储与配置。

BPMN 绑定（概念）：

```xml
<bpmn:serviceTask id="check" name="校验库存"
  camunda:delegateExpression="${checkInventoryDelegate}" />
```

**为什么不用 class 全名**：团队常选 Spring 管理以支持 `@Autowired`。

### 3.4 验证

- 无人工干预下流程自动越过服务任务。
- 日志与变量 `inStock` 符合预期；异常路径有记录可查。

### 3.5 小实验记录表

| 实验 | 期望 |
|------|------|
| 抛运行时异常 | 事务回滚/作业失败（按配置） |
| 抛 BpmnError | 走错误边界（若建模） |
| 正常返回 | 变量写入成功 |

---

## 4. 项目总结

| 维度 | 内容 |
|------|------|
| 优点 | Delegate 上手快；External Task 解耦强。 |
| 缺点 / 代价 | Delegate 易把重活塞进同进程；External 增加运维与网络成本。 |
| 适用场景 | 短事务业务规则；长耗时/弹性用 External。 |
| 不适用场景 | 极长阻塞 IO 仍放在同步 Delegate。 |
| 注意事项 | 异常语义、幂等、外部系统超时。 |
| 常见踩坑 | Bean 名拼写；事务边界误解；重试导致重复副作用。 |

**延伸阅读**：第 14 章 External Task；第 20 章错误边界。

## 5. 附录：Spring Bean 命名规范

`delegateExpression` 引用 Bean 名；团队应统一前缀（如 `delegateXxx`）并在 Code Review 检查 **拼写** 与 **单测覆盖**。

