# 背景

# 第16章 ILM 与数据分层：Hot-Warm-Cold 成本治理

时序数据会持续增长，手工管理索引生命周期很快失控。ILM 的价值在于把“经验操作”变成“自动策略”，稳定成本与性能。

## 本章目标
- 用生命周期策略自动管理时序数据。
- 在性能与成本间取得长期平衡。

## 1. ILM 解决什么问题
时序数据（日志、指标）天然会增长，  
ILM 可自动执行滚动、迁移、删除，减少人工运维。

## 2. 常见分层策略
- Hot：高频写入与查询
- Warm：中频查询，成本优先
- Cold/Frozen：低频查询，长期保留

## 3. 关键动作
- rollover：索引滚动切分
- shrink：减少分片
- delete：按保留期删除

## 4. 落地建议
- 保留策略要与业务查询窗口一致。
- 策略变更先在测试集群验证。

# 总结
- 生命周期治理是 ES 长期稳定运行的关键。
- 自动化策略优于手工操作。

## 练习题
1. 为日志数据设计 7/30/90 天生命周期策略。  
2. 比较启用 ILM 前后的存储成本变化。  
3. 说明何时适合做索引 shrink。  

## 实战（curl）

```bash
# 创建 ILM 策略
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/_ilm/policy/logs_policy" \
  -H "Content-Type: application/json" \
  -d '{
    "policy":{"phases":{
      "hot":{"actions":{"rollover":{"max_size":"20gb","max_age":"1d"}}},
      "delete":{"min_age":"30d","actions":{"delete":{}}}
    }}
  }'

curl -u "$ES_USER:$ES_PASS" "$ES_URL/_ilm/policy/logs_policy?pretty"
```

## 实战（Java SDK）

```java
client.ilm().getLifecycle(g -> g.name("logs_policy"));
```

