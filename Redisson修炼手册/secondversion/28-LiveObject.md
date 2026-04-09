# 第十一章（分篇四）：Live Object——`RLiveObjectService` 与 `@REntity`

[← 第十一章导览](24-分布式服务.md)｜[目录](README.md)

---

## 1. 项目背景

希望多个 JVM **像共享堆一样** 读写同一业务对象：改一个字段，其它实例 **几乎立刻** 能在 getter 上看到新值，又 **不想** 手写 `RMap` 的每个字段 key。  
Redisson **Live Object（RLO）** 通过 **`@REntity` + `@RId`** 等注解，把对象字段映射到 Redis **Hash**（运行时代理），由 **`RLiveObjectService`** 完成 `persist` / `attach` / `merge` / `get`。

---

## 2. 项目设计（大师 × 小白）

**小白**：这不就是 ORM 吗？  
**大师**：味道像，但 **存储是 Redis、一致性边界是单线程命令原子**，不是 JPA 那套。调试时你要面对 **代理、引用、索引**——**团队规范** 跟不上就别上。

**小白**：我用 Lombok `@Data` 偷懒？  
**大师**：官方明确：**getter/setter/构造器不能靠 Lombok 生成**；业务方法里也要 **走 getter** 而不是直接捅字段，否则代理拦不住。

---

## 3. 项目实战（主代码片段）

```java
import org.redisson.api.RLiveObjectService;
import org.redisson.api.RedissonClient;
import org.redisson.api.annotation.REntity;
import org.redisson.api.annotation.RId;

@REntity
public class SkuCounter {

    @RId
    private String skuId;

    private long soldCount;

    public SkuCounter() {
    }

    public SkuCounter(String skuId) {
        this.skuId = skuId;
    }

    public String getSkuId() {
        return skuId;
    }

    public void setSkuId(String skuId) {
        this.skuId = skuId;
    }

    public long getSoldCount() {
        return soldCount;
    }

    public void setSoldCount(long soldCount) {
        this.soldCount = soldCount;
    }
}

RedissonClient redisson = /* ... */;
RLiveObjectService live = redisson.getLiveObjectService();

SkuCounter row = new SkuCounter("SKU-1001");
row = live.persist(row);
row.setSoldCount(row.getSoldCount() + 1);

SkuCounter loaded = live.get(SkuCounter.class, "SKU-1001");
```

**检索**：带 **`@RIndex`** 的字段可用 **`Conditions`** 查询（见 [services.md Live Object](../data-and-services/services.md#live-object-service)）；开源版搜索性能限制见官方 **PRO** 说明。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **字段级远程共享**；少写胶水 `RMap`；引用关系可建模为 **对象图**。 |
| **缺点** | **学习曲线陡**；调试难；**Lombok 不友好**；团队对 **Codec、索引、级联** 要统一认知。 |
| **适用场景** | 强 Redis 共栈、对象模型清晰、愿意维护 **RLO 规范** 的团队。 |
| **注意事项** | `transient` 字段 **不入库**；集合类型会映射为 **Redisson 集合**（见官方对照表）；延迟敏感应用可 **预注册** 类型。 |
| **常见踩坑** | 直接访问 **字段** 绕过代理；把 RLO 当 **普通 POJO** 随便传 JSON；**搜索索引** 与 **notify-keyspace-events** 配置遗漏。 |

---

## 本章实验室（约 45～60 分钟）

**环境**：单 Redis；**禁止 Lombok** 于 `@REntity` 类；两进程或两线程均可。

### 步骤

1. `persist` 新建 `SkuCounter("LAB-1")`，`setSoldCount(0)`。  
2. 进程 A：`get(SkuCounter.class, "LAB-1")`，`setSoldCount(10)`（**必须经 setter**）。  
3. 进程 B：再次 `get`，读取 `getSoldCount()`，验证 **为 10**。  
4. **故意**在业务代码里 **直接改字段** `row.soldCount = 99`（若编译允许）或错误用法，对照文档说明 **为何错误**；正确方式 **仅经 getter/setter**。  
5. `redis-cli`：`HGETALL` 对应 Hash key（注意 Redisson **前缀**），字段与 Java 侧一致。

### 验证标准

- 实验 2～3：**跨视图** 数值一致。  
- 能口述：**RLO 与 JPA 在一致性边界上的差异**（至少 1 点）。

### 记录建议

- 团队规范草案：**RLO 类 Code Review 检查项**（3 条以内）。

**上一篇**：[第十一章（分篇三）RScheduledExecutorService](27-RScheduledExecutorService.md)｜**下一章**：[第十二章导览](29-Spring生态集成.md)
