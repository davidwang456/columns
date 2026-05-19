# 第39章：Milvus SRE 落地与生产故障演练

> **定位**：把 Milvus 纳入企业级稳定性体系。
> **版本**：Milvus 2.5.x
> **源码关联**：deployments/、configs/milvus.yaml、internal/querycoordv2/handoff.go

---

## 1. 项目背景

运维老周的 Milvus 集群已经稳定运行 6 个月。CTO 要求将所有核心服务纳入 SRE 体系——必须有明确的 SLO（Service Level Objective）、灾备方案、故障演练记录和 Runbook。

老周的第一反应是"Milvus 不就是个数据库吗，MySQL 的 SRE 套路搬过来不就行了"。但很快他发现差异巨大：

1. **SLO 定义不同**：MySQL 的 SLO 是"可用性 99.9% + 查询延迟 < 10ms"。Milvus 的搜索延迟受数据量和索引类型影响极大，不能用一个固定值。
2. **灾备方案不同**：MySQL 用主从复制 + binlog 回放。Milvus 用 etcd 快照 + 对象存储同步 + 索引重建。
3. **故障演练不同**：MySQL 演练切主、磁盘满、连接打满。Milvus 需要演练 QueryNode OOM、etcd 异常、对象存储抖动、Compaction 积压——这些场景 MySQL 根本没有。
4. **监控告警不同**：MySQL 看 QPS/连接数/慢查询。Milvus 还要看 Segment 数量、索引构建队列、MQ 积压。

本章将建立 Milvus 的 SRE 体系：SLO 设计、灾备方案、四维故障演练和 Runbook。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：SLO 设计——可用性、延迟、写入成功率**

*（老周在 Excel 里列了 15 个指标，不知道哪些该定为 SLO）*

**小胖**（看着 15 行指标头晕）："SLO 定这么多？不如就定'99.9% 可用性'——"

**大师**："仅有'可用性'不够——服务活着但搜一次要 5 秒，算不算 SLA 违约？SLO 需要覆盖四个维度。"

**大师**（画出 SLO 四维模型）：

```
Milvus SLO 四维模型:

┌─────────────────────────────────────────────────────────────┐
│ SLO 维度          │ 指标               │ 目标值              │
├───────────────────┼────────────────────┼───────────────────┤
│ ① 可用性          │ 组件 uptime        │ ≥ 99.9% (月)       │
│                   │ Proxy 可达性       │ ≥ 99.99%           │
├───────────────────┼────────────────────┼───────────────────┤
│ ② 搜索延迟        │ Search P95         │ < 100ms (基准)     │
│                   │ Search P99         │ < 500ms            │
│                   │ (注: 随数据量调整)   │                    │
├───────────────────┼────────────────────┼───────────────────┤
│ ③ 写入成功率      │ Insert Error Rate  │ < 0.1%             │
│                   │ Insert P95 Latency │ < 50ms (客户端)    │
├───────────────────┼────────────────────┼───────────────────┤
│ ④ 恢复时间        │ RTO (故障恢复时间)  │ < 5 分钟           │
│                   │ RPO (数据丢失窗口)  │ < 5 秒             │
└───────────────────┴────────────────────┴───────────────────┘

错误预算 (Error Budget):
  可用性 99.9% → 月允许宕机: 43 分钟
  可用性 99.99% → 月允许宕机: 4.3 分钟
```

**大师**："SLO 的关键原则——"

| 原则 | 说明 |
|------|------|
| **不要太严格** | 99.99% 的成本是 99.9% 的 3-5 倍 |
| **基于历史数据** | 先跑 1 个月收集实际指标，再定 SLO |
| **分业务等级** | 核心业务 99.9%，非核心 99% |
| **留错误预算** | 99.9% 意味着每月可以宕机 43 分钟——用完就冻结上线 |

> **技术映射**：SLO = 保险公司合同（保什么、保到什么程度、超出怎么赔）；错误预算 = 理赔额度（用完今年就不能再出险了）；分级 SLO = VIP 和普通用户的保障级别不同。

---

**第二幕：灾备方案——备份、跨集群恢复与对象存储容灾**

