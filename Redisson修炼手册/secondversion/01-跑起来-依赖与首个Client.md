# 第一章：十分钟跑起来——依赖与第一个 `RedissonClient`

[← 目录](README.md)

---

## 江湖兵器谱（趣味）

小白背着空剑鞘冲进山门：「师父，我按文档抄了 `Redisson.create`，控制台一片红！」  
大师头也没抬：「剑客出门三件事：**剑穗（坐标）、剑鞘（URL 方案）、收剑式（shutdown）**。你少哪样？」  
小白嘀咕：「我们脚手架里 Spring Boot 都有了，还要手写 `main`？」大师：「**手写一次**，你才懂 Starter 替你藏了啥。」

---

## 需求落地（由浅入深）

1. **浅**：迭代第一天就要「连上测试 Redis、写进读出 hello」——卡住的多半是 **依赖版本**、**地址写错**、或 **TLS 与密码** 与运维交付不一致。  
2. **中**：决定 **YAML 外置配置** 还是代码内嵌；多环境（dev/stage/prod）是否 **同一套模板**；K8s 里 Service 名变更时 **是否重建 Bean**。  
3. **深**：`Redisson.create(config)` 得到**进程级单例**；`shutdown` 与 Spring 生命周期绑定；同一 JVM 里 **禁止每请求 new 客户端**——否则连接与线程池把机器吃光。

---

## 对话钩子

**小白**：连不上 / 版本不对 / 不知道用 `redis://` 还是 `rediss://`……  
**大师**：先定三件事：**Maven 坐标**、**连接 scheme**、**应用退出时是否 `shutdown`**。

**小白**：Starter 配好了，本地能连，上容器就超时？  
**大师**：查 **DNS 解析到的 IP**、**网络策略**、以及 **Redis 是否只监听内网**——客户端再聪明，也飞不过防火墙。

---

## 依赖（Community）

```xml
<dependency>
   <groupId>org.redisson</groupId>
   <artifactId>redisson</artifactId>
   <version><!-- 与项目 BOM 对齐 --></version>
</dependency>
```

**Gradle**：`implementation 'org.redisson:redisson:x.y.z'`

**PRO**：坐标 `pro.redisson:redisson`，License 见 [getting-started.md](../getting-started.md)、[configuration.md](../configuration.md)。

**JDK**：README 声明 **JDK 8+**；Android 场景需自行对照发行说明。

---

## 纯 Java：最小可运行

```java
Config config = new Config();
config.useSingleServer().setAddress("redis://127.0.0.1:6379");

RedissonClient redisson = Redisson.create(config);
try {
    RBucket<String> bucket = redisson.getBucket("demo");
    bucket.set("hello");
} finally {
    redisson.shutdown();
}
```

### 从 YAML 加载（实战推荐）

```java
Config config = Config.fromYAML(new File("config/redisson.yaml"));
RedissonClient redisson = Redisson.create(config);
```

`redisson.yaml` 骨架（单机示例，字段以官方为准）：

```yaml
singleServerConfig:
  address: "redis://127.0.0.1:6379"
  connectionMinimumIdleSize: 8
  connectionPoolSize: 32
```

---

## Spring Boot：思路对齐

引入 **redisson-spring-boot-starter**（版本与 Spring Boot 对齐，见 [integration-with-spring.md](../integration-with-spring.md)），在 `application.yml` 写 `spring.redis.redisson.file` 或内联 `config`，由容器注入 `RedissonClient`。

**要点**：Bean 销毁阶段调用 `shutdown`，与 Spring 生命周期绑定——**不要**每个请求 `create` 一次客户端。

---

## 三种 API 入口（先混脸熟）

```java
RedissonClient redisson = Redisson.create(config);
RedissonReactiveClient reactive = redisson.reactive();
RedissonRxClient rx = redisson.rxJava();
```

详见 [api-models.md](../api-models.md)。

---

## 生产注意（清单）

- [ ] **单例 `RedissonClient`**，全应用复用。  
- [ ] **shutdown**：Servlet 容器销毁、`@PreDestroy`、或 Spring `DisposableBean`。  
- [ ] **升级前**：在预发验证 **Codec / key** 兼容性。  
- [ ] **密码与 TLS**：`rediss://`、`valkeys://`，证书与主机名校验别偷懒。

---

## 本章实验室（约 30～45 分钟）

**环境**：Redis 正常端口 `6379`；另准备一个 **错误端口**（如 `6380` 无服务）用于对照。

### 步骤

1. **异常分类**  
   - 将 `Config` 中地址改为 `redis://127.0.0.1:6380`（无监听），运行 `Redisson.create` 后执行一次 `getBucket("x").get()`。  
   - 将地址改回正确端口但 **密码故意写错**（若 Redis 启用了 `requirepass`）。  
   - 分别保存异常栈 **根因一行**（`Connection refused` / `Timeout` / `WRONGPASS` 等），整理成表：**现象 → 可能原因 → 第一步排查**。

2. **shutdown 对比**  
   - 程序 A：`create` 后 `shutdown()`，进程退出前用 `jcmd <pid> Thread.print` 或 VisualVM 看线程。  
   - 程序 B：相同代码但 **注释掉 `shutdown`**，退出前再次 dump 线程。  
   - 对比是否仍有 **netty / redisson 相关非守护线程**（以实际栈为准）。

3. **（可选）Spring Boot**  
   - 在 `@PostConstruct` 或 `ApplicationRunner` 中打印：`redisson.getConfig().toYAML()` 或文档推荐的 **脱敏摘要**（勿打明文密码）。  
   - 验证：启动日志中能确认 **单机/集群** 等模式关键词。

### 验证标准

- 至少能区分 **「连不上」** 与 **「认证失败」** 两类错误。  
- 能说明：**为何生产必须绑定 shutdown 与 Spring 生命周期**。

### 记录建议

- 团队排障手册增加一节：**Redisson 连接失败速查表**（3 行以内）。

---

## 大师私房话

「能连上」≈ 刚拿到驾照。**断线谁重连、命令失败谁重试、线程池会不会被打满**——才是上路后的生死题。见 [第二章](02-配置-拓扑与调参.md)、[第三章](03-线程模型与三种API.md)。

**上一章**：[第零章](00-序章-为什么选Redisson.md)｜**下一章**：[第二章](02-配置-拓扑与调参.md)
