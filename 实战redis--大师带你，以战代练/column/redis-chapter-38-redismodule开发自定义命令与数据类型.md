# 第38章：Redis Module 开发：自定义命令与数据类型

## 1. 项目背景

业务团队已经熟练使用 String、Hash、ZSet、Stream 和 Cluster，但新的风控需求出现了：每个用户、设备、IP、商户都要做滑动计数和风险打分，规则每天变化，QPS 很高，要求延迟低于 2 毫秒。用 Lua 可以把多步操作变成原子动作，但脚本复杂后可维护性差；用外部服务可以表达复杂逻辑，却多了一次网络调用；把所有逻辑塞进应用层，又容易出现多语言重复实现。

Redis Module 提供了一条扩展路径：在 Redis 进程内注册自定义命令，必要时定义自己的数据类型、持久化方式和 AOF 重放逻辑。Redis Stack 里的 JSON、Query、TimeSeries、Bloom 等能力，本质上也展示了模块生态可以把 Redis 从缓存扩展成实时数据处理底座。

本章不追求做一个完整商业模块，而是用“风险计数器”做实战：开发一个简单模块，注册 `RISK.INCR` 和 `RISK.GET`，支持自定义数据类型、RDB 保存、AOF 重放和基础压测。读者要学会的不只是 API 调用，更是模块开发的边界：什么时候值得写模块，什么时候应该继续使用 Lua、Function 或外部服务。

## 2. 项目设计

小胖先说：“写模块是不是相当于给 Redis 装外挂？我想要一个 `RISK.INCR`，Redis 原来没有，那我自己塞一个进去。”

小白提醒：“外挂听起来危险。模块跑在 Redis 进程里，如果内存越界、阻塞线程、版本不兼容，会不会直接把 Redis 搞挂？”

大师点头：“这正是模块开发的第一原则：模块不是业务代码仓库，而是高频、通用、性能敏感、需要贴近数据结构的能力扩展。它能减少网络往返、提供新命令和新类型，但也把 C 语言内存安全、阻塞风险、版本兼容带进了 Redis。”

技术映射：模块入口通常是 `RedisModule_OnLoad`，内部调用 `RedisModule_Init` 初始化，再用 `RedisModule_CreateCommand` 注册命令。Redis 模块 API 的实现入口与封装在 `src/module.c`。

小胖问：“那自定义命令和自定义数据类型有什么区别？”

小白说：“如果只是 `RISK.INCR key field`，是不是用 Hash 加命令封装就够了？为什么还要类型？”

大师解释：“自定义命令可以操作现有 Redis 类型，比如内部打开 Hash、String、ZSet。自定义数据类型则是定义一种新的 value 表达，比如一个压缩计数器、布隆结构、时间序列块。它需要告诉 Redis 如何释放内存、如何写入 RDB、如何从 RDB 读回、如何生成 AOF 重放命令。”

技术映射：自定义类型通过 `RedisModule_CreateDataType` 创建，关键回调包括 `rdb_load`、`rdb_save`、`aof_rewrite`、`free`、`mem_usage`、`digest`。

小胖继续追问：“那我们这个风险计数器，应该怎么设计？”

小白说：“风控要关注用户维度、IP 维度和窗口时间。如果只是计数，Hash 足够；如果要高性能合并和持久化，也许自定义类型更清晰。”

大师给出取舍：“学习章我们用自定义类型实现最小计数结构：一个 key 对应一个对象，内部保存总计数和最近更新时间。生产上如果规则复杂，建议先用 Redis Function 或外部风控引擎验证，再把稳定且高频的部分沉淀为模块。”

技术映射：模块命令必须检查参数数量、key 打开模式、类型匹配、回复格式和错误路径，不能假设调用方永远正确。

## 3. 项目实战

### 3.1 环境准备

需要 Linux、WSL2 或容器环境，安装编译工具并准备 Redis 源码。示例目录：

```bash
git clone https://github.com/redis/redis.git
cd redis
make BUILD_WITH_MODULES=yes
```

新建 `risk_counter.c`，编译为动态库：

```bash
gcc -fPIC -shared -o risk_counter.so risk_counter.c -I/path/to/redis/src
redis-server --loadmodule ./risk_counter.so
```

Windows 用户建议在 WSL2 内完成模块编译和运行，避免动态库格式和编译环境差异干扰学习。

### 3.2 注册模块与命令

最小入口如下：

