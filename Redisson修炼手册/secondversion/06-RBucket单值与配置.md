# 第五章（分篇一）：`RBucket`——单值、开关与 TTL

[← 第五章导览](05-分布式对象基础.md)｜[目录](README.md)

---

## 1. 项目背景

电商中台要做一个**全局功能开关**（例如「双十二皮肤是否开启」），要求多实例读到**同一份**状态，并能按活动**自动过期**；另有一批**小配置**（JSON 字符串即可），读多写少，希望比「整表扫 MySQL」快一个数量级。  
这类需求在 Redis 里天然对应 **String** 语义；在 Redisson 里，最贴近的抽象就是 **`RBucket<V>`**：**一个逻辑 key 对应一个任意类型的值**，并继承过期能力（`RExpirable`）。

---

## 2. 项目设计（大师 × 小白）

**小白**：开关我直接 `SET feature:skin ON` 不行吗？为啥还要 `RBucket`？  
**大师**：协议层当然可以。`RBucket` 的价值在于：**Codec 帮你把 Java 对象变成字节**、**异步/响应式同一套 API**、以及和 **`RedissonClient` 生命周期**绑在一起——你写的是 `getBucket("cfg:skin").set(dto, 1, TimeUnit.DAYS)`，而不是到处拼命令字符串。

**小白**：一个用户一个缓存呢？  
**大师**：单值仍可用 `RBucket`（例如 `user:profile:123`）；若字段多、要部分更新，再考虑 **`RMap`**（见下一篇）。别用一万个字段硬塞进一个巨大 DTO——**value 太大**时要想压缩与拆分（第四章 Codec、第五章导览中的 key 规范）。

---

## 3. 项目实战（主代码片段）

以下假定已持有 **`RedissonClient redisson`**（见第一章）。泛型 `V` 由 **Codec** 决定序列化格式（第四章）。

```java
import org.redisson.api.RBucket;
import org.redisson.api.RedissonClient;

import java.time.Duration;
import java.util.concurrent.TimeUnit;

// 功能开关：整包配置对象或简单 Boolean/String 均可
RBucket<String> skinFlag = redisson.getBucket("cfg:skin:double12");
skinFlag.set("ON", 10, TimeUnit.DAYS);
String mode = skinFlag.get();

// 带过期写入；若需「不存在才写入」语义，用 setIfAbsent 系列（以当前版本 JavaDoc 为准）
// FeatureConfig 为业务 POJO，须可被 Codec 正确序列化（见第四章）
RBucket<FeatureConfig> cfg = redisson.getBucket("cfg:checkout:rules");
cfg.set(new FeatureConfig(/* ... */), Duration.ofHours(1));

// 读完即删（一次性令牌、任务占位等场景）
// FeatureConfig once = cfg.getAndDelete();
```

**要点**：`RBucket` 单值上限以官方文档为准（接口注释中常见 **512MB** 量级约束）；生产上应主动控制 **单 key 体积**。跨多个 key 的「要么全成功」需 **Lua / 事务**（第十章），不是 `RBucket` 单独能完成的。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | API 直观；适合 **开关、小配置、单对象缓存**；天然支持 **TTL**；与 Redisson 线程模型、异步 API 一致。 |
| **缺点** | 大对象会放大 **网络与序列化成本**；无「字段级局部更新」（需整 value 替换或换 `RMap`）。 |
| **适用场景** | 功能开关、灰度标记、小 JSON 配置、会话外置的简单载荷（更复杂会话见 [第十二章·Spring Session](32-SpringSession.md)）。 |
| **注意事项** | **TTL 与缓存击穿/雪崩** 要在架构层设计（互斥重建、随机 TTL 等，与 [第十二章·Spring Cache](31-SpringCache.md) 联动）；**Codec 升级** 与第四章、[第十二章导览](29-Spring生态集成.md) 一致评审。 |
| **常见踩坑** | 把超大列表/报表塞进单个 `RBucket` → **慢查询与大 key**；忘记 **多实例一致语义** 仍受 Redis 拓扑影响；与 **Spring Data Redis** 共用 key 时无命名空间约定 → 互相覆盖。 |

---

## 本章实验室（约 25 分钟）

**环境**：单 Redis；可跑两个 `main` 或两次运行同一程序。

### 步骤

1. 使用 `getBucket("lab:rbucket:switch")`，`set("ON", 2, TimeUnit.MINUTES)`。  
2. `redis-cli` 找到对应 key 后执行 `TTL`，确认 **剩余秒数合理**。  
3. 第二个进程（或第二次启动）对 **同一逻辑名** `get()`，应与第一次一致。  
4. `setIfAbsent`（或当前版本等价 API）写入 `lab:rbucket:once`，连续调用两次，第二次应失败/为 false。

### 验证标准

- TTL 在 Redis 与业务预期 **同量级**；`setIfAbsent` 行为符合 **「仅首次成功」** 预期。

### 记录建议

- 截图：`TTL` 输出 + Java 侧 `get` 结果。

**上一篇**：—（从 [第五章导览](05-分布式对象基础.md) 进入）｜**下一篇**：[第五章（分篇二）RMap 与本地缓存](07-RMap与本地缓存.md)｜**下一章**：[第六章导览](10-分布式集合选型.md)
