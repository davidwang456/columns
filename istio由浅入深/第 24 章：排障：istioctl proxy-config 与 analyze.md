# 第 24 章：排障：istioctl proxy-config 与 analyze

**受众提示**：运维/oncall 必备；开发自助；测试记录复现与期望输出。

**实验基线**：[LAB-KIT.md](LAB-KIT.md)。

---

## 1. 项目背景

配置「看起来对」但行为错时，必须以 **Envoy 实际生效配置**为准。`istioctl proxy-config` 系列导出 **listener/route/cluster/endpoint/bootstrap**，是**最高信源**。`istioctl analyze` 做静态检查。推广目标：**全员会基础三板斧**。

---

## 2. 项目设计：大师与小白的对话

**小白**：为啥不直接 `kubectl exec` 进 Envoy admin？

**大师**：可以，但 `istioctl` **封装**了常用视图，减少误操作。

**小白**：route 和 cluster 先看谁？

**大师**：**route** 决定匹配与目的地；**cluster** 决定 upstream 与 LB；**endpoint** 看具体 IP。

**小白**：analyze 全绿就安全吗？

**大师**：**静态**检查；**运行时**还要看指标与日志。

**要点清单**

1. 建立 **排障 runbook**：症状 → 命令。
2. **对比** 两版本 proxy-config **diff**（升级时）。
3. 与 **proxy-status** 联用看 **同步**。

---

## 3. 项目实战

```bash
export POD=$(kubectl get pod -l app=productpage -o jsonpath="{.items[0].metadata.name}")

istioctl proxy-status $POD.default

istioctl proxy-config listener $POD -n default
istioctl proxy-config route $POD -n default
istioctl proxy-config cluster $POD -n default
istioctl proxy-config endpoints $POD -n default

istioctl analyze -n default
```

**练习**：修改 `VirtualService` 后，观察 **route** 变化时间差。

---

## 4. 项目总结

**优点**

- **快速**定位「配置未生效」类问题；
- **可脚本化**对比。

**缺点**

- 输出**冗长**，需练习；
- 非常深层问题仍要 **Envoy debug log**。

**适用场景**

- **503/404**、**路由错误**、**升级后**行为变化。

**注意事项**

- **多集群**下指定 **context**；
- **权限**：RBAC 限制 `istioctl` 使用者。

**常见踩坑**

1. **现象**：看了错的 Pod。**原因**：**多副本**旧 Pod。**处理**：确认 **pod name**。
2. **现象**：route 不更新。**原因**：**istiod** 或 **Sidecar** 连接问题。**处理**：`proxy-status`。
3. **现象**：输出与文档不一致。**原因**：**版本 skew**。**处理**：对齐 `istioctl` 版本。

---

### 4.1 再谈一个场景：多副本

`proxy-config` 必须指向**具体 Pod**；滚动期间新旧 Pod 并存，若看错对象会得出错误结论。养成习惯：**先从用户报告里取 trace/pod 名**。

### 4.2 与 Git diff

把关键路由配置纳入 Git 后，可在变更前后导出 `proxy-config route` **脱敏 diff**，作为高级变更评审材料。

---

## 附：自测与练习

### 自测题

1. `proxy-status` 与 `proxy-config` 分别回答什么问题？
2. route/cluster/endpoints 阅读的推荐顺序？
3. `istioctl analyze` 能发现哪些运行时问题？

### 动手作业

刻意制造一条错误路由，先用 `analyze` 再用 `proxy-config` 定位，记录最短路径步骤为团队 Runbook。

**延伸阅读**：Istio *Debugging Envoy*；源码入口 `istioctl/pkg/proxyconfig`。

---

### 4.3 输出脱敏与分享

`proxy-config` 可能含内网 IP 与内部服务名；分享前**脱敏**。建议团队建立**标准脱敏脚本**。

### 4.4 与升级对比

升级 istio 前后对同一 workload 导出配置做 **diff**（脱敏），作为变更证据附件。

---

### 4.5 本章小结：把知识变成「可执行」

推广 Istio 时，最容易失败的不是「不会配」，而是「配了没人敢动」。建议每章学完后，在团队看板增加一张卡片：**谁能在生产执行与本章相关的变更**、**需要哪些审批**、**回滚命令是什么**。技术文档若不能映射到流程，就仍是幻灯片。

### 4.6 与第 24、31、36 章的联动提示

- **排障**：把 `istioctl analyze` 与 `proxy-config` 写进 oncall 第一步（第 24 章）。
- **交付**：把安装与 values 纳入 GitOps（第 31 章）。
- **治理**：把试点范围、预算与里程碑写成公开路线图（第 36 章）。

---

### 本章落地清单（可复制到会议纪要）

1. **本章目标（一句话）**：把技术对象翻译成团队能执行的变更或制度。
2. **责任人/协作方**：开发 ___；运维 ___；测试 ___；安全 ___（按需）。
3. **验证方式**：写出「输入 → 期望输出 → 实际输出」至少 1 条；必要时附指标/截图要求。
4. **风险与边界**：哪些场景本章方法不适用？哪些必须升级审批？
5. **回滚**：列出回滚动作（删资源/改权重/回版本）与预计耗时。
6. **与相邻章节关系**：上一章依赖 ___；下一章继续 ___。

### 写给不同角色的一句话

- **开发**：把「业务语义」与「网格语义」对齐，避免把幂等/鉴权全推给平台。
- **运维**：把变更纳入窗口与 GitOps，任何手工 kubectl 都要留痕。
- **测试**：把策略生效写成可重复断言，而不是一次性手工点点点。

### 常见误解澄清（防抬杠小抄）

- 「上了网格就不用关心网络」：错误；网络/CNI/防火墙仍是底座。
- 「网格能自动让系统高可用」：错误；高可用需要架构、容量、数据与演练。
- 「配置在 YAML 里就是真相」：不完整；真相是 **集群实际生效配置**（见 istioctl proxy-config）。
