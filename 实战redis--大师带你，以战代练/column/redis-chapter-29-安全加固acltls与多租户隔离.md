# 第29章：安全加固acltls与多租户隔离

## 1. 项目背景

一家内部平台最初把 Redis 部署在“可信内网”，所有业务共用一个密码。随着服务增多，问题逐渐暴露：开发环境脚本误执行 `FLUSHALL` 清空测试缓存，某个业务用 `KEYS *` 扫描导致其他业务抖动，审计时也无法说明是谁访问了哪些 key。更危险的是，Redis 明文传输，一旦跨机房或跨云访问，密码和数据都有泄露风险。

Redis 安全加固不是把 `requirepass` 配上就结束。生产环境需要最小权限、命令治理、网络隔离、TLS 加密、审计日志和多租户边界。ACL 可以按用户授予命令和 key 前缀权限，TLS 保护传输链路，命令重命名或禁用降低误操作风险，多实例或逻辑前缀隔离减少业务互相影响。

本章以“多业务共享 Redis 平台”为场景，为开发、测试、运维配置不同 ACL 用户，启用 TLS，并梳理危险命令治理和多租户隔离策略。重点是可落地配置和验收命令。

## 2. 项目设计

小胖先说：“内网服务都自己人，用一个密码省事。每个业务一个账号，岂不是麻烦？”

小白反问：“如果有人误删了 `order:*`，你怎么知道是谁？如果推荐服务只能读 `rec:*`，为什么要给它 `FLUSHDB` 权限？”

大师说：“安全的第一原则是最小权限。Redis 6 以后有 ACL 用户体系，可以限制命令类别、具体命令和 key 前缀。技术映射：用账号表达身份，用权限表达边界，用审计表达责任。”

小胖又问：“那只配 ACL 就安全了吗？密码在网络里传来传去，会不会被抓包？”

小白补充：“跨云、跨机房、容器网络里都可能有旁路风险。TLS 能保护链路，但证书管理和客户端兼容也要考虑。”

大师回答：“对。TLS 解决传输加密和服务端身份校验，ACL 解决登录后能做什么，网络策略解决谁能连上。三者要叠加，不要互相替代。技术映射：认证、授权、加密、网络隔离分别对应不同风险。”

小胖看着多租户方案问：“所有业务共用一个 Redis，只要 key 加前缀不就行？”

大师提醒：“前缀是弱隔离，适合低风险共享。强隔离要拆实例、拆集群、拆网络和资源配额。否则一个租户的大 key、慢命令或内存暴涨会影响其他租户。”

## 3. 项目实战

### 3.1 ACL 最小配置

启动实验实例：

```bash
docker run --name redis-lab-29 -p 6379:6379 -d redis:8.6 \
  redis-server --requirepass rootpass
```

管理员登录：

```bash
redis-cli -a rootpass
ACL LIST
```

创建三个用户。注意 ACL 密码参数里的 `>` 在 Shell 中要加引号，避免被当成重定向：

```bash
redis-cli -a rootpass ACL SETUSER app_product on ">prodpass" "~product:*" +@read +@write -@dangerous
redis-cli -a rootpass ACL SETUSER app_report on ">reportpass" "~report:*" +@read -@write -@dangerous
redis-cli -a rootpass ACL SETUSER ops_readonly on ">opspass" "~*" +@read +info "+client|getname" -@write -@dangerous
redis-cli -a rootpass ACL SAVE
```

验证权限：

```bash
redis-cli --user app_product -a prodpass SET product:1 "phone"
redis-cli --user app_product -a prodpass GET product:1
redis-cli --user app_product -a prodpass SET order:1 "bad"
redis-cli --user app_report -a reportpass SET report:1 "bad"
```

预期：`product:*` 可读写，越权 key 或写命令会返回权限错误。测试团队要把越权访问作为安全回归用例。

### 3.2 危险命令治理

常见危险命令包括 `FLUSHALL`、`FLUSHDB`、`CONFIG`、`KEYS`、`MONITOR`、`SHUTDOWN`、`SAVE`。Redis 7 以后可以用 ACL 直接禁用类别或具体命令：

```bash
redis-cli -a rootpass ACL SETUSER app_product -flushall -flushdb -config -keys -monitor -shutdown
```

生产配置还应关闭默认用户或设置强密码：

```conf
user default off
aclfile /usr/local/etc/redis/users.acl
protected-mode yes
bind 0.0.0.0
```

注意：如果关闭 default 用户，一定要先确认客户端都已迁移到具名用户，否则会造成大面积认证失败。

### 3.3 TLS 配置

生成测试证书可以用 OpenSSL：

```bash
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -out ca.crt -subj "/CN=redis-ca"
openssl genrsa -out redis.key 2048
openssl req -new -key redis.key -out redis.csr -subj "/CN=redis"
openssl x509 -req -in redis.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out redis.crt -days 365 -sha256
```

Redis 配置片段：

```conf
port 0
tls-port 6379
tls-cert-file /tls/redis.crt
tls-key-file /tls/redis.key
tls-ca-cert-file /tls/ca.crt
tls-auth-clients no
aclfile /usr/local/etc/redis/users.acl
```

客户端验证：

```bash
redis-cli --tls --cacert ca.crt -h 127.0.0.1 -p 6379 \
  --user app_product -a prodpass PING
```

生产环境建议启用客户端证书双向认证，至少保证服务端证书校验开启，避免客户端忽略证书错误。

### 3.4 多租户隔离策略

弱隔离：同一实例，不同 ACL 用户和 key 前缀，例如 `product:*`、`order:*`、`report:*`。适合低风险、低流量、同团队场景。

中隔离：同一集群不同实例组或不同数据库编号不作为强隔离手段，推荐按业务拆 Redis 实例，独立 `maxmemory`、慢日志、连接数和告警。

强隔离：独立集群、独立网络策略、独立证书和账号，适合支付、风控、会员权益等高风险系统。

运维流程：

1. 新业务申请 Redis 时填写数据类型、访问命令、key 前缀、容量和安全等级。
2. 平台创建专属 ACL 用户，默认禁用危险命令。
3. 上线前执行越权访问、TLS 连接和命令白名单测试。
4. 定期执行 `ACL LIST`、`ACL WHOAMI`、`CLIENT LIST` 和日志审计。

常见坑：第一，把 `requirepass` 当成完整安全方案。第二，在代码里明文写密码并提交仓库。第三，TLS 只在服务端开启，客户端却跳过证书验证。第四，多租户只靠前缀隔离，却没有容量和慢命令边界。

## 4. 项目总结

Redis 安全加固要分层完成：网络上限制访问来源，连接上启用认证和 TLS，权限上使用 ACL 最小授权，运维上治理危险命令和审计行为，多租户上按风险选择隔离强度。

优点：ACL 能精确控制命令和 key 范围，TLS 能保护传输，租户拆分能降低互相影响。缺点：权限配置需要维护，TLS 增加证书管理成本，多实例隔离会增加资源和运维复杂度。

适用场景包括多业务共享 Redis、跨网络访问、合规审计、生产账号治理和高危命令管控。不适合为了省事让所有业务继续共用 root 密码。

思考题：
1. 为什么 key 前缀隔离不能替代实例级资源隔离？
2. 开启 TLS 后，客户端连接池和健康检查需要同步调整哪些参数？

推广建议：开发团队按最小命令集申请权限，测试团队负责越权和证书用例，运维团队维护 ACL、证书和审计，架构团队制定多租户分级标准。
