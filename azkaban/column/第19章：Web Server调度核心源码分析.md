# 第19章：Web Server调度核心源码分析

## 1. 项目背景

### 业务场景

Azkaban集群每周一凌晨会出现一个诡异现象：2:00到2:05之间触发的50个Flow中，总有两三个"失踪"——调度日志显示Cron触发了，但Flow没有启动。开发团队怀疑是Quartz调度器的线程池太小，导致Cron触发时的任务调度被丢弃。

他们想修改调度器的线程池大小，但不知道在哪里配置——`azkaban.properties`中没有相关参数。深入源码后发现需要在`TriggerManager`初始化时传入`QuartzScheduler`的线程配置。

### 痛点放大

不理解Web Server调度内核时：

1. **调度不可靠**：多次出现"到了时间没触发"的灵异事件，排查无从下手
2. **Cron调度重复执行**：偶尔同一个Flow同时触发了两次
3. **大规模调度性能差**：1000+的Schedule并发触发时Web Server CPU飙升
4. **自定义调度需求得不到满足**：想支持"依赖上游Flow完成后触发"，不知从哪里改

## 2. 项目设计——剧本式交锋对话

**小胖**（皱着眉头看监控截图）：大师，你看这个——同一个Flow的Execution Id出现了两个，开始时间相差只有1秒。这不可能是我们手动触发两次，是调度系统自己搞的。

**大师**：这是典型的"Quartz misfire"问题。Azkaban底层使用Quartz Scheduler来驱动定时调度。当系统负载过高时，Quartz的线程池被占满，新触发的任务没线程执行——这就是misfire。你的Flow出现了两个实例，是因为Quartz的misfire策略设成了"补执行"（fire-and-proceed）。

**小白**：Quartz是个啥？它跟Azkaban的关系是什么？

**大师**（画出一个层级图）：

```
调度系统技术栈：
┌──────────────────────────────────────────┐
│ Azkaban Schedule (业务层)                 │
│   - ScheduleManager (管理调度计划)        │
│   - TriggerManager (管理触发器)           │
├──────────────────────────────────────────┤
│ Quartz Scheduler (调度引擎)               │
│   - CronTrigger (Cron表达式解析)          │
│   - JobStore (持久化调度任务到MySQL)       │
│   - ThreadPool (执行触发动作的线程池)      │
├──────────────────────────────────────────┤
│ MySQL (持久化)                             │
│   - triggers表                            │
│   - execution_flows表                     │
└──────────────────────────────────────────┘
```

Azkaban把"什么时候触发"交给Quartz处理，自己负责"触发了以后怎么办"。

**小胖**：那Azkaban自己做了哪些调度相关的事情？

**大师**：Azkaban的调度核心类有两个：

1. **ScheduleManager**：管理用户的调度计划——增删改查CRUD
2. **TriggerManager**：管理Quartz触发器——把Azkaban的Schedule转换成Quartz的Trigger，并处理misfire策略

调度全链路如下：

```
用户创建Schedule
    ↓
ScheduleManager.insertSchedule()
    ↓ (保存到MySQL的triggers表)
    ↓
TriggerManager.schedule()
    ↓ (创建Quartz CronTrigger)
    ↓
Quartz Scheduler线程轮询
    ↓ (Cron时间到达)
    ↓
Trigger.callback()
    ↓ (回调Azkaban的逻辑)
    ↓
ExecutorManager.submitFlow()
    ↓
Executor接收到Flow，开始执行
```

**小白**：那为什么有时一个Flow会被触发两次？

**大师**（详细解释）：关键在于Quartz的misfire机制。Quartz在以下几种情况下会产生misfire：

1. 调度线程池满了（没有空闲线程处理触发）
2. 系统时钟向前调整（NTP同步）
3. Quartz的JobStore加载延迟（MySQL慢查询导致）

当misfire发生后，Quartz会按照预设的策略处理。Azkaban默认用的是`MISFIRE_INSTRUCTION_IGNORE_MISFIRE_POLICY`——这意味着"等我腾出手了就马上补执行"。但如果你重启Web Server（重启期间Quartz暂停），重启后发现有一堆misfire的Cron触发堆积，它们会在重启后**一次性全部触发**——导致同一个Flow被同时提交多次。

