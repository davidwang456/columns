# 第32章：Redis对象系统与内部编码源码剖析

## 1. 项目背景

业务平台上线后，运维同学发现一个奇怪现象：同样是十万个用户资料，有的环境占用几十 MB，有的环境占用上百 MB；同样是字符串，有的 `OBJECT ENCODING` 显示 `int`，有的显示 `embstr`，有的显示 `raw`。开发同学只看到外部类型 String、Hash、List、Set、ZSet，却不知道 Redis 内部还会根据数据大小、元素数量和取值范围选择不同编码。

这类问题在生产里很常见。购物车 Hash 字段少时很省内存，字段一多就发生编码转换；用户 ID 集合全是整数时用 `intset` 很紧凑，混入一个字符串后立刻变成哈希表；小字符串走 `embstr`，大字符串走 `raw`，内存分配和释放路径也不一样。如果只会写命令，不理解对象系统，容量规划就容易失真，排查大 key 时也只能猜。

本章进入高级篇源码观察的第一站：Redis 对象系统。我们会从 `redisObject` 看外部类型与内部编码的关系，再用命令批量写入不同大小的数据，观察 `OBJECT ENCODING`、`MEMORY USAGE` 的变化，最后关联源码文件解释为什么会转换。

本章不是要求背下每个结构体字段，而是建立一个工程判断：看到一个业务模型时，能预估它大概会落在哪种内部编码，什么时候会膨胀，应该用什么命令验证，如何在源码中找到证据。

## 2. 项目设计

小胖先问：“我明明只用了 String、Hash、Set，为什么大师老说什么 SDS、listpack、intset？这不是把简单问题复杂化吗？”

小白翻着监控说：“可内存问题就是从这里来的。外部类型一样，内部编码不同，内存占用和操作成本可能差很多。比如 Hash 少量字段是 listpack，大量字段会转成 hashtable。”

大师在白板写下 `type + encoding + ptr`：“Redis 对外提供的是命令和逻辑类型，对内保存的是对象。对象头里记录类型、编码、LRU 信息、引用计数和指针。指针指向真正的数据结构，比如 SDS、dict、quicklist、listpack、skiplist。你可以把外部类型理解成菜单名，把内部编码理解成厨房实际用的容器。”

技术映射：源码核心在 `src/server.h` 的 `redisObject`，对象创建和编码转换分布在 `object.c`、`t_string.c`、`t_hash.c`、`t_set.c`、`t_zset.c` 等文件中。

小胖追问：“那 String 为什么还分 `int`、`embstr`、`raw`？字符串不就是一串字吗？”

大师回答：“如果值是可以放进 long 的整数，Redis 可以用 `int` 编码，省掉额外 SDS。短字符串用 `embstr`，对象头和 SDS 连续分配，创建释放都快。长字符串用 `raw`，对象头和 SDS 分开，适合后续扩容修改。”

小白补充：“所以一个计数器 `SET pv 1` 和一个长 JSON `SET product:1 {...}`，虽然都是 String，内部成本完全不同。”

技术映射：字符串创建重点看 `createStringObject`、`createEmbeddedStringObject`、`tryObjectEncoding`，验证命令用 `OBJECT ENCODING key`。

小胖又问：“那 Hash、Set、ZSet 转换有什么规律？是不是 Redis 偷偷变来变去，业务会受影响？”

大师说：“转换对命令语义透明，但对内存和延迟不透明。Hash 小对象用 listpack，字段数量或字段长度超过阈值后转 hashtable；Set 全整数且元素少时用 intset，混入非整数或超过阈值转 hashtable；ZSet 小集合用 listpack，变大后用 skiplist 加 dict。业务结果不变，但转换瞬间可能带来一次额外成本，转换后内存也会上升。”

小白点头：“这解释了为什么导入数据时内存曲线不是线性增长，而是到某个点突然变陡。”

技术映射：编码转换条件与配置项有关，例如 `hash-max-listpack-entries`、`hash-max-listpack-value`、`set-max-intset-entries`、`zset-max-listpack-entries`。

小胖最后问：“源码这么多，怎么不迷路？”

大师回答：“按命令找入口。执行 `HSET` 就看 `t_hash.c`，执行 `SADD` 就看 `t_set.c`，对象通用逻辑看 `object.c`，底层容器再跳到 `sds.c`、`dict.c`、`listpack.c`、`quicklist.c`、`intset.c`、`zskiplist` 相关实现。每次只追一个命令、一种转换。”

## 3. 项目实战

### 3.1 源码文件与观察点

建议准备 Redis 8.6.x 源码目录，重点文件如下：

- `src/server.h`：观察 `redisObject` 字段。
- `src/object.c`：观察对象创建、引用计数、编码优化。
- `src/t_string.c`：观察 String 命令和字符串编码。
- `src/t_hash.c`：观察 Hash 的 listpack 与 hashtable 转换。
- `src/t_set.c`：观察 Set 的 intset 与 hashtable 转换。
- `src/t_zset.c`：观察 ZSet 的 listpack、dict、skiplist。
- `src/sds.c`、`src/dict.c`、`src/listpack.c`、`src/intset.c`：观察底层容器。

