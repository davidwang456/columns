# 第十三章（分篇六）：Tomcat Session——`RedissonSessionManager`

[← 第十三章导览](33-框架矩阵速览.md)｜[目录](README.md)

---

## 1. 项目背景

传统 **WAR** 部署在 **Apache Tomcat** 集群，需要 **非粘性** 负载均衡：用户每次请求可能落到不同实例，**Session 必须外置到 Redis**。Redisson 提供 **`RedissonSessionManager`**（及 **JNDI 共享实例** 等进阶用法），支持 Tomcat **7.x–11.x** 对应 **`redisson-tomcat-7` … `redisson-tomcat-11`**。

---

## 2. 项目设计（大师 × 小白）

**小白**：Session 放 Redis，Tomcat 内存里还有吗？  
**大师**：看 **`readMode`**：`MEMORY` **本地+Redis**，`REDIS` **主要在 Redis**（见官方说明）——**延迟与一致性**不同。

**小白**：我多个环境共用一个 Redis？  
**大师**：用 **`keyPrefix`** 把 **测试与生产** 隔开——否则 **Session 串台** 是 P0 事故。

---

## 3. 项目实战（主代码片段）

**步骤概要**（详见 [web-session-management.md](../web-session-management.md)）：

1. 将 **`redisson-all-*.jar`** 与对应 **`redisson-tomcat-{7|8|9|10|11}-*.jar`** 放入 **`TOMCAT_BASE/lib`**。  
2. 在 **`context.xml` 或 `server.xml`** 中配置 **Manager**：

```xml
<Manager className="org.redisson.tomcat.RedissonSessionManager"
         configPath="${catalina.base}/redisson.yaml"
         readMode="REDIS"
         updateMode="DEFAULT"
         broadcastSessionEvents="false"
         keyPrefix="prod:tomcat1:"/>
```

**`updateMode`**：`DEFAULT` 与 **`AFTER_REQUEST`** 行为与 **`readMode`** 组合有关；**`broadcastSessionUpdates`** 在 `MEMORY` 模式下影响多实例同步。

**共享 Redisson 实例**：多 Context 可用 **`JndiRedissonFactory` + `JndiRedissonSessionManager`**（见官方 **Shared Redisson instance** 小节）。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **经典 Tomcat 集群** 最直出的 Session 外置方案；支持 **非粘性** 路由；与 Redisson **统一配置 YAML**。 |
| **缺点** | **运维向**：jar 版本与 Tomcat 大版本 **强绑定**；配置在 **XML**，易与应用代码 **脱节**。 |
| **适用场景** | 未上 Spring Session 的老系统、纯 Servlet 栈、Tomcat 水平扩展。 |
| **注意事项** | 升级 **Tomcat 大版本** 时同步换 **`redisson-tomcat-*`**；关注 **序列化安全** 与 **Session 固定攻击** 防护。 |
| **常见踩坑** | **`keyPrefix` 为空** 导致多环境互踩；**readMode/updateMode** 组合理解错误导致 **属性不刷新**；**redisson.yaml** 路径在容器内 **不存在**。 |

---

## 本章实验室（约 45～60 分钟）

**环境**：两台 Tomcat 或 **一实例两端口** + 负载均衡（可选）；`context.xml` / `server.xml` 按 [web-session-management.md](../web-session-management.md) 配置 **`RedissonSessionManager`**；Redis 测试实例。

### 步骤

1. 部署 **同一应用 war** 到两节点，`keyPrefix` 设 **`lab-tomcat-dev:`**。  
2. 浏览器登录，**轮换请求** 到两台（无粘性），确认 **Session 不丢**。  
3. `redis-cli` 查看 session key，核对 **前缀、TTL**。  
4. 故意 **空 `keyPrefix`** 与 **正确前缀** 各跑一次（不同 Redis DB 或不同环境），说明 **互踩风险**。

### 验证标准

- 实验 2：**无粘性下** 至少 **4 次** 请求均保持登录态（或等价会话属性）。  
- 有一份 **readMode/updateMode** 当前组合的行为说明（对照文档）。

### 记录建议

- 升级 **Tomcat 小版本** 时的 **redisson-tomcat jar** 检查清单。

**上一篇**：[第十三章（分篇五）MyBatis](38-MyBatis.md)｜**下一章**：[第十四章 可观测与上线清单](40-可观测与上线清单.md)