**小胖**：那怎么避免呢？改成"丢弃misfire"不就好了？

**大师**：那就是另一个极端了——如果凌晨2点的调度因为misfire被丢弃，那这一天的数据就丢了。最平衡的做法是设置misfire的限制：

```java
// 在TriggerManager中配置
quartzProps.setProperty("org.quartz.jobStore.misfireThreshold", "60000"); // 60秒
quartzProps.setProperty("org.quartz.threadPool.threadCount", "20");
```

核心思路是：
- 增大线程池（`threadCount=20`），减少misfire发生的概率
- 设置合理的misfire阈值（`misfireThreshold=60000`），超过这个时间的才丢弃
- 添加重复执行检测——如果同一个Flow已有RUNNING实例，拒绝再次触发

### 技术映射总结

- **Quartz Scheduler** = 高级闹钟（Cron表达式解析 + 持久化 + 集群协调）
- **misfire** = 闹钟响了但没人在旁边按掉（系统忙，触发器事件被遗漏）
- **fire-and-proceed** = 补打卡（迟到了但还是要打，如果已经打过就重复了）
- **TriggerManager** = 闹钟管理中心（把所有闹钟管理好，准时叫醒对应的任务）

## 3. 项目实战

### 3.1 环境准备

- Azkaban Web Server源码（`azkaban-web-server`模块）
- IntelliJ IDEA

### 3.2 分步实现

#### 步骤1：源码目录定位

**目标**：找到调度相关的核心类。

```
azkaban-web-server/src/main/java/azkaban/webapp/servlet/
├── ScheduleServlet.java       # 调度REST API接口
├── TriggerManager.java        # 触发器管理（Quartz封装）
└── ScheduleManager.java       # 调度业务逻辑

azkaban-common/src/main/java/azkaban/scheduler/
├── Schedule.java              # 调度计划POJO
└── ScheduleManager.java       # 通用的调度管理接口

azkaban-common/src/main/java/azkaban/trigger/
├── Trigger.java               # 触发器接口
└── CronTrigger.java           # Cron触发器实现
```

#### 步骤2：ScheduleManager源码分析

**目标**：理解调度的创建和管理流程。

```java
// ScheduleManager.java —— 调度管理核心
public class ScheduleManager {
    
    public void insertSchedule(Schedule s) throws ScheduleManagerException {
        // 1. 判断是否允许重复调度
        if (hasSchedule(s.getProjectId(), s.getFlowName())) {
            throw new ScheduleManagerException("Schedule already exists");
        }
        
        // 2. 持久化到MySQL
        scheduleLoader.insertSchedule(s);
        
        // 3. 注册到Quartz
        Trigger t = buildQuartzTrigger(s);
        triggerManager.addTrigger(t);
        
        // 4. 启动调度
        quartzScheduler.start();
    }
    
    public void removeSchedule(Schedule s) {
        // 1. 从Quartz中移除
        triggerManager.removeTrigger(s.getScheduleId());
        // 2. 从MySQL中删除
        scheduleLoader.removeSchedule(s);
    }
    
    private Trigger buildQuartzTrigger(Schedule s) {
        // 将Azkaban的Schedule转换为Quartz的CronTrigger
        CronTrigger trigger = new CronTrigger();
        trigger.setCronExpression(s.getCronExpression());
        trigger.setScheduleId(s.getScheduleId());
        trigger.setTimeZone(s.getTimezone());
        trigger.setMisfireInstruction(
            CronTrigger.MISFIRE_INSTRUCTION_DO_NOTHING  // 重要！
        );
        return trigger;
    }
}
```

#### 步骤3：TriggerManager源码分析

**目标**：理解Quartz与Azkaban的交互方式。

