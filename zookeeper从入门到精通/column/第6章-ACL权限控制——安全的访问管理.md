# 第6章：ACL 权限控制——安全的访问管理

## 1. 项目背景

### 业务场景

你的团队在 ZooKeeper 上搭建了配置中心和服务注册中心，多个团队的多个服务都在用。这时问题来了：

- **部门 A** 的支付服务配置（数据库密码、支付密钥）存储在 `/config/payment/` 下
- **部门 B** 的用户服务配置存储在 `/config/user/` 下
- 两个部门不应该看到对方的配置信息

更关键的是，某个实习生不小心执行了 `deleteall /config`——整个配置中心的数据被删光了。没有权限控制，任何能连上 ZooKeeper 的人都可以做任何操作。

### 痛点放大

没有 ACL 的生产环境 ZooKeeper 集群，会面临以下风险：

- **数据泄露**：数据库密码、API Key 等敏感信息暴露给所有能连接 ZooKeeper 的人
- **数据篡改**：恶意或误操作修改关键配置，导致服务异常
- **数据删除**：误删关键节点，影响业务正常运行
- **不可审计**：无法追踪谁在什么时间做了什么操作

ZooKeeper 的 ACL（Access Control List）机制正是解决这些问题而设计的。

---

## 2. 项目设计

### 剧本式交锋对话

**场景**：运维群里炸锅了——有人删了生产环境 ZooKeeper 的 `/config/payment` 节点，支付服务全部报错。

**小胖**：谁干的？！赶紧看看日志。不对，ZooKeeper 没有操作日志……

**大师**：这就是为什么我一直在说要在 ZooKeeper 上配 ACL。ZooKeeper 的 ACL 模型是 `scheme:id:permissions` 三元组。

**小白**：scheme、id、permissions 分别是什么意思？

**大师**：

- **scheme**：认证方式，比如 world（所有人）、digest（用户名密码）、ip（IP 白名单）、auth（当前认证用户）、x509（TLS 证书）
- **id**：认证标识，比如用户名（digest 的 id）、IP 地址（ip 的 id）
- **permissions**：权限，5 种权限位的组合：
  - `CREATE`（c）：创建子节点
  - `READ`（r）：读取节点数据和子节点列表
  - `WRITE`（w）：修改节点数据
  - `DELETE`（d）：删除子节点
  - `ADMIN`（a）：设置 ACL

> **技术映射**：ACL = 门禁卡，scheme = 刷卡/指纹/人脸三种识别方式，id = 你的卡号/指纹 ID，permissions = 能进哪些门

**小胖**：那具体怎么配置？比如我现在想：支付服务能读写 `/config/payment`，其他服务只能读。

**大师**：用 digest 认证方式，为每个服务创建一个账号：

```bash
# 添加认证用户
addauth digest payment-svc:pass123

# 创建节点并设置 ACL
create /config/payment "payment-config"
setAcl /config/payment digest:payment-svc:crwda,world:anyone:r

# 解释：
# digest:payment-svc:crwda → 支付服务有 CREATE、READ、WRITE、DELETE、ADMIN 权限
# world:anyone:r → 其他所有人只有 READ 权限

# 验证：未认证的客户端只能读
get /config/payment        # 成功
set /config/payment "new"  # 失败！Authentication is not valid

# 验证：已认证的客户端能写
addauth digest payment-svc:pass123
set /config/payment "new"  # 成功
```

**小白**：那子节点自动继承父节点的 ACL 吗？

**大师**：这是一个非常重要的坑——**ZooKeeper 的 ACL 不会自动继承**。每个 ZNode 都有自己的 ACL，创建时默认使用父节点的 ACL，但一旦创建成功，修改父节点 ACL 不会影响子节点。所以要为每个子节点单独设置 ACL。

**小胖**：那如果我忘了密码怎么办？是不是永远都改不了 ACL 了？

**大师**：ZooKeeper 有一个"后门"——`superDigest`。在 `zoo.cfg` 中配置超级管理员：

```properties
# 生成超级密码的哈希（使用 ZooKeeper 自带的 DigestAuthenticationProvider）
# 在命令行执行：
# java -cp zookeeper-3.9.2.jar:lib/* org.apache.zookeeper.server.auth.DigestAuthenticationProvider super:superpass
# 输出类似：super:D/b3pGqSdNkYF3zOeQO9RkYnHx4=

# 然后将这个哈希配入 zoo.cfg
superDigest=super:D/b3pGqSdNkYF3zOeQO9RkYnHx4=
```

