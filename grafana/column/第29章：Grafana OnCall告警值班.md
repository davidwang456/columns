# 第29章：Grafana OnCall告警值班

## 1. 项目背景

"凌晨3点数据库挂了，运维组5个人都在睡觉——因为不知道今晚谁是值班人。告警发到了微信群但没人认领。最后是CTO被业务方电话叫醒才处理了问题，第二天早上全公司开会复盘。"

这家公司告警有了、监控有了、SLO也建了，但卡在了"最后一公里"——告警到人。Grafana Alerting负责"发现问题"和"发送通知"，但"谁来响应？怎么排班？没人处理怎么办？"这些问题需要一个告警值班管理工具来回答。

Grafana OnCall（前身为Amixr）正是填补这个空白的组件。它提供了排班管理（Schedule）、告警升级（Escalation Chain）、值班接管（Shift Swap）、电话/短信通知等功能，与Grafana Alerting深度集成，完成从"告警触发"到"问题处理"的完整闭环。

本章将搭建一套完整的OnCall体系，让"告警有人响应"不再是一句空话。

## 2. 项目设计

**小胖**（看着手机上的137条未读告警消息）：大师，告警群里有137条未读消息，都是昨晚发的。没人看，因为没人知道谁值班。老板很生气，说"告警体系建了跟没建一样"。

**大师**：告警≠响应。告警只是"通知发生了一件事"，响应需要"人去做一件事"。OnCall解决的就是这个断层。

**小白**：OnCall具体怎么工作的？

**大师**：OnCall有四个核心概念：

**Schedule（排班）**：定义谁在什么时间段值班。比如"张三负责周一至周五 9:00-18:00，李四负责夜间和周末"。

**Escalation Chain（告警升级链）**：告警没人处理时自动升级。经典的3级链条：
```
第0步：通知当前值班人（Slack/钉钉）
       ↓ 5分钟没人认领(Acknowledge)
第1步：通知值班人 + 打电话
       ↓ 10分钟还没人认领
第2步：通知值班人主管 + 发短信给所有运维
       ↓ 15分钟还没人认领
第3步：通知运维总监 + CTO
```

**Integration（告警集成）**：OnCall接收来自Grafana Alerting（或其他告警源）的告警，然后按Escalation Chain分发给对应的人。

**Alert Group（告警分组）**：OnCall会把同类告警自动分组，避免重复通知。

**小胖**：和PagerDuty、Opsgenie比呢？

**大师**：PagerDuty和Opsgenie功能更全但价格不菲（人均$20+/月）。OnCall开源免费，与Grafana原生集成，告警到Dashboard只点击一下。对于中小团队，OnCall完全够用。

关键优势：告警通知中直接包含Grafana Dashboard链接、告警详情、一键静默按钮。不需要在PagerDuty→Grafana之间来回切换。

**小白**：具体怎么接入Grafana Alerting？

**大师**：两种方式：

**方式一：Grafana Alerting → OnCall (Webhook)**
在Grafana Alerting的Contact Point中直接配置OnCall的Webhook URL。这是最简洁的集成。

**方式二：Alertmanager → OnCall**
Alertmanager作为中间层，把Prometheus告警转发给OnCall。

方式一更直接——从Grafana Alerting触发→OnCall处理→通知值班人→值班人点击链接打开Grafana Dashboard。

**技术映射**：Schedule = 值班表（每月排好谁哪天值班），Escalation Chain = 求救接力棒（不上就传下一个），Acknowledge = 接警确认（"收到，我来处理"），Phone notification = 夺命连环call（再不接就打电话）。

## 3. 项目实战

**环境准备**

在Docker Compose中添加OnCall（简化部署用开源版）：

OnCall部署较复杂，需要PostgreSQL + Redis + RabbitMQ + OnCall Engine + OnCall Web。简化起见，使用官方Helm或Docker Compose：

