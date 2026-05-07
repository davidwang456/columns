# 第23章：SLA监控与告警升级

## 1. 项目背景

### 业务场景

凌晨5点，运营总监的手机响了——"核心经营日报"还没生成。此时距离早上9点的管理层周会还有4小时，但如果Flow在6点前还没完成，CIO在周会上就会看到一张空白报表。

当前Azkaban已经配置了Job失败邮件告警。但这个告警只在"Job已经失败"后触发，无法实现"Flow运行了2小时还没完成"的进度预警。更糟糕的是，上周发生了一次"静默失败"——Flow执行"成功"了，但输出的数据量只有平时的10%，原因是上游数据采集系统只传了一半的数据。

### 痛点放大

没有SLA监控时：

1. **只能事后告警**：等Job失败了才知道，已经晚了
2. **无法感知延迟**：Flow跑了3小时才完成（平时只要30分钟），说明出了问题但无人察觉
3. **质量无保证**：流程跑完了，但数据可能是错的或不全的
4. **告警层级缺失**：失败通知了值班开发，但15分钟没人处理，也应该通知Leader

## 2. 项目设计——剧本式交锋对话

**小胖**（焦急地看表）：大师，日报Flow从凌晨2点跑到4点了还没结束！平时30分钟就完了，肯定卡住了。但Azkaban没发任何告警，因为它还没失败，只是"一直在运行"。

**大师**：这就是SLA（Service Level Agreement，服务水平协议）的价值。Azkaban支持两种SLA：

1. **时间SLA**：Flow必须在指定时间内完成，否则告警
2. **数据SLA**：Job输出的数据必须满足某种质量要求

**小白**：时间SLA具体怎么配置？

**大师**：Azkaban支持两种SLA规则：

```bash
# Flow级别的SLA规则
sla.rule=finish_in:2h           # 必须在2小时内完成
sla.rule=duration:30m           # 单个Job执行时间不能超过30分钟

# 多规则组合
sla.rule=finish_in:2h,duration:30m
```

当Flow启动超过2小时还没完成，或某个Job执行超过30分钟，SLA会触发告警邮件。

**小胖**：那告警升级呢？比如：
1. Flow超过1小时 → 邮件通知开发
2. Flow超过2小时 → 短信通知Leader
3. Flow超过4小时 → 电话通知总监

**大师**（画图）：

```
告警升级链（Azkaban原生不支持多级，需自定义实现）：
┌─────────────────────────────────────────────────────┐
│ T+0min  Flow启动                                     │
│ T+60min SLA-1: finish_in:1h 触发 → 邮件告警开发       │
│ T+120min SLA-2: finish_in:2h 触发 → 企业微信告警Leader │
│ T+240min SLA-3: finish_in:4h 触发 → 电话告警总监       │
└─────────────────────────────────────────────────────┘
```

Azkaban原生只支持一个SLA规则，多级告警需要你在外面封装。但可以变通：创建多个"检查Job"作为Flow的一部分，每个检查Job在不同的时间点执行数据验证。

**小白**：那数据质量SLA呢？怎么检查"数据量只有平时的10%"？

**大师**：这需要你自定义实现。基本思路是：

```bash
# Flow的最后一个Job
type=command
command=bash -c '
# 1. 获取今日数据量
TODAY_COUNT=$(hive -e "SELECT COUNT(*) FROM ods.orders WHERE dt=CURDATE()" 2>/dev/null | tail -1)

# 2. 获取过去7天的平均数据量
AVG_COUNT=$(hive -e "SELECT AVG(cnt) FROM (
    SELECT COUNT(*) AS cnt FROM ods.orders 
    WHERE dt BETWEEN DATE_SUB(CURDATE(), 7) AND DATE_SUB(CURDATE(), 1)
    GROUP BY dt
) t" 2>/dev/null | tail -1)

# 3. 判断数据质量
THRESHOLD=$(echo "$AVG_COUNT * 0.7" | bc)
echo "今日数据量: $TODAY_COUNT, 7日平均: $AVG_COUNT, 阈值(70%): $THRESHOLD"

if [ "$TODAY_COUNT" -lt "$THRESHOLD" ]; then
    echo "⚠️  数据量异常！低于历史平均的70%"
    # 发送告警但不阻塞Flow
    python3 /opt/scripts/quality_alert.py "数据量异常: $TODAY_COUNT < $THRESHOLD"
fi
'
```

### 技术映射总结

- **finish_in SLA** = 快递时限（承诺2天内送达，超时就投诉）
- **duration SLA** = 单站停留时间（你这个工序太慢了，影响整条线）
- **数据质量SLA** = 出品抽查（菜是做出来了，但分量少一半也要投诉）
- **告警升级** = 问题上报链（保安处理不了→叫经理→叫老板）

