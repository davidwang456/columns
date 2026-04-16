# 第 7 章：密码编码：PasswordEncoder 与迁移策略

> 本章对齐 [docs/template.md](../template.md)，建议字数 3000–5000。

---

## 1 项目背景（约 500 字）

### 业务场景

遗留系统密码使用 **MD5/SHA1** 存储，安全团队要求 **升级到 BCrypt/Argon2**。业务要求：**老用户下次登录时无感迁移**，新用户直接用新算法。

### 痛点放大

若全局重置密码，客服成本爆炸；若双写密码，**数据一致性**难保证。需要 **`PasswordEncoder` 体系** + **DelegatingPasswordEncoder** 按前缀路由到不同算法。

### 流程图

```mermaid
flowchart LR
  Input[明文密码]
  Enc[PasswordEncoder]
  Store[存储哈希]
  Input --> Enc --> Store
```

源码锚点：`crypto/` 模块。

---

## 2 项目设计：剧本式交锋对话（约 1200 字）

**小胖**

「密码为啥不加密传输就够？还要哈希？」

**小白**

「HTTPS 与哈希各解决什么问题？」

**大师**

「**传输层** 靠 TLS；**存储层** 必须 **单向哈希 + 盐**，防 DB 泄露后批量撞库。」

**技术映射**：TLS → 传输；`PasswordEncoder` → 存储。

**小胖**

「`{bcrypt}` 花括号啥意思？」

**大师**

「**`DelegatingPasswordEncoder`** 根据前缀选 **BCrypt、PBKDF2、Argon2** 等实现；迁移期可并存多种前缀。」

**技术映射**：`{id}` 前缀 → 多算法路由。

**小白**

「验证老 MD5 时如何校验？」

**大师**

「自定义 **legacy encoder** 或 **MigrationEncoder**：验证成功后用新算法 **re-encode 写回**。」

**技术映射**：读时迁移 → 登录成功后 `upgradeEncoding`。

---

## 3 项目实战（约 1500–2000 字）

### 步骤 1：默认 BCrypt

```java
@Bean
PasswordEncoder encoder() {
  return new BCryptPasswordEncoder();
}
```

### 步骤 2：Delegating 多算法

```java
Map<String, PasswordEncoder> encoders = new HashMap<>();
encoders.put("bcrypt", new BCryptPasswordEncoder());
encoders.put("noop", NoOpPasswordEncoder.getInstance()); // 仅演示禁止生产
return new DelegatingPasswordEncoder("bcrypt", encoders);
```

### 步骤 3：登录后升级（伪代码）

```java
if (encoder.upgradeEncoding(encodedPassword)) {
  user.setPassword(encoder.encode(rawPassword));
}
```

### 测试

单元测试覆盖 **旧哈希可验证**、**新密码写入新格式**。

### 可能遇到的坑

| 坑 | 处理 |
|----|------|
| 明文密码打日志 | 日志脱敏 |
| 强度参数过低 | 调整 BCrypt strength / 换 Argon2 |

---

## 4 项目总结（约 500–800 字）

### 优点与缺点

| 维度 | Delegating + 迁移 | 一刀切重置 |
|------|-------------------|------------|
| 用户体验 | 好 | 差 |
| 实现复杂度 | 高 | 低 |

### 适用场景

- 存量用户多、需平滑升级。

### 不适用场景

- 泄露已证实密码明文，应强制重置。

### 思考题

1. Argon2 与 BCrypt 选型差异？（内存硬、侧信道）
2. 为何不应使用 `NoOpPasswordEncoder` 生产？

### 推广计划提示

- **安全**：渗透测试重点测「登录接口限流、账户锁定」。
- **运维**：密钥轮换与 `PasswordEncoder` 无关，但 **JWT 密钥** 另见第 21 章。

---

*本章完。*
