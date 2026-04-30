# 第30章：安全加固——TLS 加密与 Kerberos 认证

## 1. 项目背景

### 业务场景

ZooKeeper 集群中存储了敏感数据——数据库密码、API Key、服务注册信息。默认情况下：

- **客户端与 ZooKeeper 通信**：明文传输，任何能抓包的人都能看到数据内容
- **ZooKeeper 节点间通信**：明文传输，集群内部的 ZooKeeper 通信也在公共网络上
- **身份认证**：任何知道 ZooKeeper 地址的人都可以连上来操作（如果没配 ACL）

在金融、医疗等合规严苛的行业，这些安全隐患是不可接受的。

### 痛点放大

未加密的 ZooKeeper 集群面临：

- **数据泄露**：在中间网络抓包就能获取 ZooKeeper 中的敏感配置
- **中间人攻击**：攻击者可以伪造 ZooKeeper 节点，劫持客户端数据
- **未授权访问**：任何人都能连接 ZooKeeper 并读取/写入数据

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：安全审计报告指出 ZooKeeper 集群存在安全风险。

**小白**：安全团队说我们的 ZooKeeper 用明文传输，需要加密。怎么搞？

**大师**：ZooKeeper 的安全体系分两层：

**传输加密（TLS）**：

```
客户端 ←→ ZooKeeper：TLS 加密传输
ZooKeeper ←→ ZooKeeper：TLS 加密传输
```

**身份认证（SASL/Kerberos）**：

```
客户端 ←→ ZooKeeper：Kerberos 认证
ZooKeeper ←→ ZooKeeper：Kerberos 认证
```

**小胖**：需要两套都上吗？还是只上 TLS 就够了？

**大师**：推荐**TLS + ACL**组合应对大部分场景。Kerberos 配置复杂，只在高度合规场景需要。

**TLS 配置流程：**

```
1. 为每个节点生成证书（KeyStore）
2. 生成信任证书列表（TrustStore）
3. 配置 ZooKeeper 启用 TLS
4. 配置客户端启用 TLS
5. 验证加密通信
```

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x
- JDK 11+（含 keytool）
- OpenSSL（可选，用于生成证书）

### 分步实现

#### 步骤 1：生成 TLS 证书

```bash
#!/bin/bash
# 生成 ZooKeeper TLS 证书

# 1. 创建目录
mkdir -p certs && cd certs

# 2. 生成 CA 证书（自签名 CA）
keytool -genkeypair -alias ca -keyalg RSA -keysize 2048 \
  -keystore ca.jks -storepass changeit \
  -dname "CN=ZooKeeper CA, OU=DevOps, O=Company, L=Beijing, C=CN" \
  -ext bc:c \
  -validity 3650

# 3. 导出 CA 证书
keytool -exportcert -alias ca -keystore ca.jks \
  -rfc -file ca.crt -storepass changeit

# 4. 生成服务端证书（每个节点一个）
for node in zk1 zk2 zk3; do
  # 生成密钥对
  keytool -genkeypair -alias $node -keyalg RSA -keysize 2048 \
    -keystore ${node}.jks -storepass changeit \
    -dname "CN=${node}.example.com, OU=DevOps, O=Company, L=Beijing, C=CN" \
    -validity 3650

  # 生成证书签名请求
  keytool -certreq -alias $node -keystore ${node}.jks \
    -file ${node}.csr -storepass changeit

  # CA 签署证书
  keytool -gencert -alias ca -keystore ca.jks -storepass changeit \
    -infile ${node}.csr -outfile ${node}.crt -rfc \
    -ext "SAN=DNS:${node}.example.com,IP:127.0.0.1"

  # 将 CA 证书和服务端证书导入服务端 KeyStore
  keytool -importcert -alias ca -keystore ${node}.jks \
    -file ca.crt -storepass changeit -noprompt
  keytool -importcert -alias $node -keystore ${node}.jks \
    -file ${node}.crt -storepass changeit

  echo "证书生成完成: ${node}.jks"
done

# 5. 生成 TrustStore（客户端使用）
keytool -importcert -alias ca -keystore truststore.jks \
  -file ca.crt -storepass changeit -noprompt

echo ""
echo "所有证书已生成！"
ls -la *.jks *.crt
```