## 3. 项目实战

### 3.1 环境准备

Azkaban运行中，准备SLA测试的Flow。

### 3.2 分步实现

#### 步骤1：配置Flow级别SLA

**目标**：为关键Flow添加时间和数据质量SLA。

```bash
# sla_demo.flow
nodes=job_init,job_process,job_validate,sla_check

# Flow SLA配置
sla.rule=finish_in:1h
sla.emails=oncall@company.com
```

```bash
# job_init.job —— 初始化并记录开始时间
type=command
command=echo "Flow started at: $(date '+%Y-%m-%d %H:%M:%S')"
command.1=echo "SLA: Must finish within 1 hour"
```

```bash
# sla_check.job —— SLA检查Job（Flow最后一个Job）
type=command
command=bash -c '
echo "=== SLA Check ==="

# 计算Flow总耗时
START_TIME_MS=${azkaban.flow.starttimestamp}
END_TIME_MS=$(date +%s%3N)
DURATION_SEC=$(( (END_TIME_MS - START_TIME_MS) / 1000 ))
DURATION_MIN=$(( DURATION_SEC / 60 ))

echo "Flow耗时: ${DURATION_MIN}分钟"

# SLA判定
SLA_LIMIT_MIN=60
if [ $DURATION_MIN -gt $SLA_LIMIT_MIN ]; then
    echo "⚠️  SLA违反！耗时${DURATION_MIN}分钟 > 限制${SLA_LIMIT_MIN}分钟"
    # 触发SLA告警
    python3 /opt/scripts/sla_alert.py \
      --flow="${azkaban.flow.flowid}" \
      --duration=${DURATION_MIN} \
      --limit=${SLA_LIMIT_MIN} \
      --execid="${azkaban.flow.execid}"
else
    echo "✓ SLA合格：${DURATION_MIN}分钟 <= ${SLA_LIMIT_MIN}分钟"
fi
'
dependsOn=job_init,job_process,job_validate
```

#### 步骤2：多级告警升级系统

**目标**：实现告警随等待时间升级。

```python
#!/usr/bin/env python3
# escalation_manager.py —— 告警升级管理

import time
import json
import requests
from datetime import datetime

class AlertEscalationManager:
    """告警升级管理器"""
    
    ESCALATION_LEVELS = [
        {"level": 1, "name": "开发值班", "timeout_min": 15, "channel": "email"},
        {"level": 2, "name": "技术Leader", "timeout_min": 30, "channel": "wechat"},
        {"level": 3, "name": "运维总监", "timeout_min": 60, "channel": "phone"},
    ]
    
    def __init__(self, azkaban_url, session):
        self.azkaban_url = azkaban_url
        self.session = session
        self.alert_state = {}  # 记录每个Flow的告警状态
    
    def monitor_flow(self, project, flow):
        """监控一个Flow，按需升级告警"""
        flow_key = f"{project}/{flow}"
        
        while True:
            # 获取Flow状态
            status, duration_min = self._get_flow_status(project, flow)
            
            if status in ("SUCCEEDED", "FAILED", "KILLED"):
                print(f"Flow {flow_key} 已结束: {status}")
                self.alert_state.pop(flow_key, None)
                break
            
            # 检查是否需要升级告警
            current_level = self.alert_state.get(flow_key, {}).get("level", 0)
            
            for esc in self.ESCALATION_LEVELS:
                if esc["level"] > current_level and duration_min >= esc["timeout_min"]:
                    self._send_alert(flow_key, esc, duration_min)
                    self.alert_state[flow_key] = {
                        "level": esc["level"],
                        "alerted_at": datetime.now()
                    }
                    break
            
            time.sleep(60)  # 每分钟检查一次
    
    def _get_flow_status(self, project, flow):
        """获取Flow状态和耗时"""
        resp = requests.get(
            f"{self.azkaban_url}/manager",
            params={
                "project": project,
                "ajax": "fetchFlowExecutions",
                "flow": flow,
                "start": 0,
                "length": 1
            },
            cookies={"azkaban.browser.session.id": self.session}
        )
        
        data = resp.json()
        execs = data.get("executions", [])
        if not execs:
            return "UNKNOWN", 0
        
        latest = execs[0]
        status = latest.get("status", "UNKNOWN")
        start_time = latest.get("startTime", 0) / 1000
        duration_min = (time.time() - start_time) / 60 if start_time else 0
        
        return status, duration_min
    
    def _send_alert(self, flow_key, escalation, duration_min):
        """发送告警"""
        print(f"⚠️  告警升级 Level {escalation['level']}: {escalation['name']}")
        print(f"   Flow: {flow_key}")
        print(f"   已运行: {duration_min:.1f}分钟")
        print(f"   渠道: {escalation['channel']}")
        
        # 实际发送逻辑：
        if escalation["channel"] == "email":
            self._send_email(escalation, flow_key, duration_min)
        elif escalation["channel"] == "wechat":
            self._send_wechat(escalation, flow_key, duration_min)
        elif escalation["channel"] == "phone":
            self._make_phone_call(escalation, flow_key, duration_min)
    
    def _send_email(self, escalation, flow_key, duration):
        """邮件告警"""
        print(f"  [EMAIL] To: {escalation['name']} - Flow {flow_key} 已运行{duration:.0f}分钟")
    
    def _send_wechat(self, escalation, flow_key, duration):
        """企业微信告警"""
        print(f"  [WECHAT] To: {escalation['name']} - Flow {flow_key} 已运行{duration:.0f}分钟")
    
    def _make_phone_call(self, escalation, flow_key, duration):
        """电话告警（通过第三方API）"""
        print(f"  [PHONE] Calling: {escalation['name']} - Flow {flow_key} 紧急！")

if __name__ == '__main__':
    manager = AlertEscalationManager("http://localhost:8081", "session_xxx")
    manager.monitor_flow("core_pipeline", "daily_report")
```