重启后用 `addauth digest super:superpass` 认证，就成为超级管理员，可以操作任何节点。

> **技术映射**：superDigest = 超级管理员万能钥匙，忘了密码找管理员开锁

---

## 3. 项目实战

### 环境准备

- ZooKeeper 3.9.x 集群（单机模式即可）
- zkCli.sh 命令行

### 分步实现

#### 步骤 1：体验五种权限位

```bash
# 连接 ZooKeeper
./bin/zkCli.sh -server 127.0.0.1:2181

# 用 world 默认 ACL 创建节点（所有人都可以读写）
create /acl-demo "demo"

# 查看默认 ACL
getAcl /acl-demo
# 输出：'world,'anyone: cdrwa
# 所有人都有所有权限（默认配置）
```

#### 步骤 2：digest 认证实战

```bash
# 创建节点（只能由 admins 组管理员操作）
create /secure-data "sensitive"

# 设置 ACL：只有 admin 用户能完整操作，其他人只读
setAcl /secure-data digest:admin:crwda,digest:viewer:r
# 注意：digest 密码需要先用 addauth 添加用户，再设置 ACL

# 或者先添加认证，再设置（更常见的做法）
addauth digest admin:admin123
addauth digest viewer:viewer123
create /secure-data2 "sensitive"
setAcl /secure-data2 auth:admin:crwda,auth:viewer:r
```

#### 步骤 3：多租户 ACL 方案

创建一个多部门隔离的 ZooKeeper 结构：

```bash
# 1. 为部门 A 和 B 创建用户
addauth digest dept-a:pass-a
addauth digest dept-b:pass-b

# 2. 创建根路径
create /departments "multiple-departments"
create /departments/dept-a "dept-a-data"
create /departments/dept-b "dept-b-data"

# 3. 设置 ACL（部门 A 只能操作自己的路径）
setAcl /departments/dept-a auth:dept-a:cdrwa,world:anyone:

# 4. 验证隔离
# 重新连接（模拟部门 B 的客户端）
./bin/zkCli.sh -server 127.0.0.1:2181
addauth digest dept-b:pass-b

# 可以读取自己的路径
ls /departments/dept-b   # 成功

# 无法操作部门 A 的路径
set /departments/dept-a "hacked"  # 失败！Authentication is not valid
```

#### 步骤 4：IP 白名单 ACL

```bash
# 创建只能由特定 IP 修改的节点
# 假设内部 IP 段 10.0.0.0/8
create /internal-only "internal"
setAcl /internal-only ip:127.0.0.1:crwda,ip:10.0.0.0/8:cdrwa
# 127.0.0.1 和 10.x.x.x 网段的客户端可以操作
# 其他 IP 的客户端没有权限
```

#### 步骤 5：编写 Java ACL 程序

创建 `AclDemo.java`：

```java
package com.zkdemo;

import org.apache.zookeeper.*;
import org.apache.zookeeper.data.ACL;
import org.apache.zookeeper.data.Id;
import org.apache.zookeeper.data.Stat;

import java.util.ArrayList;
import java.util.List;

public class AclDemo {
    private static final String ZK_URL = "127.0.0.1:2181";

    public static void main(String[] args) throws Exception {
        // 创建客户端并鉴权
        ZooKeeper zk = new ZooKeeper(ZK_URL, 5000, event -> {});
        Thread.sleep(1000);

        // 添加认证信息
        zk.addAuthInfo("digest", "admin:admin123".getBytes());

        // 1. 创建节点并设置 ACL
        List<ACL> aclList = new ArrayList<>();
        // admin 用户拥有所有权限
        aclList.add(new ACL(ZooDefs.Perms.ALL, new Id("digest", "admin:admin123")));
        // viewer 用户只有读权限
        aclList.add(new ACL(ZooDefs.Perms.READ, new Id("digest", "viewer:viewer123")));

        String path = zk.create("/secure-app", "secure-data".getBytes(), aclList, CreateMode.PERSISTENT);
        System.out.println("创建节点: " + path);

        // 2. 验证 ACL：已认证用户可以写
        Stat stat = new Stat();
        byte[] data = zk.getData("/secure-app", false, stat);
        System.out.println("读取数据: " + new String(data));

        zk.setData("/secure-app", "updated-by-admin".getBytes(), stat.getVersion());
        System.out.println("更新成功（有 WRITE 权限）");

        // 3. 创建一个没有写权限的客户端
        ZooKeeper viewerZk = new ZooKeeper(ZK_URL, 5000, event -> {});
        Thread.sleep(1000);
        viewerZk.addAuthInfo("digest", "viewer:viewer123".getBytes());

        try {
            viewerZk.setData("/secure-app", "unauthorized-update".getBytes(), -1);
        } catch (KeeperException.NoAuthException e) {
            System.out.println("viewer 用户写失败（没有 WRITE 权限）: " + e.getMessage());
        }

        // viewer 用户可以读
        byte[] viewerData = viewerZk.getData("/secure-app", false, null);
        System.out.println("viewer 用户读取成功: " + new String(viewerData));

        // 清理
        zk.delete("/secure-app", -1);
        zk.close();
        viewerZk.close();
    }
}
```

