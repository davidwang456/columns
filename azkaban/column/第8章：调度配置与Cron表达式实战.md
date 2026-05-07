# 第8章：调度配置与Cron表达式实战

## 1. 项目背景

### 业务场景

数据团队的ETL流水线已经稳定运行了一周，但每天都靠手动点击"Execute Flow"按钮触发——开发同学轮流值班，凌晨2点起床点击执行。运营总监要求将这套流程自动化：每天凌晨2点自动启动，如果2点那批数据没到齐则延迟重试，节假日跳过日报生成。

运维同学在Azkaban的Schedule配置页面填了一个Cron表达式，结果任务没跑。查了半天发现——他把"每天凌晨2点"写成了 `0 0 2 * * ?` 但是忘了调时区，实际配的是UTC时间，等于北京时间上午10点才触发。更糟糕的是，后面他又配置了几个"每5分钟执行一次"的调度，结果3天就产生了800多条执行记录，数据库都快满了。

### 痛点放大

调度配置不当时：

1. **时区陷阱**：Cron表达式配的是本地时间，但Azkaban内部用UTC存储和比较时间，导致任务偏离8小时。
2. **Cron表达式写错**：`0 0/5 * * * ?` 和 `0 */5 * * * ?` 含义完全不同，前者"0分开始每5分钟"，后者"每5分钟一次"——新手常搞混。
3. **调度堆积**：上一次调度还没跑完，下一次调度又触发了。最多同时5个实例在跑，资源耗尽。
4. **调度泄露**：暂停调度后，已触发的正在执行的Flow不受影响，但操作者以为"暂停=停止"，导致重复执行。

## 2. 项目设计——剧本式交锋对话

**小胖**（顶着黑眼圈）：大师，我这周值了两次夜班，凌晨2点手动执行Flow。能不能让Azkaban自己帮我们跑啊？

**大师**：当然能。Azkaban的Schedule模块就是为这个设计的——你可以给任意Flow绑定一个调度计划，Azkaban按你指定的时间自动触发执行。

**小胖**：就是那个Cron表达式对吧？我试过但搞不定，写了一堆`* * * * *`结果每分钟都在跑，服务器都快炸了！

**大师**（笑）：Cron表达式确实容易写错。我教你一个记忆口诀——

```
秒 分 时 日 月 星期
*  *  *  *  *   *
│  │  │  │  │   │
│  │  │  │  │   └─ 星期（1-7，1=周日）
│  │  │  │  └─── 月份（1-12）
│  │  │  └────── 日期（1-31）
│  │  └───────── 小时（0-23）
│  └──────────── 分钟（0-59）
└─────────────── 秒（0-59）—— 注意：标准Unix Cron没有秒字段！
```

Azkaban用的是Quartz Cron，比标准Linux Cron多一个秒字段。常用的几个模板：

| 需求 | Cron表达式 | 含义 |
|------|-----------|------|
| 每分钟 | `0 * * * * ?` | 每分钟的0秒 |
| 每5分钟 | `0 0/5 * * * ?` | 从0分开始，每5分钟 |
| 每天凌晨2点 | `0 0 2 * * ?` | 每天02:00:00 |
| 每周一早上8点 | `0 0 8 ? * 2` | 周一（2=周一）08:00 |
| 每月1日凌晨3点 | `0 0 3 1 * ?` | 每月1号03:00 |
| 每小时 | `0 0 * * * ?` | 每整点 |

**小白**（举手）：为什么有的位置写`?`，有的写`*`？这两个有什么区别？

**大师**：好问题！`*` 和 `?` 在Quartz Cron中有本质区别：
- `*` 表示"所有可能的值" —— 如月份写`*`表示1-12月
- `?` 表示"不指定/不关心" —— 用在日和周字段，表示"我不指定具体哪一天/星期几"

规则很简单：**日和星期不能同时指定为一个具体值**。比如你不能同时说"每月15号"和"每周二"，因为这会产生冲突（15号未必是周二）。必须有一个用`?`表示"不指定"。