#### 步骤3：数据质量SLA

**目标**：在Flow中添加数据质量验证Job。

```python
#!/usr/bin/env python3
# data_quality_sla.py —— 数据质量SLA检查

import sys
import json
from datetime import datetime, timedelta

def check_row_count(current_count, historical_avg, threshold=0.7):
    """检查数据行数是否达标"""
    if current_count < historical_avg * threshold:
        return {
            "passed": False,
            "reason": f"Row count {current_count} is below {threshold*100}% of historical avg {historical_avg}"
        }
    return {"passed": True}

def check_null_rate(table, column, max_null_rate=0.05):
    """检查空值率"""
    # 执行Hive SQL查询
    # SELECT COUNT(*) AS null_count FROM table WHERE column IS NULL
    pass

def check_data_freshness(max_delay_hours=4):
    """检查数据新鲜度"""
    # 最新数据的时间戳不能超过4小时
    pass

class DataQualitySLA:
    """数据质量SLA检查器"""
    
    def __init__(self, config_file):
        with open(config_file, 'r') as f:
            self.rules = json.load(f)
    
    def run_checks(self):
        """执行所有质量检查"""
        results = []
        
        for rule in self.rules:
            result = self._execute_rule(rule)
            results.append(result)
            
            if not result["passed"]:
                self._alert_quality_failure(rule, result)
        
        # 输出检查报告
        passed = sum(1 for r in results if r["passed"])
        total = len(results)
        print(f"\n=== 数据质量SLA报告 ===")
        print(f"通过: {passed}/{total}")
        
        for r in results:
            status = "✓" if r["passed"] else "✗"
            print(f"  {status} {r['rule_name']}: {r.get('reason', 'OK')}")
        
        # 如果关键检查失败，返回非0
        critical_failures = [r for r in results if not r["passed"] and r.get("critical")]
        if critical_failures:
            sys.exit(1)
    
    def _execute_rule(self, rule):
        """执行单个规则"""
        rule_type = rule["type"]
        rule_name = rule["name"]
        
        if rule_type == "row_count":
            current = self._get_current_count(rule["query"])
            historical = rule.get("historical_avg", current * 1.5)
            threshold = rule.get("threshold", 0.7)
            result = check_row_count(current, historical, threshold)
        elif rule_type == "null_rate":
            result = check_null_rate(rule["table"], rule["column"])
        else:
            result = {"passed": True, "reason": "Unknown rule type"}
        
        result["rule_name"] = rule_name
        result["critical"] = rule.get("critical", False)
        return result
    
    def _get_current_count(self, query):
        """执行查询获取当前统计值"""
        import subprocess
        result = subprocess.run(
            f'hive -e "{query}"',
            shell=True, capture_output=True, text=True
        )
        try:
            return int(result.stdout.strip().split('\n')[-1])
        except:
            return 0
    
    def _alert_quality_failure(self, rule, result):
        """质量失败告警"""
        print(f"⚠️  数据质量告警: {rule['name']}")
        print(f"   原因: {result['reason']}")

# ===== 配置文件示例 =====
# data_quality_config.json
config = {
    "rules": [
        {
            "name": "订单量检查",
            "type": "row_count",
            "query": "SELECT COUNT(*) FROM ods.orders WHERE dt='${TODAY}'",
            "historical_avg": 50000,
            "threshold": 0.7,
            "critical": True
        },
        {
            "name": "用户登录量检查",
            "type": "row_count",
            "query": "SELECT COUNT(*) FROM dwd.user_login WHERE dt='${TODAY}'",
            "historical_avg": 200000,
            "threshold": 0.5,
            "critical": False
        }
    ]
}

if __name__ == '__main__':
    sla = DataQualitySLA("data_quality_config.json")
    sla.run_checks()
```