#### 步骤 2：配置 ZooKeeper 启用 TLS

创建 `zoo-tls.cfg`：

```properties
# ZooKeeper TLS 配置（在 zoo.cfg 中添加）

# 客户端 TLS（客户端到 ZooKeeper）
secureClientPort=2281

# 服务端 KeyStore
ssl.keyStore.location=/path/to/certs/zk1.jks
ssl.keyStore.password=changeit

# 服务端 TrustStore
ssl.trustStore.location=/path/to/certs/truststore.jks
ssl.trustStore.password=changeit

# 是否需要客户端证书（双向认证）
ssl.clientAuth=none     # 可选：none / want / need

# 集群内部 TLS（ZooKeeper 节点之间）
serverCnxnFactory=org.apache.zookeeper.server.NettyServerCnxnFactory
ssl.quorum.keyStore.location=/path/to/certs/zk1.jks
ssl.quorum.keyStore.password=changeit
ssl.quorum.trustStore.location=/path/to/certs/truststore.jks
ssl.quorum.trustStore.password=changeit
ssl.quorum.clientAuth=need
```

#### 步骤 3：启动 TLS 配置的 ZooKeeper

```bash
# 设置 JVM 参数（启用 TLS 需要额外配置）
export SERVER_JVMFLAGS="-Dzookeeper.serverCnxnFactory=org.apache.zookeeper.server.NettyServerCnxnFactory -Dzookeeper.ssl.keyStore.location=/path/to/certs/zk1.jks -Dzookeeper.ssl.keyStore.password=changeit -Dzookeeper.ssl.trustStore.location=/path/to/certs/truststore.jks -Dzookeeper.ssl.trustStore.password=changeit"

# 启动
./bin/zkServer.sh start zoo-tls.cfg
```

#### 步骤 4：TLS 客户端连接

创建 `TlsClientDemo.java`：

```java
package com.zkdemo.security;

import org.apache.zookeeper.Watcher;
import org.apache.zookeeper.ZooKeeper;
import org.apache.zookeeper.admin.ZooKeeperAdmin;

import javax.net.ssl.*;
import java.io.FileInputStream;
import java.security.KeyStore;

public class TlsClientDemo {
    public static void main(String[] args) throws Exception {
        System.out.println("=== TLS 加密 ZooKeeper 客户端 ===\n");

        // 1. 配置 SSL Context
        String trustStorePath = "/path/to/certs/truststore.jks";

        System.setProperty("zookeeper.clientCnxnSocket",
                "org.apache.zookeeper.ClientCnxnSocketNetty");
        System.setProperty("zookeeper.ssl.trustStore.location", trustStorePath);
        System.setProperty("zookeeper.ssl.trustStore.password", "changeit");

        // 如果需要客户端证书认证（双向认证）
        // System.setProperty("zookeeper.ssl.keyStore.location", "/path/to/certs/client.jks");
        // System.setProperty("zookeeper.ssl.keyStore.password", "changeit");

        // 2. 连接 TLS 端口（2281）
        ZooKeeper zk = new ZooKeeper("127.0.0.1:2281", 5000, event -> {
            if (event.getState() == Watcher.Event.KeeperState.SyncConnected) {
                System.out.println("TLS 连接成功!");
            }
        });

        Thread.sleep(2000);

        // 3. 验证可以正常操作
        System.out.println("Session ID: " + Long.toHexString(zk.getSessionId()));
        System.out.println("连接状态: " + zk.getState());

        zk.close();
    }
}
```

#### 步骤 5：Kerberos 认证配置

创建 `zk-jaas.conf`（JAAS 配置文件）：