**小胖**：原来如此！那如果我要"工作日（周一到周五）每天早上8点"，应该怎么写？

**大师**：`0 0 8 ? * 2-6`。星期2=周一，6=周五。

**小白**：那"跳过"的问题怎么办？比如节假日不跑日报？

**大师**：这分两个层面：

1. **Azkaban原生支持**：在Schedule设置中，可以勾选"跳过指定日期"。但你需要手动维护一个"排除日期"列表。
2. **更灵活的方式**：在Flow的第一个Job中，加一个"日期检查"逻辑——如果是节假日，则`echo "Holiday, skip" && exit 0`。

**小胖**：还有个问题——如果前一次的调度还没跑完，第二次触发会怎样？会不会启动两个一模一样的实例？

**大师**：这就是"调度并发控制"的问题。Azkaban的默认行为是——如果上一次调度还在运行，会**跳过本次触发**。这个行为可以在Schedule配置中调整：

```bash
# 调度并发控制选项
schedule.concurrencyOption=skip  # skip | run | pipeline
```

- `skip`：上一次未完成则跳过本次（默认，安全）
- `run`：无论上一次是否完成都启动新实例（小心堆积）
- `pipeline`：按配置的并行度启动（类似流水线）

我强烈推荐用默认的`skip`，避免资源雪崩。如果确实需要高频率执行（比如每5分钟一次），在Job内部做好"快速失败"的机制——如果数据没准备好，立即退出，别在Yarn队列里排队。

### 技术映射总结

- **Cron表达式** = 闹钟设定（什么时间响、星期几响、每月几号响）
- **秒字段** = 闹钟的精确性（精确到秒）
- **? 问号** = "随缘"（日和星期，二选一即可）
- **skip并发策略** = 电梯超载保护（满员了就不让进）
- **时区配置** = 手表基准（北京时间 vs 纽约时间，差了13小时必须调整）

## 3. 项目实战

### 3.1 环境准备

Azkaban中已有至少一个可执行的Flow（如第4章创建的`etl_pipeline`）。

### 3.2 分步实现

#### 步骤1：通过Web界面创建Schedule

**目标**：为Flow绑定一个每天凌晨2点的调度。

1. 进入项目 → 选择Flow → 点击左侧`Schedule`链接
2. 在弹出窗口中配置：

```
Schedule Time: 0 0 2 * * ?    （每天凌晨2:00:00触发）
Timezone: Asia/Shanghai        （北京时间）
Schedule Options:
  ☑ Skip if previous hasn't finished
  ☐ Allow multiple concurrent runs
  ☐ Send failure email
Notify on Failure:  first failure only
```

3. 点击`Schedule`按钮，页面返回"Schedule for flow xxx has been set."

#### 步骤2：通过API创建和查询Schedule

**目标**：使用REST API管理调度。

```bash
# 登录
curl -c cookies.txt \
  -X POST "http://localhost:8081" \
  --data "action=login&username=azkaban&password=azkaban"

# 为Flow创建调度
curl -b cookies.txt \
  -X POST "http://localhost:8081/schedule" \
  --data "ajax=scheduleCronFlow" \
  --data "projectName=etl_pipeline" \
  --data "flow=etl_pipeline" \
  --data "cronExpression=0 0 2 * * ?" \
  --data "scheduleTimezone=Asia/Shanghai" \
  --data "failureAction=finishPossible" \
  --data "failureEmails=admin@company.com" \
  --data "skipPastOccurrences=false"

# 查询所有调度
curl -b cookies.txt \
  "http://localhost:8081/schedule?ajax=fetchAllScheduledFlows"
# 返回格式：
# {
#   "items": [
#     {
#       "scheduleId": 1,
#       "cronExpression": "0 0 2 * * ?",
#       "submitUser": "azkaban",
#       "firstSchedTime": "2025-01-16",
#       "nextExecTime": "2025-01-17 02:00:00",
#       "period": "day",
#       "projectName": "etl_pipeline",
#       "flowName": "etl_pipeline"
#     }
#   ]
# }

# 移除调度
curl -b cookies.txt \
  -X POST "http://localhost:8081/schedule" \
  --data "action=removeSched&scheduleId=1"
```