```c
#include "redismodule.h"

int RiskIncrCommand(RedisModuleCtx *ctx, RedisModuleString **argv, int argc);
int RiskGetCommand(RedisModuleCtx *ctx, RedisModuleString **argv, int argc);

int RedisModule_OnLoad(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    if (RedisModule_Init(ctx, "riskcounter", 1, REDISMODULE_APIVER_1) == REDISMODULE_ERR)
        return REDISMODULE_ERR;
    if (RedisModule_CreateCommand(ctx, "risk.incr", RiskIncrCommand, "write", 1, 1, 1) == REDISMODULE_ERR)
        return REDISMODULE_ERR;
    if (RedisModule_CreateCommand(ctx, "risk.get", RiskGetCommand, "readonly", 1, 1, 1) == REDISMODULE_ERR)
        return REDISMODULE_ERR;
    return REDISMODULE_OK;
}
```

验证：

```bash
redis-cli module list
redis-cli risk.incr risk:user:1001 3
redis-cli risk.get risk:user:1001
```

如果返回 unknown command，优先检查模块是否加载成功；如果 Redis 启动失败，查看 server 日志中模块初始化错误。

### 3.3 定义数据结构与类型

定义一个简单结构：

```c
typedef struct RiskCounter {
    long long total;
    long long updated_at;
} RiskCounter;
```

模块里创建数据类型：

```c
static RedisModuleType *RiskType;

RedisModuleTypeMethods tm = {
    .version = REDISMODULE_TYPE_METHOD_VERSION,
    .rdb_load = RiskRdbLoad,
    .rdb_save = RiskRdbSave,
    .aof_rewrite = RiskAofRewrite,
    .free = RiskFree
};

RiskType = RedisModule_CreateDataType(ctx, "riskcnt01", 0, &tm);
```

`RISK.INCR key delta` 的处理逻辑：
1. 校验参数数量和 delta 是否为整数。
2. 以写模式打开 key。
3. 如果 key 不存在，分配 `RiskCounter` 并绑定到 key。
4. 如果 key 存在，检查类型必须是 `RiskType`。
5. 更新 total 和 updated_at。
6. 调用 `RedisModule_ReplicateVerbatim` 让复制和 AOF 记录原命令。
7. 返回新 total。

`RISK.GET key` 则只读打开 key，返回 total 和 updated_at。错误回复应使用明确文本，例如 `ERR wrong type for risk counter`，方便客户端和测试断言。

### 3.4 持久化、AOF 与验证

RDB 保存时写入两个整数：

```c
void RiskRdbSave(RedisModuleIO *io, void *value) {
    RiskCounter *rc = value;
    RedisModule_SaveSigned(io, rc->total);
    RedisModule_SaveSigned(io, rc->updated_at);
}
```

RDB 加载时按相同顺序读回。AOF rewrite 可以生成：

```c
RedisModule_EmitAOF(aof, "RISK.INCR", "sl", key, rc->total);
```

验证步骤：

```bash
redis-cli risk.incr risk:user:1001 5
redis-cli save
redis-cli shutdown
redis-server --loadmodule ./risk_counter.so
redis-cli risk.get risk:user:1001
```

再做基础压测：

```bash
redis-benchmark -n 100000 -c 50 risk.incr risk:user:1001 1
```

源码观察点：
- `src/module.c`：模块加载、命令注册、类型注册、API 暴露。
- `src/server.c`：命令查找和执行仍走统一命令表。
- `src/rdb.c`、`src/aof.c`：模块类型如何接入持久化流程。

常见坑：模块命令不要执行长时间阻塞操作；不要在 Redis 主线程里访问慢外部服务；内存分配与释放必须成对；自定义类型名长度有限且版本号要谨慎；线上升级模块前必须验证旧 RDB 能否加载。

## 4. 项目总结

Redis Module 适合把稳定、高频、性能敏感的通用能力下沉到 Redis 内部。它可以注册命令、定义类型、接入 RDB/AOF、利用 Redis 内存和事件模型，但也要求开发者承担更高的工程责任。

优点：
- 减少网络往返，把高频逻辑贴近数据执行。
- 可以定义新类型，突破核心数据结构限制。
- 能复用 Redis 的复制、持久化和命令协议。
- 适合沉淀平台级能力，如 JSON、Query、TimeSeries、Bloom。

缺点：
- C 语言开发和调试成本高。
- 模块崩溃可能影响 Redis 进程。
- 版本兼容、数据格式升级和部署回滚复杂。
- 不适合承载频繁变化的业务规则。

适用场景是高性能计数、概率结构、时间序列、搜索索引、向量检索和企业内部通用扩展。不适用场景是临时业务逻辑、频繁变更规则、需要大量外部 IO 的流程。思考题：同一个限流需求，何时用 Lua，何时用 Redis Function，何时才值得写 Module？自定义类型升级时，如何保证旧 RDB 和 AOF 可恢复？