```java
// TriggerManager.java —— Quartz调度器的封装
public class TriggerManager {
    
    private Scheduler quartzScheduler;
    private Properties quartzProps;
    
    public TriggerManager(Props props) {
        // 1. 初始化Quartz调度器
        this.quartzProps = new Properties();
        quartzProps.setProperty("org.quartz.threadPool.threadCount", "20");
        quartzProps.setProperty("org.quartz.jobStore.misfireThreshold", "60000");
        
        SchedulerFactory factory = new StdSchedulerFactory(quartzProps);
        this.quartzScheduler = factory.getScheduler();
        
        // 2. 设置Quartz Job（统一入口）
        JobDetail jobDetail = JobBuilder.newJob(AzkabanQuartzJob.class)
            .withIdentity("azkaban-trigger-job")
            .build();
        
        // 3. 加载MySQL中已有的所有调度
        loadExistingSchedules();
    }
    
    public void addTrigger(Trigger t) {
        CronTrigger quartzTrigger = TriggerBuilder.newTrigger()
            .withIdentity("trigger-" + t.getScheduleId())
            .withSchedule(CronScheduleBuilder.cronSchedule(t.getCronExpression())
                .inTimeZone(TimeZone.getTimeZone(t.getTimezone()))
                .withMisfireHandlingInstructionDoNothing()  // 防重复
            )
            .build();
        
        quartzScheduler.scheduleJob(quartzTrigger);
    }
}

// AzkabanQuartzJob.java —— Quartz触发的回调
public class AzkabanQuartzJob implements org.quartz.Job {
    
    @Override
    public void execute(JobExecutionContext context) {
        String scheduleId = context.getTrigger().getKey().getName();
        
        // 1. 防重复检查：当前是否有RUNNING的实例
        if (hasRunningExecution(scheduleId)) {
            logger.warn("Schedule {} already has a running execution, skip", scheduleId);
            return;
        }
        
        // 2. 提交Flow执行
        try {
            Flow flow = scheduleManager.getFlow(scheduleId);
            executorManager.submitExecutableFlow(flow, context.getFireTime());
            logger.info("Schedule {} triggered flow {}", scheduleId, flow.getFlowId());
        } catch (Exception e) {
            logger.error("Failed to submit flow for schedule {}", scheduleId, e);
        }
    }
}
```

#### 步骤4：misfire策略优化

**目标**：修改TriggerManager，防止同一Flow被重复触发。

```java
// 优化后的misfire处理
public class OptimizedTriggerManager extends TriggerManager {
    
    // 添加Flow执行计数器，防止重复触发
    private final Map<String, Long> lastTriggerTime = new ConcurrentHashMap<>();
    private static final long MIN_TRIGGER_INTERVAL_MS = 30_000;  // 30秒内不重复触发
    
    @Override
    public void onTrigger(String scheduleId) {
        long now = System.currentTimeMillis();
        Long lastTime = lastTriggerTime.get(scheduleId);
        
        if (lastTime != null && (now - lastTime) < MIN_TRIGGER_INTERVAL_MS) {
            logger.warn("Schedule {} triggered too frequently, skip. Last: {}, Now: {}",
                        scheduleId, lastTime, now);
            return;  // 30秒内同一个调度只触发一次
        }
        
        lastTriggerTime.put(scheduleId, now);
        
        // 执行正常的触发逻辑
        super.onTrigger(scheduleId);
    }
}
```

#### 步骤5：调度监控工具

**目标**：编写脚本监控调度器的健康状态。