**小胖**："灾备不就是备份恢复吗？第 13 章不是讲过了？"

**大师**："第 13 章讲的是'怎么备份'。SRE 层面的灾备要解决'极端情况下怎么恢复'。三个演练场景——"

```
灾备三层架构:

第一层: Collection 级备份 (日常)
  工具: milvus-backup
  频率: 每日全量 + 每小时增量
  RPO: < 1 小时
  RTO: < 30 分钟 (恢复 + 建索引)

第二层: etcd 快照 (元数据容灾)
  工具: etcdctl snapshot save
  频率: 每 6 小时
  RPO: < 6 小时
  RTO: < 1 小时 (恢复 etcd + 重启 Coordinator)

第三层: 对象存储跨区域复制 (存储层容灾)
  工具: MinIO Mirror / S3 Cross-Region Replication
  频率: 实时异步
  RPO: < 1 分钟
  RTO: < 5 分钟 (切换到备用 Bucket)

灾备演练 Checklist:
  □ 从备份恢复一个 Collection 到隔离环境
  □ 验证恢复后的数据量和搜索一致性
  □ etcd 节点全挂后从快照恢复
  □ 对象存储主 Bucket 不可用后切换到备用
  □ 记录每次演练的耗时和改进点
```

**大师**："灾备的核心指标——"

| 指标 | 含义 | 目标 |
|------|------|------|
| **RPO** (Recovery Point Objective) | 最大可容忍的数据丢失量 | < 5 分钟 |
| **RTO** (Recovery Time Objective) | 最大可容忍的恢复时间 | < 30 分钟 |
| **演练频率** | 多久验证一次灾备方案 | 每季度至少 1 次 |

> **技术映射**：备份 = 定期体检；灾备演练 = 消防演习（平时不练，真着火就傻眼）；RPO = 能接受丢多近的数据（丢 5 分钟的 vs 丢 1 天的）；RTO = 扑灭火灾需要多长时间。

---

**第三幕：四维故障演练——etcd/MQ/QN OOM/存储膨胀**

**小胖**："故障演练具体怎么搞？是手动删 Pod 还是用 Chaos Engineering 工具？"

**大师**："四维故障演练，从小到大——"

```
Milvus 故障演练矩阵:

┌──────────────────────────────────────────────────────────────┐
│ 故障类型            │ 模拟方式            │ 观察指标            │
├────────────────────┼────────────────────┼────────────────────┤
│ ① etcd 异常         │ 杀 etcd Pod         │ Coordinator 是否切换 │
│   (1 节点宕机)      │ 或 iptables DROP    │ Proxy 能否降级读缓存 │
│                    │                    │ 恢复后元数据一致性   │
├────────────────────┼────────────────────┼────────────────────┤
│ ② 对象存储抖动      │ tc qdisc 加延迟     │ DataNode Flush 是否  │
│   (MinIO 慢 IO)     │ (模拟 5s 延迟)      │ 超时但恢复后重试成功  │
│                    │                    │ QueryNode 搜索是否   │
│                    │                    │ 降级但仍有结果        │
├────────────────────┼────────────────────┼────────────────────┤
│ ③ QueryNode OOM    │ 写入超大 Segment    │ QueryCoord 心跳检测  │
│   (内存耗尽)        │ 填满 QN 内存        │ Handoff 耗时         │
│                    │                    │ 搜索可用性恢复时间    │
├────────────────────┼────────────────────┼────────────────────┤
│ ④ 索引任务积压      │ 关闭 IndexNode     │ 数据可搜索但走暴力    │
│   (IndexNode 宕机)  │                    │ 新建索引积压数量     │
│                    │                    │ IndexNode 恢复后追赶  │
└────────────────────┴────────────────────┴────────────────────┘

每次演练的标准流程:
  ① 公告: 提前 1 天通知相关人员
  ② 执行: 按 Runbook 逐步操作
  ③ 观察: 记录各项指标变化
  ④ 复盘: 写演练报告 → 更新 Runbook
  ⑤ 改进: 修复演练中发现的问题
```

**大师**："Runbook 模板——"