```properties
# ZooKeeper 服务端 JAAS 配置
Server {
    com.sun.security.auth.module.Krb5LoginModule required
    useKeyTab=true
    keyTab="/etc/security/keytabs/zk.service.keytab"
    storeKey=true
    useTicketCache=false
    principal="zookeeper/zk1.example.com@EXAMPLE.COM";
};

# ZooKeeper 客户端 JAAS 配置
Client {
    com.sun.security.auth.module.Krb5LoginModule required
    useKeyTab=true
    keyTab="/etc/security/keytabs/zk-client.keytab"
    storeKey=true
    useTicketCache=false
    principal="zk-client@EXAMPLE.COM";
};
```

在 `zoo.cfg` 中添加 Kerberos 配置：

```properties
# Kerberos 认证
authProvider.1=org.apache.zookeeper.server.auth.SASLAuthenticationProvider
requireClientAuthScheme=sasl
jaasLoginRenew=3600000

# 服务端 Principal
kerberos.removeHostFromPrincipal=false
kerberos.removeRealmFromPrincipal=false
```

客户端添加认证信息：

```java
// Java 客户端 Kerberos 认证
System.setProperty("java.security.auth.login.config", "/path/to/zk-jaas.conf");
System.setProperty("javax.security.auth.useSubjectCredsOnly", "false");

ZooKeeper zk = new ZooKeeper("127.0.0.1:2181", 5000, event -> {
    if (event.getState() == Watcher.Event.KeeperState.SyncConnected) {
        System.out.println("Kerberos 认证连接成功!");
    }
});

// 或者使用 addAuthInfo
zk.addAuthInfo("sasl", "zk-client".getBytes());
```

#### 步骤 6：安全 ACL 与认证结合

```java
package com.zkdemo.security;

import org.apache.zookeeper.*;
import org.apache.zookeeper.data.ACL;
import org.apache.zookeeper.data.Id;

import java.util.ArrayList;
import java.util.List;

public class SecureAclDemo {
    public static void main(String[] args) throws Exception {
        // 启用 SASL 认证
        System.setProperty("java.security.auth.login.config",
                "/path/to/zk-jaas.conf");

        ZooKeeper zk = new ZooKeeper("127.0.0.1:2181", 5000, event -> {
            if (event.getState() == Watcher.Event.KeeperState.SyncConnected) {
                System.out.println("已连接到 ZooKeeper");
            }
        });
        Thread.sleep(1000);

        // 创建带 ACL 的节点
        List<ACL> aclList = new ArrayList<>();

        // 方式 1: 只允许 SASL 认证用户
        aclList.add(new ACL(ZooDefs.Perms.ALL,
                new Id("sasl", "zk-client@EXAMPLE.COM")));

        zk.create("/secure-data", "sensitive".getBytes(),
                aclList, CreateMode.PERSISTENT);
        System.out.println("创建安全节点: /secure-data");

        // 方式 2: IP + SASL 组合
        List<ACL> ipAcl = new ArrayList<>();
        ipAcl.add(new ACL(ZooDefs.Perms.READ,
                new Id("ip", "10.0.0.0/8")));
        ipAcl.add(new ACL(ZooDefs.Perms.ALL,
                new Id("sasl", "admin@EXAMPLE.COM")));

        zk.create("/ip-restricted", "restricted".getBytes(),
                ipAcl, CreateMode.PERSISTENT);
        System.out.println("创建 IP 限制节点: /ip-restricted");

        // 方式 3: 创建后修改 ACL
        List<ACL> newAcl = ZooDefs.Ids.CREATOR_ALL_ACL;
        zk.setACL("/secure-data", newAcl, -1);
        System.out.println("修改 ACL 完成");

        zk.close();
    }
}
```

#### 步骤 7：审计日志配置

```bash
# ZooKeeper 审计日志（3.6+）
# 在 zoo.cfg 中添加：
audit.enable=true

# 查看审计日志（默认在 dataDir/logs/ 下）
tail -f /var/log/zookeeper/zookeeper_audit.log

# 审计日志格式：
# 2025-03-14 10:00:00,123 [session:0x100000001] - CREATE /secure-data - OK
# 2025-03-14 10:00:05,456 [session:0x100000001] - SETDATA /secure-data - OK
# 2025-03-14 10:00:10,789 [session:0x100000002] - DELETE /secure-data - DENIED
```