```bash
#!/bin/bash
# monitor_scheduler.sh

echo "=== Azkaban 调度器监控 ==="
echo "时间: $(date)"
echo ""

# 1. 检查Quartz调度器状态
echo "[1] Quartz调度器状态..."
QRTZ_TRIGGERS=$(mysql -h prod-db -u azkaban -p'xxx' azkaban -e "
    SELECT TRIGGER_STATE, COUNT(*) AS cnt 
    FROM QRTZ_TRIGGERS 
    GROUP BY TRIGGER_STATE;
" 2>/dev/null)

echo "$QRTZ_TRIGGERS"

# 2. 检查misfire数量
echo "[2] Misfire检查..."
MISFIRE_COUNT=$(mysql -h prod-db -u azkaban -p'xxx' azkaban -e "
    SELECT COUNT(*) FROM QRTZ_TRIGGERS
    WHERE TRIGGER_STATE = 'ERROR' 
    OR (NEXT_FIRE_TIME < UNIX_TIMESTAMP() * 1000 
        AND NEXT_FIRE_TIME > 0);
" 2>/dev/null | tail -1)

echo "  异常Trigger数: $MISFIRE_COUNT"
if [ "$MISFIRE_COUNT" -gt 10 ]; then
    echo "  ⚠️  异常数量偏高，建议检查调度器"
fi

# 3. 调度线程池状态（通过JMX）
echo "[3] 调度线程池状态..."
JMX_URL="http://localhost:8081/jmx"
curl -s "$JMX_URL" 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
# 查找Quartz相关的MBean
for bean in data.get('beans', []):
    if 'quartz' in bean.get('name', '').lower():
        print(f\"  {bean['name']}\")
" 2>/dev/null

# 4. 24小时内触发统计
echo "[4] 最近24小时调度触发统计..."
TRIGGER_STATS=$(mysql -h prod-db -u azkaban -p'xxx' azkaban -e "
    SELECT 
        FROM_UNIXTIME(start_time/1000, '%Y-%m-%d %H:00') AS hour,
        COUNT(*) AS trigger_count
    FROM execution_flows
    WHERE submit_user = 'azkaban'  -- 调度触发的Flow
      AND start_time > UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 24 HOUR)) * 1000
    GROUP BY hour
    ORDER BY hour;
" 2>/dev/null)

echo "$TRIGGER_STATS"

echo "=== 监控完成 ==="
```

### 3.3 测试验证

```java
// 单元测试验证misfire防重复逻辑
@Test
public void testPreventDuplicateTrigger() {
    // 模拟同一个Flow在1秒内被触发两次
    OptimizedTriggerManager manager = new OptimizedTriggerManager(props);
    
    // 第一次触发应该成功
    boolean firstResult = manager.onTrigger("schedule-001");
    assertTrue(firstResult);
    
    // 立即第二次触发应该被拒绝
    boolean secondResult = manager.onTrigger("schedule-001");
    assertFalse(secondResult);
    
    // 等待31秒后再次触发应该成功
    Thread.sleep(31_000);
    boolean thirdResult = manager.onTrigger("schedule-001");
    assertTrue(thirdResult);
}
```

## 4. 项目总结

### 调度系统关键配置

| 参数 | 默认值 | 建议值 | 说明 |
|------|--------|--------|------|
| quartz.threadPool.threadCount | 10 | 20-50 | 取决于Schedule数量 |
| quartz.jobStore.misfireThreshold | 60000 | 30000-120000 | 太小会频繁misfire，太大会漏调度 |
| quartz.scheduler.skipUpdateCheck | true | true | 跳过Quartz版本检查 |

### 适用场景

- **适用**：Schedule数量>100的大规模场景、需要防止重复触发的核心调度、对调度可靠性有高要求的金融场景
- **不适用**：小型单机部署、Schedule数量<10的简单场景

### 注意事项

- MySQL的`QRTZ_*`表是Quartz自动创建的，不要手动修改
- Web Server重启期间不会丢失调度，因为所有Schedule都持久化在MySQL中
- 修改Quartz配置后需重启Web Server
- Web Server集群模式下，多个实例共享同一个Quartz的JobStore（通过MySQL锁协调）

### 常见踩坑经验

1. **NTP时间跳变导致misfire风暴**：服务器时钟向前调整了1小时，导致所有Cron调度被认为是misfire。解决：先停止Web Server，调整完时间再启动。
2. **QRTZ_TRIGGERS表增长**：每次创建/修改Schedule会在表中新增记录，不会删除旧记录。定期清理`TRIGGER_STATE='WAITING'`且`NEXT_FIRE_TIME=0`的记录。
3. **Quartz Clustered模式下的死锁**：多Web实例使用Quartz集群模式时，同一个Cron触发可能被两个实例同时获得锁导致死锁。解决：确保`org.quartz.jobStore.isClustered=true`且所有实例时钟同步。

### 思考题

1. 如何实现"工作日调度"——Flow只在周一至周五触发，周六日自动跳过？Azkaban的Quartz Cron支持`0 0 8 ? * 2-6`语法，但当遇到法定节假日调休时如何处理？
2. 如果需要支持"事件驱动的调度"——当上游Flow完成后自动触发下游Flow，而不仅仅是时间触发，如何扩展Azkaban的TriggerManager？