```yaml
  oncall-engine:
    image: grafana/oncall-engine:latest
    container_name: oncall-engine
    environment:
      - DATABASE_URL=postgres://oncall:oncall123@postgres:5432/oncall
      - REDIS_URI=redis://redis:6379/1
      - BROKER_URL=amqp://rabbitmq
    ports:
      - "8080:8080"

  oncall-celery:
    image: grafana/oncall-engine:latest
    command: celery -A engine worker
    depends_on:
      - oncall-engine

  rabbitmq:
    image: rabbitmq:3.12-alpine
    container_name: rabbitmq
    ports:
      - "5672:5672"
```

**步骤一：基础排班配置**

OnCall → Schedules → Create Schedule：

- Name: `运维值班`
- Timezone: `Asia/Shanghai`

添加值班人员和时间段：
- 周一至周五 9:00-18:00: `Zhang San`
- 周一至周五 18:00-9:00（夜间）: `Li Si`
- 周六日全天: `Wang Wu`

支持iCal导入（从Google Calendar/Outlook同步）。

**步骤二：配置Escalation Chain**

OnCall → Escalation Chains → Create：

```
Step 0: Notify on Slack + DingTalk
  - Wait: 0 minutes (立即通知)
  
Step 1: Phone call
  - Wait: 5 minutes (5分钟后未确认升级)
  - Action: Call on-call person
  
Step 2: Escalate to backup
  - Wait: 10 minutes
  - Action: Notify backup person + Team lead
  
Step 3: Escalate to manager
  - Wait: 15 minutes
  - Action: Call manager
```

关键等待时间设置原则：
- 凌晨需要快响应（0min→5min→10min）
- 工作时间可以慢一点（0min→15min→30min）

**步骤三：Grafana Alerting集成OnCall**

在OnCall中创建Integration：
- Integration type: `Grafana Alerting`
- 记录生成的Webhook URL

在Grafana Alerting Contact Point中添加：
- Integration: `Webhook`
- URL: `https://oncall.example.com/integrations/v1/grafana/xxx`

在Notification Policy中设置：
- 所有`severity=critical`的告警 → Contact Point: `OnCall`
- 所有`severity=warning`的告警 → Contact Point: `Slack`（不打扰值班人）

**步骤四：电话通知配置**

OnCall支持通过Twilio/Vonage/Zvonok等电话服务商发起电话通知：

```yaml
# oncall配置
TWILIO_ACCOUNT_SID: "ACxxx"
TWILIO_AUTH_TOKEN: "xxx"
TWILIO_PHONE_NUMBER: "+1234567890"
```

配置后，Escalation Chain中的"Phone call"步骤会真正拨打电话，播放语音："You have a Grafana alert: Service Order-Service is down. Press 1 to acknowledge."

**步骤五：Acknowledge（告警认领）**

值班人接到通知后有3种响应方式：
1. **从Slack/钉钉回复**：回复`/ack`或点击按钮
2. **从OnCall Web UI**：点击"Acknowledge"
3. **电话按键**：按1认领

认领后：
- 告警状态变为Acknowledged
- Escalation Chain停止升级
- 其他值班人收到通知："张三正在处理"
- 如果超过一定时间（如30分钟）仍未解决，可以重新触发升级

**步骤六：Shift Swap（换班）**

值班人如果临时有事：
1. OnCall → My schedule → Request shift swap
2. 选择要换的时间段
3. 选择替换人
4. 替换人确认 → 排班自动更新

这个功能防止了"今天是我值班但我有事，口头找同事替——同事也忘了——晚上告警没人处理"的经典事故。

**常见坑点**
1. **时区混乱**：值班人、Grafana Server、OnCall Server三者时区不一致会导致"排班表"对不上实际时间。统一用UTC或固定时区。
2. **电话通知启用后不要滥用**：凌晨3点的Warning级别告警也打电话 → 值班人第二天上班直接提离职。
3. **OnCall自身挂了**：OnCall服务宕机意味着所有告警升级都失效。OnCall自身也需要监控和告警。

**步骤七：实战——完整的凌晨故障响应流程**

凌晨2:15，order-service数据库连接池耗尽。