### 测试验证

```bash
# 验证 TLS 连接
./bin/zkCli.sh -server 127.0.0.1:2281
# 如果 TLS 配置正确，应该能成功连接

# 验证明文端口已被 TLS 端口取代
./bin/zkCli.sh -server 127.0.0.1:2181
# 如果只配置了 TLS，2181 端口不再接受明文连接

# 验证 Kerberos 认证
curl http://zookeeper:2181/commands/auth

# 验证审计日志
tail -f /var/log/zookeeper/zookeeper_audit.log
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| SSLHandshakeException | 证书 CN 不匹配 | 使用 SAN（Subject Alternative Name） |
| Kerberos 时间戳无效 | 客户端和服务端时钟不同步 | 配置 NTP 同步 |
| ConnectionLoss（TLS 模式） | TLS 端口未开放（配置了 `secureClientPort` 但防火墙没放行） | 检查防火墙 |

---

## 4. 项目总结

### 安全配置速查表

| 安全措施 | 配置参数 | 复杂度 | 性能影响 |
|---------|---------|--------|---------|
| TLS 客户端加密 | `secureClientPort`, SSL Store | 中 | 10-20% CPU 开销 |
| TLS 集群内部加密 | `ssl.quorum.*` | 中 | 15-30% CPU 开销 |
| Kerberos 认证 | `jaasLoginRenew`, SASL | 高 | 5-10% 开销 |
| 审计日志 | `audit.enable=true` | 低 | 2-5% 写性能下降 |
| ACL | `setAcl` | 低 | 可忽略 |

### 适用场景

- **TLS 加密**：所有生产环境的 ZooKeeper 集群（安全基线）
- **Kerberos**：金融、政府等高合规要求的环境
- **审计日志**：需要操作审计和合规的场景

### 注意事项

- TLS 和 Kerberos 可以同时使用（这是最高安全配置）
- `secureClientPort` 配置后，旧端口（2181）默认仍然可用，需要额外限制
- 自签名证书在 Java 中可能需要导入到 JDK 的 cacerts 中

### 常见踩坑经验

**故障 1：TLS 端口配置后原端口仍然可用**

现象：配置了 `secureClientPort=2281` 后，2181 端口仍然接受明文连接。

根因：`secureClientPort` 是额外开启 TLS 端口，不会自动关闭明文端口。

解决方案：在防火墙层面限制明文端口的访问来源，或配置 `clientPort` 为 127.0.0.1 只允许本地连接。

**故障 2：Java 客户端证书验证失败**

现象：Java 客户端连接 TLS ZooKeeper 时报 `PKIX path building failed`。

根因：客户端没有导入 CA 证书到 TrustStore。ZooKeeper 使用了自签名 CA，客户端 Java 运行时不信任该 CA。

解决方案：将自签名 CA 证书导入客户端的 TrustStore：
```bash
keytool -importcert -alias zk-ca -keystore $JAVA_HOME/lib/security/cacerts \
  -file ca.crt -storepass changeit
```

### 思考题

1. 双向 TLS（mTLS）中，客户端需要提供证书证明身份，服务端也需要验证客户端证书。这种方案和 Kerberos 认证比起来，在运维复杂度上有什么差异？
2. 审计日志记录了每个操作的 sessionId、操作类型、路径和结果。如果审计日志一天产生了 10GB 数据，如何设计审计日志的存储和分析方案？

### 推广计划提示

- **开发**：开发环境可以不用 TLS（增加调试复杂度），但生产环境必须配置 TLS 加密
- **运维**：证书有有效期，需要建立证书续期机制（建议使用 ACME 自动续期）
- **安全**：TLS + ACL 是最小安全配置，Kerberos + 审计日志是合规配置