### 运行结果

```
创建节点: /secure-app
读取数据: secure-data
更新成功（有 WRITE 权限）
viewer 用户写失败（没有 WRITE 权限）: KeeperErrorCode = NoAuth for /secure-app
viewer 用户读取成功: updated-by-admin
```

### 可能遇到的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| `NoAuth` 错误 | 客户端没有相应权限 | 检查 ACL 配置和认证信息 |
| `InvalidACL` 错误 | ACL 格式错误或 scheme 不支持 | 确认 scheme 名称正确 |
| ACL 设置后无法访问 | 忘记添加 world:anyone:r | 至少给一种 identity 分配权限 |
| 子节点无法操作 | ACL 不继承 | 手动设置每个子节点的 ACL |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 安全性 | 细粒度 5 种权限控制 | ACL 不继承，管理复杂 |
| 灵活性 | 4 种 scheme 适配不同场景 | digest 密码以明文传输 |
| 性能 | 权限校验快，计算开销小 | 每个操作都校验，有微小开销 |

### 适用场景

- **多租户集群**：不同部门/项目共享同一个 ZooKeeper 集群
- **生产配置中心**：敏感配置（密码、密钥）严格控制写入权限
- **服务注册发现**：防止未授权的服务注册或恶意注销
- **共享集群**：多个团队共用，需要隔离数据和操作

**不适用场景**：
- 单一团队的小规模集群（ACL 管理成本可能大于收益）
- 需要动态权限变更的高频场景（ACL 修改后不波及子节点）

### 注意事项

- ACL 基于路径，不由父节点继承
- digest 密码在网络上明文传输，生产环境建议配合 TLS 使用
- 使用 `addauth` 添加的认证信息只对当前会话有效
- 设置 ACL 前必须先 `addauth`

### 常见踩坑经验

**故障 1：清理节点时遇到 NoAuth**

现象：有 CRWDA 权限的账号试图删除子节点时报 `NoAuth`。

根因：DELETE 权限是对子节点的控制——要删除 `/config/db` 这个子节点，必须有 `/config/db` 的 DELETE 权限。而不是父节点 `/config` 的 DELETE 权限。

**故障 2：ACL 设置后所有人都无法操作**

现象：设置 ACL 后，即使管理员也无法读取节点。

根因：只给某个 digest 用户设置了权限，但没有给 world 任何权限，结果自己也忘了 `addauth`。正确的做法是先 `addauth` 再 `setAcl`。

### 思考题

1. 如果一个节点有 `ACL(world:anyone:r)`，这意味着所有人都能读。如果这时候你用 `setAcl` 改为 `ACL(digest:user1:crwda)`，此时"所有人"还能读吗？为什么？
2. 如何设计一个 ACL 策略，使得 `/services/order-service` 下的节点只能被 order-service 实例写，但所有服务都能读？写出具体步骤。

### 推广计划提示

- **开发**：代码中操作 ZooKeeper 时，记得初始化时 `addAuthInfo`，否则遇到 ACL 保护的节点会报错
- **运维**：生产环境务必配置 ACL，至少给所有节点设置 `world:anyone:r`；关键配置节点需要单独的 digest 认证
- **测试**：测试 ACL 相关功能时，需要覆盖认证成功、认证失败、未认证三种场景