#### 步骤4：SLA监控大盘

**目标**：编写脚本生成SLA满足率统计。

```bash
#!/bin/bash
# sla_report.sh —— SLA统计报告

echo "=== Azkaban SLA统计报告 ==="
echo "时间: $(date)"
echo ""

# 统计最近30天的Flow执行情况
mysql -h prod-db -u azkaban -p'xxx' azkaban -e "
SELECT 
    flow_id,
    COUNT(*) AS total_executions,
    SUM(CASE WHEN status='SUCCEEDED' THEN 1 ELSE 0 END) AS success_count,
    SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) AS fail_count,
    ROUND(AVG((end_time - start_time)/1000/60), 1) AS avg_duration_min,
    ROUND(MAX((end_time - start_time)/1000/60), 1) AS max_duration_min,
    CONCAT(ROUND(SUM(CASE WHEN status='SUCCEEDED' THEN 1 ELSE 0 END)/COUNT(*)*100, 1), '%') AS success_rate
FROM execution_flows
WHERE start_time > UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 30 DAY)) * 1000
  AND submit_user = 'azkaban'  -- 排除手动执行
GROUP BY flow_id
HAVING total_executions > 5
ORDER BY success_rate ASC;
"

echo ""
echo "=== SLA不达标的Flow（成功率 < 99%） ==="
mysql -h prod-db -u azkaban -p'xxx' azkaban -e "
SELECT 
    flow_id,
    COUNT(*) AS executions,
    CONCAT(ROUND(SUM(CASE WHEN status='SUCCEEDED' THEN 1 ELSE 0 END)/COUNT(*)*100, 1), '%') AS rate
FROM execution_flows
WHERE start_time > UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 30 DAY)) * 1000
GROUP BY flow_id
HAVING rate < '99%'
ORDER BY rate ASC;
"
```

### 3.3 测试验证

```bash
# 创建测试Flow验证SLA
curl -b cookies.txt -X POST "http://localhost:8081/executor?ajax=executeFlow" \
  --data "project=sla_test&flow=sla_demo"

# 手动触发SLA检查
python3 data_quality_sla.py
```

## 4. 项目总结

### SLA监控能力矩阵

| 监控维度 | Azkaban原生 | 自定义扩展 | 第三方工具 |
|---------|-----------|----------|----------|
| 时间SLA (finish_in) | ★★★ | — | ★★★ |
| 数据量SLA | ★☆☆ | ★★★ | ★★★ |
| 质量SLA | ✗ | ★★★ | ★★★ |
| 告警升级 | ★☆☆ | ★★★ | ★★★ |
| SLA大盘 | ✗ | ★★☆ | ★★★ |

### 适用场景

- **适用**：核心数据管道的SLA保障、金融/电商等对数据时效性敏感的行业
- **不适用**：非关键的探索性分析任务、一次性临时任务

### 注意事项

- SLA的`finish_in`是从Flow提交时间开始计时，不是从第一个Job开始
- SLA邮件告警依赖SMTP配置正确
- 数据质量检查Job应该被设置为`retries=0`（质量不达标不是重试能解决的）
- 告警升级系统需要考虑自身的可用性

### 常见踩坑经验

1. **finish_in:2h实际可能超过2小时**：SLA检查不是连续的，Azkaban默认每5分钟检查一次。如果Flow在2h01min完成，SLA规则可能不会触发。
2. **周末的SLA不应与工作日相同**：周末数据量可能只有平日的10%，用同样阈值会误报。解决：在SLA规则中加入日期判断。
3. **告警升级中的"死循环"**：Flow凌晨2点开始，4小时超时触发告警升级，但凌晨6点没人响应，系统一直发告警直到Flow手动被Kill。

### 思考题

1. 如何实现"自适应SLA"——根据历史数据自动调整阈值（比如过去30天的平均耗时+3倍标准差），而不是硬编码一个固定值？
2. Azkaban的SLA是Flow级别的。如果需要Job级别的SLA（比如"Spark作业的GC时间不能超过总时间的20%"），如何实现？