#### 步骤3：Cron表达式测试工具

**目标**：编写脚本验证Cron表达式的效果。

```python
#!/usr/bin/env python3
# cron_tester.py —— 验证Cron表达式在未来若干次触发的时间

from croniter import croniter
from datetime import datetime
import pytz

def test_cron(cron_expr, tz='Asia/Shanghai', count=10):
    """
    测试Cron表达式，返回接下来N次触发时间
    """
    tz_obj = pytz.timezone(tz)
    base = datetime.now(tz_obj)
    cron = croniter(cron_expr, base)

    print(f"Cron表达式: {cron_expr}")
    print(f"时区: {tz}")
    print(f"基准时间: {base.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\n接下来 {count} 次触发时间:")
    print("-" * 40)

    for i in range(count):
        next_time = cron.get_next(datetime)
        print(f"  {next_time.strftime('%Y-%m-%d %H:%M:%S %A')}")

if __name__ == '__main__':
    import sys
    cron_expr = sys.argv[1] if len(sys.argv) > 1 else "0 0 2 * * ?"
    test_cron(cron_expr)

# 安装依赖：pip install croniter pytz
```

```bash
# 测试不同Cron表达式
python3 cron_tester.py "0 0 2 * * ?"       # 每天凌晨2点
python3 cron_tester.py "0 0/15 * * * ?"    # 每15分钟
python3 cron_tester.py "0 0 8 ? * 2-6"     # 工作日早上8点
```

#### 步骤4：时区验证

**目标**：演示时区配置的重要性。

```bash
# 对比相同Cron在不同时区下的行为
echo "=== 时区对比: Asia/Shanghai vs UTC ==="

# 上海时区（UTC+8）
echo "Asia/Shanghai (0 0 2 * * ?):"
python3 -c "
from croniter import croniter
from datetime import datetime
import pytz
tz = pytz.timezone('Asia/Shanghai')
c = croniter('0 0 2 * * ?', datetime.now(tz))
for _ in range(3):
    print(f'  {c.get_next(datetime).strftime(\"%Y-%m-%d %H:%M:%S\")}')
"

# UTC时区
echo "UTC (0 0 2 * * ?):"
python3 -c "
from croniter import croniter
from datetime import datetime
import pytz
tz = pytz.timezone('UTC')
c = croniter('0 0 2 * * ?', datetime.now(tz))
for _ in range(3):
    print(f'  {c.get_next(datetime).strftime(\"%Y-%m-%d %H:%M:%S\")} —— 北京时间: {c.get_next(datetime).strftime(\"%H\")}点')
"
```

#### 步骤5：调度记录查询

**目标**：查看调度历史，确认任务是否按时触发。

```bash
# 查询某个Flow的调度执行历史
curl -b cookies.txt \
  "http://localhost:8081/manager?project=etl_pipeline&ajax=fetchFlowExecutions&flow=etl_pipeline&start=0&length=20"

# 返回格式（简化）：
# {
#   "executions": [
#     {"execId": 42, "startTime": "2025-01-16 02:00:05", 
#      "endTime": "2025-01-16 02:03:22", "status": "SUCCEEDED",
#      "submitUser": "azkaban"},
#     {"execId": 41, "startTime": "2025-01-15 02:00:03",
#      "endTime": "2025-01-15 02:02:58", "status": "SUCCEEDED",
#      "submitUser": "azkaban"},
#     ...
#   ]
# }
```

**验证调度是否准时**：观察Execution的开始时间是否与Cron表达式的预期触发时间一致。如果时区配置错误，会看到明显的8小时偏移。

### 3.3 测试验证