```
故障: QueryNode OOM
ALERT: milvus_querynode_mem_usage > 95%
严重级别: P2 (影响搜索性能, 不中断服务)

排查步骤:
  1. 确认告警 QN: kubectl get pods -n milvus | grep querynode
  2. 查看 OOM 日志: kubectl logs <pod> --previous | grep OOM
  3. 检查 Segment 分布: kubectl exec querycoord-0 -- milvus_cli describe_collection <name>
  4. 确认副本是否接替: 搜索测试 → 验证结果正常

恢复步骤:
  1. 如果有 Replica → 自动恢复, 无需手动操作 (观察即可)
  2. 如果无 Replica → 手动重启 QN: kubectl delete pod <pod>
  3. 如果频繁 OOM → 临时扩容: kubectl scale deployment querynode --replicas=N+1
  4. 长期修复 → 减小 Segment 大小 / 换 IVF_SQ8 / 增加 QN 内存

升级条件 (触发 P1):
  如果搜索错误率 > 5% 且持续 > 5 分钟 → 升级为 P1 → 通知 on-call 工程师
```

---

## 3. 项目实战

### 3.1 实战目标

设计并执行一次 Milvus 生产故障演练，输出 Runbook 和复盘报告。

### 3.2 分步实现

#### 步骤 1：故障演练脚本

```python
# step1_chaos_test.py
"""Milvus 故障演练自动化脚本"""
import subprocess
import time
from datetime import datetime

class MilvusChaosTest:
    """Milvus 混沌测试"""
    
    def __init__(self, namespace="milvus-prod"):
        self.ns = namespace
        self.log = []
    
    def log_event(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        self.log.append(entry)
        print(entry)
    
    def test_querynode_oom(self):
        """演练 1: QueryNode OOM"""
        self.log_event("=== 演练 1: QueryNode OOM ===")
        
        # 1. 记录故障前状态
        self.log_event("故障前: 搜索测试...")
        # (假设有搜索测试脚本)
        
        # 2. 注入故障: 删除一个 QueryNode Pod
        self.log_event("注入故障: kubectl delete pod querynode-1")
        subprocess.run(["kubectl", "delete", "pod", "-n", self.ns,
                       "-l", "app.kubernetes.io/component=querynode",
                       "--wait=false"])
        
        # 3. 观察恢复
        self.log_event("观察恢复...")
        for i in range(12):  # 最多等 60 秒
            time.sleep(5)
            result = subprocess.run(
                ["kubectl", "get", "pods", "-n", self.ns, "-l",
                 "app.kubernetes.io/component=querynode"],
                capture_output=True, text=True
            )
            if "Running" in result.stdout:
                self.log_event(f"  T+{(i+1)*5}s: Pod 恢复运行")
                break
        
        # 4. 验证搜索
        self.log_event("恢复后: 搜索测试...")
        
        return self.log
    
    def test_etcd_failure(self):
        """演练 2: etcd 节点宕机"""
        self.log_event("=== 演练 2: etcd 节点宕机 ===")
        
        self.log_event("注入故障: 删除 1 个 etcd Pod (3节点集群中1个)")
        subprocess.run(["kubectl", "delete", "pod", "-n", self.ns,
                       "etcd-1", "--wait=false"])
        
        self.log_event("观察: etcd 集群自动恢复 (Raft 选举)")
        time.sleep(10)
        
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", self.ns, "etcd-1"],
            capture_output=True, text=True
        )
        self.log_event(f"etcd-1 状态: {result.stdout.strip()}")
        
        return self.log

chaos = MilvusChaosTest()
chaos.test_querynode_oom()
```

#### 步骤 2：Runbook 生成器