**2:15:00** - Grafana Alerting触发："数据库连接池使用率>95%"
**2:15:05** - 告警进入OnCall → Escalation Chain Step 0：Slack通知值班人张三
**2:15:10** - 张三手机Slack收到消息："[FIRING] 数据库连接池告警 / order-service / value: 97%"
**2:15:30** - 消息中包含Dashboard直达链接，张三点击后在手机上看到实时监控
**2:16:00** - 张三在Slack中回复`/ack`确认接警
**2:16:05** - 告警状态变为Acknowledged，升级链停止
**2:16:30** - 张三打开Grafana Dashboard → Metric面板显示连接池97%
**2:17:00** - 张三点击Exemplar → 跳转Tempo → 看到大量慢查询Span
**2:18:00** - 张三点击Span关联的Loki日志 → 发现有一条异常日志："slow query: 23s, SQL: SELECT * FROM orders WHERE ..."
**2:19:00** - 定位到缺少索引导致全表扫描 → 通知DBA加索引
**2:22:00** - DBA添加索引后连接池恢复正常
**2:23:00** - 告警自动恢复（Resolved）

MTTR = 8分钟（从告警到恢复）

对比没有OnCall之前：告警发到微信群→没人认领→1小时后CTO被叫醒→30分钟后找到DBA→总耗时90分钟。

**告警响应演练脚本**：
```bash
#!/bin/bash
# chaos-alert-drill.sh - 每月告警响应演练

echo "=== 告警响应演练开始 ==="
SERVICE=$1

# 1. 注入模拟故障
echo "注入${SERVICE}模拟故障..."
kubectl scale deployment $SERVICE --replicas=0

# 2. 等待告警触发
echo "等待告警触发（最多3分钟）..."
sleep 180

# 3. 检查OnCall中是否有人Ack
ACK_STATUS=$(curl -s -H "Authorization: Bearer $TOKEN" \
  "http://oncall:8080/api/v1/alert_groups?service=$SERVICE" | \
  jq '.results[0].acknowledged')

START_TIME=$(date -d '3 minutes ago' +%s)
ACK_TIME=$(date +%s)
MTTR=$(( (ACK_TIME - START_TIME) / 60 ))

echo "告警确认状态: $ACK_STATUS"
echo "响应时间: ${MTTR}分钟"

if [ "$ACK_STATUS" = "true" ] && [ $MTTR -le 5 ]; then
    echo "✅ 演练通过：5分钟内完成告警确认"
else
    echo "❌ 演练失败：超时未确认或响应太慢"
fi

# 4. 恢复服务
kubectl scale deployment $SERVICE --replicas=3
```

## 4. 项目总结

**OnCall vs PagerDuty vs 手动值班**

| 维度 | OnCall | PagerDuty | 手动微信群 |
|------|--------|-----------|----------|
| 费用 | 免费开源 | $20+/人/月 | 免费 |
| Grafana集成 | 原生深度 | 通过Webhook | 无 |
| 排班管理 | 支持 | 支持 | 靠人记忆 |
| 升级链 | 支持 | 支持 | 无 |
| 电话通知 | 需配置Twilio | 内置 | 无 |
| 告警认领 | 支持 | 支持 | 靠人回复"收到" |

**适用场景**
1. ≤30人的运维/开发团队（OnCall开源版完全够用）
2. 需要24×7值班轮换的团队
3. 告警经常"没人认领"需要升级链
4. 已在使用Grafana生态的组织

**注意事项**
1. OnCall是Grafana生态的"最后一块拼图"，需要前面已经有告警规则
2. 至少配置2级升级链——1个人挂了的概率比你想象的高
3. 定期做"告警响应演练"——真实的凌晨电话打过来，值班人是不是真的会接？

**思考题**
1. 如果你既要给国内团队值班（UTC+8），又要给海外团队值班（UTC-8），怎么设计排班？
2. OnCall的Escalation Chain中如果配置了"15分钟没人认领→升级通知主管"，但主管恰好也是当晚的Backup值班人——会出现什么问题？
