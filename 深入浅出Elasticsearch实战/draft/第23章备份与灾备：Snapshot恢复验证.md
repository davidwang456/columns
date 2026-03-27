# 背景

# 第23章 备份与灾备：Snapshot 恢复验证

“有备份”不等于“可恢复”。只有经过恢复演练验证的快照策略，才能在真实事故中发挥作用。

## 本章目标
- 建立“可恢复”的备份体系。
- 用演练验证灾备方案是否有效。

## 1. 备份不等于可恢复
很多团队有 snapshot，但从未恢复演练。  
没有演练的备份，在事故中等同于不可靠。

## 2. Snapshot 基础策略
- 明确备份频率与保留周期。
- 选择可靠的仓库与权限控制。
- 记录每次备份任务结果。

## 3. 恢复演练重点
- 恢复耗时是否满足 RTO
- 数据回溯点是否满足 RPO
- 恢复后查询与写入是否正常

## 4. 工程建议
- 定期自动化演练。
- 演练后更新应急手册与责任人。

# 总结
- 备份的价值在“恢复成功率”，不在“备份数量”。
- 灾备是持续治理，不是一次性项目。

## 练习题
1. 制定你们团队的 snapshot 周期与保留策略。  
2. 设计一次全量恢复演练流程。  
3. 定义恢复成功的验收标准。  

## 实战（curl）

```bash
# 1) 创建仓库（示例：fs）
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/_snapshot/local_repo" \
  -H "Content-Type: application/json" \
  -d '{"type":"fs","settings":{"location":"/tmp/es_backup"}}'

# 2) 创建快照
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/_snapshot/local_repo/snap_001?wait_for_completion=true"

# 3) 查看快照
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_snapshot/local_repo/snap_001?pretty"
```

## 实战（Java SDK）

```java
client.snapshot().create(c -> c.repository("local_repo").snapshot("snap_001").waitForCompletion(true));
```