```python
# step2_runbook_gen.py
"""生成 Milvus 故障 Runbook"""
def generate_runbook():
    runbook = """
╔══════════════════════════════════════════════════════════════╗
║              Milvus 生产故障 Runbook                         ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  故障 1: 搜索延迟突增 (P95 > 500ms)                           ║
║  ├─ 查看 Grafana: Milvus Overview → Search P95              ║
║  ├─ 定位慢 QN: QueryNode Deep Dive → 哪个 QN 的 P95 高       ║
║  ├─ 检查该 QN 的 Segment 数 → 是否 > 1000?                   ║
║  ├─ 如果是 → 手动触发 Compaction                             ║
║  └─ 如果不是 → 检查 QN 内存/CPU → 扩容 QN                    ║
║                                                              ║
║  故障 2: 写入失败率突增 (> 1%)                                 ║
║  ├─ 检查 MQ 积压: StreamingNode consumer lag                 ║
║  ├─ 检查 DataNode: 是否有 DN 宕机?                           ║
║  ├─ 检查对象存储: MinIO/S3 是否健康?                          ║
║  └─ 扩大写入批量大小 / 增加 DataNode                          ║
║                                                              ║
║  故障 3: 组件宕机                                             ║
║  ├─ Proxy 宕机: 自动切换 (多 Proxy) → 观察即可                ║
║  ├─ QueryNode 宕机: 有 Replica → 自动恢复                     ║
║  │                 无 Replica → 等 Handoff (~30s)             ║
║  ├─ Coordinator 宕机: 单点 → 立即手动重启                     ║
║  └─ etcd 宕机: 3节点中1个 → 不影响; 2个+ → 紧急修复          ║
║                                                              ║
║  故障 4: 存储空间不足 (< 10% 剩余)                             ║
║  ├─ 触发 Compaction: collection.compact()                    ║
║  ├─ 清理旧备份: 删除 > 7 天的备份                             ║
║  ├─ 扩容 PVC: kubectl patch pvc <name> -p '{...}'           ║
║  └─ 长期: 调整 GC retention 降低周期                          ║
║                                                              ║
║  值班联系:                                                    ║
║  P0 (全站不可用) → 打电话 138xxxx + 通知 CTO                   ║
║  P1 (核心功能受损) → IM + 邮件, 30min 内响应                    ║
║  P2 (非核心受损) → 邮件, 2h 内响应                              ║
╚══════════════════════════════════════════════════════════════╝
"""
    return runbook

print(generate_runbook())
```

#### 步骤 3：安全治理速查

```python
# step3_security_governance.py
"""Milvus 安全治理要点"""
print("""
Milvus 安全治理清单:

  □ 鉴权: 开启 Milvus 内置 RBAC
    - 创建用户: utility.create_user("app_readonly", "password")
    - 授予角色: utility.grant_role("readonly", "app_readonly")
  
  □ 网络隔离: 
    - Milvus 端口 19530 只对内网开放
    - 用 K8s NetworkPolicy 限制 Pod 间通信
  
  □ 审计日志:
    - 开启 Milvus audit log (configs/milvus.yaml → log.audit.enable: true)
    - 记录: 谁、什么时候、做了什么操作
  
  □ 备份加密:
    - 对象存储开启 SSE (Server-Side Encryption)
    - etcd 备份文件上传到加密的 S3 Bucket
""")
```

---

## 4. 项目总结

### 4.1 SRE 核心指标一览

| 指标 | 目标 | 监控方式 | 告警阈值 |
|------|------|---------|---------|
| 可用性 | 99.9% | `up{}` 指标 | 组件宕机 > 2min |
| P95 延迟 | < 100ms | `histogram_quantile` | > 200ms for 5min |
| 写入成功率 | > 99.9% | `insert_fail / insert_total` | > 1% for 5min |
| RTO | < 5min | 故障演练计时 | 记录每次演练耗时 |

### 4.2 注意事项

- **SLO 要基于真实数据**：不要拍脑袋定 99.99%，先跑 1 个月收集指标再定。
- **演练要有记录**：每次演练写复盘报告，形成知识积累。
- **Runbook 要保持更新**：每出现一种新故障，更新一次 Runbook。

### 4.3 思考题

1. 如果"错误预算"在本月 25 号就用完了（因为两次意外宕机），剩下 5 天应该做什么？是冻结上线还是重新评估 SLO？
2. 对象存储跨区域复制的延迟可能达到数分钟。如果主 Region 的对象存储完全不可用，切换到备用 Region 后，MinIO 中的数据可能落后 5 分钟——这对 Milvus 的搜索结果有什么影响？

---

> **下一章预告**：第40章是高级篇综合实战——从零打造企业级向量检索中台。读完本章，你应该能建立 Milvus 的完整 SRE 体系。