```bash
#!/bin/bash
# verify_schedule.sh

echo "=== Schedule验证 ==="

# 1. 创建测试调度（每2分钟一次）
SCHEDULE_RESP=$(curl -s -b cookies.txt \
  -X POST "http://localhost:8081/schedule" \
  --data "ajax=scheduleCronFlow" \
  --data "projectName=test_schedule" \
  --data "flowName=test_flow" \
  --data "cronExpression=0 0/2 * * * ?")

if echo "$SCHEDULE_RESP" | grep -q "scheduleId"; then
    echo "[PASS] Schedule创建成功"
else
    echo "[FAIL] Schedule创建失败: $SCHEDULE_RESP"
fi

# 2. 等待2分钟后检查是否有自动执行的记录
echo "[Test 2] 等待调度触发（3分钟）..."
sleep 180

RECENT_EXECS=$(curl -s -b cookies.txt \
  "http://localhost:8081/manager?project=test_schedule&ajax=fetchFlowExecutions&flow=test_flow&start=0&length=5")

if echo "$RECENT_EXECS" | grep -q '"execId"'; then
    echo "[PASS] 检测到自动触发的执行记录"
else
    echo "[FAIL] 未检测到自动执行记录"
fi

# 3. 清理测试Schedule
SCHEDULE_ID=$(echo "$SCHEDULE_RESP" | grep -o '"scheduleId":[0-9]*' | grep -o '[0-9]*')
curl -b cookies.txt \
  -X POST "http://localhost:8081/schedule" \
  --data "action=removeSched&scheduleId=$SCHEDULE_ID"

echo "=== 验证完成 ==="
```

## 4. 项目总结

### Cron表达式常用模板速查

| 场景 | Cron表达式 | 说明 |
|------|-----------|------|
| 每分钟 | `0 * * * * ?` | 每分钟0秒触发 |
| 每5分钟 | `0 0/5 * * * ?` | 0,5,10...55分触发 |
| 每30分钟 | `0 0/30 * * * ?` | 0,30分触发 |
| 每小时 | `0 0 * * * ?` | 每整点触发 |
| 每天凌晨2点 | `0 0 2 * * ?` | 天级批处理 |
| 每天8点、12点、18点 | `0 0 8,12,18 * * ?` | 多时间点 |
| 工作日8点 | `0 0 8 ? * 2-6` | 周一至周五 |
| 每月1日3点 | `0 0 3 1 * ?` | 月报表 |
| 每年1月1日0点 | `0 0 0 1 1 ?` | 年度任务 |

### 适用场景

- **适用**：天级/小时级的批处理调度、日报/月报自动生成、数据同步定期触发、运维巡检定时任务
- **不适用**：秒级/毫秒级的实时任务、需要复杂日历（如"每月最后一个工作日"）的任务、事件驱动（非时间驱动）的触发

### 注意事项

- Azkaban使用Quartz Cron，比标准Linux Cron多一个**秒字段**（6个字段 vs 5个字段）
- 时区一定要显式配置，默认是UTC，国内务必设为`Asia/Shanghai`
- 调度并发默认是`skip`策略——避免同个Flow同时跑两个实例
- 调度暂停后，已触发且正在运行的实例不会被自动取消

### 常见踩坑经验

1. **时区未配置**：默认UTC导致任务跑晚了8小时。在每个Schedule设置中务必选择`Asia/Shanghai`，同时在`azkaban.properties`中设置全局默认时区`default.timezone.id=Asia/Shanghai`。
2. **Cron秒字段遗漏**：用Linux习惯写了5个字段`0 2 * * *`（缺少秒字段），导致Schedule创建失败。在Azkaban中必须写6个字段：`0 0 2 * * ?`。
3. **跨天调度混乱**：配置了`0 0 23 * * ?`（每天晚上11点），但任务跑了3小时后跨天了，日志日期混乱。解决：在Job中使用统一的日期变量。

### 思考题

1. 你需要一个"每个自然月最后一个工作日（周一至周五）早上9点"的调度，但Cron表达式的标准语法无法直接表达"最后一个工作日"。请设计一种方案来实现这个需求。
2. 一个Flow的调度频率是每5分钟，但正常的Job执行需要8分钟。如果使用默认的`skip`策略，每隔一个调度周期就会跳过一次执行。请设计两种优化策略来解决这个问题。