源码观察点：创建对象时如何设置 `type` 和 `encoding`；写入元素后是否触发转换；转换函数是否一次性搬迁所有元素；命令执行前后是否尝试优化对象编码。

### 3.2 环境准备

启动实验 Redis：

```bash
docker run --name redis-obj-lab -p 6379:6379 -d redis:8.6
redis-cli FLUSHALL
```

如果本地有源码编译环境，可以打开调试日志：

```bash
make BUILD_TLS=no
src/redis-server redis.conf --loglevel debug
```

没有编译环境也没关系，本章命令验证足以观察编码变化。

### 3.3 String 编码观察

执行：

```bash
redis-cli SET s:int 100
redis-cli SET s:short abc
redis-cli SET s:long "$(python -c 'print("x"*100)')"
redis-cli OBJECT ENCODING s:int
redis-cli OBJECT ENCODING s:short
redis-cli OBJECT ENCODING s:long
redis-cli MEMORY USAGE s:int
redis-cli MEMORY USAGE s:short
redis-cli MEMORY USAGE s:long
```

预期现象：整数值可能显示 `int`，短字符串显示 `embstr`，长字符串显示 `raw`。不同版本和配置细节可能略有差异，判断时以实际输出为准。

源码对应：在 `object.c` 中查找 `tryObjectEncoding`，关注它如何判断整数、字符串长度和编码类型。继续看 `createEmbeddedStringObject`，理解 `embstr` 为什么适合短字符串。

### 3.4 Hash 编码转换

执行：

```bash
redis-cli DEL h:user
redis-cli HSET h:user name xiaopang age 18 city hangzhou
redis-cli OBJECT ENCODING h:user
redis-cli MEMORY USAGE h:user
```

再批量写入字段：

```bash
for i in $(seq 1 600); do redis-cli HSET h:user f$i v$i > /dev/null; done
redis-cli HLEN h:user
redis-cli OBJECT ENCODING h:user
redis-cli MEMORY USAGE h:user
```

PowerShell 可用：

```powershell
1..600 | ForEach-Object { redis-cli HSET h:user "f$_" "v$_" | Out-Null }
```

预期现象：小 Hash 通常是 `listpack`，字段超过阈值后转为 `hashtable`。如果没有转换，检查 `CONFIG GET hash-max-listpack-*`，不同配置会影响阈值。

源码对应：在 `t_hash.c` 中查找 `hashTypeTryConversion`、`hashTypeConvert`、`hashTypeSet`。

### 3.5 Set 与 ZSet 编码观察

Set：

```bash
redis-cli DEL s:ids
redis-cli SADD s:ids 1 2 3 4
redis-cli OBJECT ENCODING s:ids
redis-cli SADD s:ids user-a
redis-cli OBJECT ENCODING s:ids
```

预期现象：纯整数小集合可能是 `intset`，加入非整数后转为 `hashtable`。

ZSet：

```bash
redis-cli DEL z:rank
redis-cli ZADD z:rank 1 u1 2 u2 3 u3
redis-cli OBJECT ENCODING z:rank
1..200 | ForEach-Object { redis-cli ZADD z:rank $_ "u$_" | Out-Null }
redis-cli OBJECT ENCODING z:rank
redis-cli MEMORY USAGE z:rank
```

预期现象：小 ZSet 可用 `listpack`，变大后转为 `skiplist`。源码看 `t_zset.c` 中的 `zsetConvert`、`zaddGenericCommand`。

### 3.6 批量报告脚本

可以用 Python 生成一份编码报告：

```python
import redis
r = redis.Redis(decode_responses=True)

for key in ["s:int", "s:short", "s:long", "h:user", "s:ids", "z:rank"]:
    enc = r.object("ENCODING", key)
    mem = r.memory_usage(key)
    print(f"{key}\tencoding={enc}\tmemory={mem}")
```

把输出贴进容量评审文档，标注“数据规模、编码、单 key 内存、转换阈值”，比单纯估算 value 字节数更可靠。

## 4. 项目总结

本章把 Redis 外部类型和内部编码连了起来。String、Hash、Set、ZSet 不是固定容器，而是会根据数据规模和内容选择更省内存或更适合查询的结构。

优点是 Redis 能在小对象场景下非常节省内存，常见命令语义保持稳定；缺点是编码转换对业务不可见，容易在导入、扩容、热点写入时造成内存突增或延迟抖动。

生产建议：容量评估不要只看 key 数量，要采样 `OBJECT ENCODING` 和 `MEMORY USAGE`；大批量导入前先用影子数据观察转换点；Hash、Set、ZSet 的阈值配置不要随意调大，节省内存和单次操作成本之间要平衡；排查大 key 时同时看元素数量、编码和业务访问模式。

思考题：一个用户画像应该用一个大 Hash，还是拆成多个小 Hash？当 Set 可能混入字符串 ID 时，容量评估为什么不能按 intset 估算？
