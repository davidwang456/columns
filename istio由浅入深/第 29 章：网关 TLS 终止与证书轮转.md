# 第 29 章：网关 TLS 终止与证书轮转

**受众提示**：运维/安全主导证书；开发确认 HSTS 与重定向；测试验证到期与链。

**实验基线**：[LAB-KIT.md](LAB-KIT.md)。

---

## 1. 项目背景

对外 **HTTPS** 通常在 **Ingress Gateway** 终止 TLS，证书存在 **Kubernetes Secret**（`credentialName`）。轮转涉及：**提前续期**、**热加载**、**双证书**窗口、**客户端**兼容性（链不全）。网格层要区分：**网关证书**与 **istio 内部 CA**（第 26 章）。

---

## 2. 项目设计：大师与小白的对话

**小白**：Let's Encrypt 能直接挂网关吗？

**大师**：可以，常用 **cert-manager** 自动签发；注意 **HTTP-01** vs **DNS-01**。

**小白**：证书更新了要重启网关吗？

**大师**：依赖 **Envoy 热重载**与 **Secret 挂载**机制；要以**实测**为准，别靠猜。

**小白**：TLS 1.0 还能开吗？

**大师**：**不建议**；合规与 **扫描**会找你。

**要点清单**

1. **到期告警**提前 30 天；
2. **全链**与 **中间证书**检查；
3. **SNI** 多域名与 **通配符**策略。

---

## 3. 项目实战

### 3.1 Gateway HTTPS 片段（概念）

```yaml
apiVersion: networking.istio.io/v1beta1
kind: Gateway
metadata:
  name: secure-gateway
spec:
  selector:
    istio: ingressgateway
  servers:
  - port:
      number: 443
      name: https
      protocol: HTTPS
    tls:
      mode: SIMPLE
      credentialName: example-credential
    hosts:
    - "api.example.com"
```

### 3.2 Secret 创建（示例）

```bash
kubectl create -n istio-system secret tls example-credential \
  --cert=path/tls.crt --key=path/tls.key
```

### 3.3 验证

```bash
openssl s_client -connect api.example.com:443 -servername api.example.com
curl -v https://api.example.com/health
```

---

## 4. 项目总结

**优点**

- **集中**证书管理；
- 与 **网格路由**统一。

**缺点**

- **错误配置**影响所有绑定域名；
- **私钥**泄露风险。

**适用场景**

- 对外 **API**、**Web**、**移动端**入口。

**注意事项**

- **HTTP/2** 与 **gRPC**；
- **重定向** HTTP→HTTPS **循环**。

**常见踩坑**

1. **现象**：证书链不全。**原因**：**Secret** 只含叶子。**处理**：合并 **fullchain**。
2. **现象**：更新后仍旧证。**原因**：**Pod** 未 reload。**处理**：滚动或检查挂载。
3. **现象**：通配符不匹配子域。**原因**：**SNI** 与 **cert** 不一致。**处理**：修正 **hosts**。

---

### 4.1 再谈一个场景：证书到期告警

证书续期失败常在「刚好第 90 天」爆发。要把 **到期阈值告警**接到**可值班渠道**，并演练 **rollback**。

### 4.2 与 HSTS

启用 HSTS 后，短时间的证书错误会被客户端**长期记住**；变更要更谨慎。

---

## 附：自测与练习

### 自测题

1. `credentialName` 指向的 Secret 通常包含哪些文件？
2. 证书更新后为何可能需要滚动网关 Pod（依实现）？
3. 全链证书与叶子证书分别解决什么问题？

### 动手作业

用 `openssl s_client` 检查你们测试域名的链是否完整，并记录输出中的 issuer 信息。

**延伸阅读**：cert-manager；Istio *Ingress Secure Gateways*。

---

### 4.3 自动化：cert-manager

生产推荐使用 **cert-manager** + **Let's Encrypt** 或企业 CA；文档要写清 **ClusterIssuer**、**挑战方式**与**失败重试**。人工每季度 `kubectl create secret` 不可持续。

### 4.4 与客户端钉扎（pinning）

移动端 certificate pinning 可能与**提前轮换**冲突；需要客户端团队参与排期。

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
