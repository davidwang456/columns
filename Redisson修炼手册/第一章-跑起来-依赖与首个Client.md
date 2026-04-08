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

## 本章实验室

1. 故意写错端口，把异常栈分类：**连接拒绝 / 超时 / 认证失败**。  
2. 写一个小 `main`：**忘记 `shutdown`**，用 JVM 参数或 profiler 看线程是否残留。  
3. Spring 项目：打印 `RedissonClient` 的 **config 摘要**（脱敏后）进启动日志，便于排障。

---

## 大师私房话

「能连上」≈ 刚拿到驾照。**断线谁重连、命令失败谁重试、线程池会不会被打满**——才是上路后的生死题。见 [第二章](第二章-配置-拓扑与调参.md)、[第三章](第三章-线程模型与三种API.md)。

**上一章**：[第零章](第零章-序章-为什么选Redisson.md)｜**下一章**：[第二章](第二章-配置-拓扑与调参.md)
