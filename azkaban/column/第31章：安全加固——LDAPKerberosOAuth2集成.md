# 第31章：安全加固——LDAP/Kerberos/OAuth2集成

## 1. 项目背景

### 业务场景

某金融公司的安全审计团队对数据平台进行了一次全面安全审查，发现Azkaban存在三个严重问题：

1. **弱认证**：用户密码通过XML文件明文存储（`azkaban-users.xml`），且默认admin账号从未修改过密码
2. **票据缺失**：Spark/Hive任务在Azkaban中运行时，没有Kerberos认证——这意味着它们用的是Azkaban运行账号的权限，而不是提交者的身份
3. **审计黑洞**：谁在什么时候提交了什么Job、查看了哪些数据——这些操作全部无法追溯

安全团队给出了最后通牒：30天内完成安全加固，否则系统必须下线。

### 痛点放大

安全不达标时：

1. **合规风险**：金融/医疗等行业要求严格的权限控制和审计，不达标无法通过监管检查
2. **越权操作**：任何人都可以修改、删除其他人的Flow
3. **数据泄露**：在Job日志中可能暴露数据库密码等敏感信息
4. **无审计追踪**：发生安全事件后无法追溯到操作人

## 2. 项目设计——剧本式交锋对话

**小胖**（拿着安全审计报告发愁）：大师，安全部给了最后通牒——30天内必须搞定三件事：LDAP认证、Kerberos票据、审计日志。Azkaban能做到吗？

**大师**：一项一项来。先说最简单的——LDAP认证。

**小白**：LDAP怎么集成到Azkaban？

**大师**：Azkaban支持通过LDAP验证用户身份。配置方式是在`azkaban.properties`中指定`user.manager.class=azkaban.user.LdapUserManager`，然后配置LDAP服务器地址和搜索规则。

```properties
# LDAP认证配置
user.manager.class=azkaban.user.LdapUserManager
user.manager.ldap.host=ldap.company.com
user.manager.ldap.port=389
user.manager.ldap.user.base.dn=ou=people,dc=company,dc=com
user.manager.ldap.user.id.property=sAMAccountName
user.manager.ldap.group.base.dn=ou=groups,dc=company,dc=com
user.manager.ldap.embedded=false
```

**小胖**：那Kerberos呢？我们的Hadoop集群要求Kerberos认证。

**大师**：Kerberos集成最关键的是"票据委托"——用户登录Azkaban后，Azkaban需要为用户生成一个Kerberos票据（TGT），然后当Azkaban替用户提交Hadoop/Spark任务时，使用该票据代表用户身份。

流程如下：

```
1. 用户登录Azkaban → LDAP认证通过
2. 用户提供Kerberos密码或keytab → 生成TGT
3. 用户提交Flow → Azkaban用用户的TGT代理提交Hadoop任务
4. Job在Yarn上执行 → 使用用户的Kerberos身份访问HDFS/Hive
```

**小白**：审计日志呢？怎么知道谁在什么时候做了什么？

**大师**：Azkaban的审计能力比较基础。你需要自己构建三层审计：

1. **Azkaban系统日志**：配置JSON格式输出，记录所有API调用
2. **操作审计表**：在MySQL中建一张`audit_log`表，记录关键操作
3. **日志收集**：将Azkaban日志接入ELK/Splunk

### 技术映射总结

- **LDAP** = 统一门禁卡（公司所有人用同一张卡出入）
- **Kerberos** = 身份护照（证明"我是谁"，票据有有效期，过期要续签）
- **OAuth2/SSO** = 统一登录入口（登录一次，所有系统都能用）
- **审计日志** = 监控摄像头（事后追查"谁在什么时间做了什么"）

## 3. 项目实战

### 3.1 环境准备

| 组件 | 用途 |
|------|------|
| LDAP/AD Server | 统一用户认证 |
| Kerberos KDC | 票据授权 |
| Keytab文件 | 服务认证凭证 |

### 3.2 分步实现

#### 步骤1：LDAP认证集成

**目标**：用户通过公司LDAP/AD登录Azkaban。

```properties
# conf/azkaban.properties —— LDAP配置

# 使用LDAP用户管理器
user.manager.class=azkaban.user.LdapUserManager

# LDAP服务器连接
user.manager.ldap.host=ldap.company.com
user.manager.ldap.port=389
user.manager.ldap.use.ssl=false

# 用户搜索规则
user.manager.ldap.user.base.dn=ou=people,dc=company,dc=com
user.manager.ldap.user.id.property=sAMAccountName

# 查找用户的filter（%s会被替换为用户名）
user.manager.ldap.user.search.filter=(&(objectClass=person)(sAMAccountName=%s))

# 组到角色的映射
user.manager.ldap.group.base.dn=ou=groups,dc=company,dc=com
user.manager.ldap.group.mapping=data_admin=admin,data_dev=writer,data_viewer=reader
user.manager.ldap.group.search.filter=(member=%s)

# 绑定用户（用于搜索目录）
user.manager.ldap.bind.account=cn=azkaban-svc,ou=service,dc=company,dc=com
user.manager.ldap.bind.password=service_password

# 其他
user.manager.ldap.embedded=false
user.manager.ldap.cache.enabled=true
user.manager.ldap.cache.size=1000
user.manager.ldap.cache.ttl.minutes=30
```

**验证LDAP连接**：

```bash
# 使用ldapsearch验证连接性
ldapsearch -H ldap://ldap.company.com:389 \
  -D "cn=azkaban-svc,ou=service,dc=company,dc=com" \
  -w "service_password" \
  -b "ou=people,dc=company,dc=com" \
  "(sAMAccountName=zhangsan)"
```

#### 步骤2：OAuth2/SSO集成

**目标**：对接公司统一SSO（如Keycloak、CAS）。

```properties
# azkaban.properties —— OAuth2配置（需自定义实现）
user.manager.class=com.company.azkaban.OAuth2UserManager

# OAuth2服务端配置
oauth2.auth.url=https://sso.company.com/auth
oauth2.token.url=https://sso.company.com/token
oauth2.client.id=azkaban-web
oauth2.client.secret=azkaban_client_secret
oauth2.redirect.uri=https://azkaban.company.com/oauth2/callback
oauth2.scope=openid,profile,email

# 用户属性映射
oauth2.user.id.attribute=sub
oauth2.user.name.attribute=preferred_username
oauth2.user.email.attribute=email
oauth2.user.group.attribute=groups
```

**自定义OAuth2 UserManager**（Java实现骨架）：

```java
public class OAuth2UserManager implements UserManager {
    
    @Override
    public User getUser(String username, String password) throws UserManagerException {
        // OAuth2模式不需要密码，从token中提取用户信息
        throw new UserManagerException("Use OAuth2 flow instead of password auth");
    }
    
    public User getUserFromToken(String accessToken) throws UserManagerException {
        // 1. 调用OAuth2 userinfo端点验证token
        String userInfo = httpClient.get(
            "https://sso.company.com/userinfo",
            "Authorization: Bearer " + accessToken
        );
        
        // 2. 解析返回的用户信息
        JsonObject json = JsonParser.parseString(userInfo).getAsJsonObject();
        String userId = json.get("sub").getAsString();
        String userName = json.get("preferred_username").getAsString();
        
        // 3. 映射组到角色
        Set<String> roles = new HashSet<>();
        for (String group : json.get("groups").getAsJsonArray()) {
            if (group.equals("data_admin")) roles.add("admin");
            else if (group.equals("data_dev")) roles.add("writer");
            else roles.add("reader");
        }
        
        return new User(userId, userName, roles);
    }
}
```

#### 步骤3：Kerberos票据集成

**目标**：Azkaban Job使用用户的Kerberos身份执行Hadoop操作。

**AzKaban侧配置**：

```properties
# azkaban.properties —— Kerberos配置

# 启用Kerberos代理
azkaban.should.proxy.user=true
azkaban.proxy.user=azkaban

# Keytab路径（Azkaban服务自身的票据）
azkaban.keytab.path=/etc/security/keytabs/azkaban.keytab
azkaban.kerberos.principal=azkaban/host.company.com@COMPANY.COM

# Hadoop认证
azkaban.hadoop.kerberos.enabled=true
hadoop.security.authentication=kerberos
```

**Job中启用Kerberos**：

```bash
# kerberos_aware.job —— 带Kerberos认证的Job
type=command
command=bash -c '
echo "=== Kerberos认证 Job ==="

# 1. 获取Kerberos票据
if [ -n "${azkaban.kerberos.principal}" ]; then
    echo "Using Azkaban proxy user: ${azkaban.proxy.user}"
    
    # 为当前用户获取票据（从keytab）
    kinit -kt /etc/security/keytabs/azkaban.keytab \
      "${azkaban.kerberos.principal}"
    
    echo "Kerberos ticket acquired"
    klist
fi

# 2. 以代理用户身份操作HDFS
export HADOOP_USER_NAME="${submit.user}"
echo "Acting as user: ${HADOOP_USER_NAME}"

# 3. 执行需要Kerberos认证的操作
hdfs dfs -ls /user/${submit.user}/data/

# 4. 清理票据
kdestroy
echo "Kerberos ticket destroyed"
'
```

#### 步骤4：审计日志系统

**目标**：记录所有关键操作的审计日志。

```java
// AuditLogger.java —— 审计日志拦截器（Java实现）

import org.apache.log4j.Logger;
import javax.servlet.*;
import javax.servlet.http.*;
import java.io.IOException;
import java.time.Instant;

public class AuditFilter implements Filter {
    
    private static final Logger auditLog = Logger.getLogger("AUDIT");
    
    @Override
    public void doFilter(ServletRequest request, ServletResponse response, 
                        FilterChain chain) throws IOException, ServletException {
        
        HttpServletRequest httpReq = (HttpServletRequest) request;
        long startTime = System.currentTimeMillis();
        
        // 记录请求信息
        String user = httpReq.getRemoteUser() != null ? 
                      httpReq.getRemoteUser() : "anonymous";
        String action = httpReq.getParameter("ajax") != null ? 
                        httpReq.getParameter("ajax") : httpReq.getPathInfo();
        
        auditLog.info(String.format(
            "{\"timestamp\":\"%s\",\"user\":\"%s\",\"action\":\"%s\"," +
            "\"ip\":\"%s\",\"method\":\"%s\",\"params\":\"%s\"}",
            Instant.now(), user, action,
            httpReq.getRemoteAddr(), httpReq.getMethod(),
            sanitize(httpReq.getQueryString())
        ));
        
        try {
            chain.doFilter(request, response);
            
            long duration = System.currentTimeMillis() - startTime;
            HttpServletResponse httpResp = (HttpServletResponse) response;
            
            // 记录响应
            auditLog.info(String.format(
                "{\"timestamp\":\"%s\",\"user\":\"%s\",\"action\":\"%s\"," +
                "\"status\":%d,\"duration_ms\":%d}",
                Instant.now(), user, action,
                httpResp.getStatus(), duration
            ));
            
        } catch (Exception e) {
            auditLog.error(String.format(
                "{\"timestamp\":\"%s\",\"user\":\"%s\",\"action\":\"%s\"," +
                "\"error\":\"%s\"}",
                Instant.now(), user, action,
                e.getMessage()
            ));
            throw e;
        }
    }
    
    private String sanitize(String input) {
        // 移除敏感参数
        if (input == null) return "";
        return input.replaceAll("password=[^&]*", "password=***")
                    .replaceAll("token=[^&]*", "token=***");
    }
}
```

**审计查询脚本**：

```python
#!/usr/bin/env python3
# audit_query.py —— 审计查询

import json
import sys
from datetime import datetime, timedelta

def query_audit_logs(log_file, action=None, user=None, hours=24):
    """查询审计日志"""
    cutoff = datetime.now() - timedelta(hours=hours)
    results = []
    
    with open(log_file, 'r') as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                entry_time = datetime.fromisoformat(entry["timestamp"])
                
                if entry_time < cutoff:
                    continue
                
                if action and entry.get("action") != action:
                    continue
                
                if user and entry.get("user") != user:
                    continue
                
                results.append(entry)
            except:
                continue
    
    return results

def generate_audit_report(log_file, hours=24):
    """生成审计报告"""
    logs = query_audit_logs(log_file, hours=hours)
    
    # 按用户统计操作数
    user_actions = {}
    for entry in logs:
        user = entry.get("user", "unknown")
        action = entry.get("action", "unknown")
        user_actions.setdefault(user, {}).setdefault(action, 0)
        user_actions[user][action] += 1
    
    print(f"=== 审计报告 (最近{hours}小时) ===")
    print(f"总操作数: {len(logs)}")
    print()
    
    for user, actions in user_actions.items():
        print(f"用户: {user}")
        for action, count in actions.items():
            print(f"  {action}: {count}次")
    
    # 异常操作检测
    anomalies = []
    for entry in logs:
        if entry.get("status") and entry["status"] >= 400:
            anomalies.append(entry)
    
    if anomalies:
        print(f"\n⚠️  异常操作 ({len(anomalies)}次):")
        for a in anomalies[:10]:
            print(f"  {a['timestamp']} {a['user']} {a['action']} → {a['status']}")

if __name__ == '__main__':
    generate_audit_report("/opt/azkaban/logs/audit.log")
```

#### 步骤5：安全配置检查清单

**目标**：自动化安全基线检查。

```bash
#!/bin/bash
# security_checklist.sh —— 安全基线检查

echo "=== Azkaban 安全基线检查 ==="

# 1. 检查默认密码
echo "[1] 默认密码检查..."
if grep -q "password>azkaban<" conf/azkaban-users.xml 2>/dev/null; then
    echo "  ✗ 发现默认密码！请立即修改"
else
    echo "  ✓ 未发现默认密码"
fi

# 2. 检查HTTPS
echo "[2] HTTPS检查..."
if grep -q "jetty.use.ssl=true" conf/azkaban.properties; then
    echo "  ✓ HTTPS已启用"
else
    echo "  ✗ HTTPS未启用，建议启用"
fi

# 3. 检查密码加密
echo "[3] 密码加密检查..."
if grep -q "azkaban.encryption.enabled=true" conf/azkaban.properties; then
    echo "  ✓ 参数加密已启用"
else
    echo "  ⚠️  参数加密未启用，敏感参数可能泄露"
fi

# 4. 检查keytab权限
echo "[4] Keytab文件权限..."
KEYTAB=$(grep "azkaban.keytab.path" conf/azkaban.properties | cut -d= -f2)
if [ -f "$KEYTAB" ]; then
    KEYTAB_PERM=$(stat -c %a "$KEYTAB")
    if [ "$KEYTAB_PERM" = "600" ]; then
        echo "  ✓ Keytab权限正确 (600)"
    else
        echo "  ✗ Keytab权限过于宽松 ($KEYTAB_PERM)"
    fi
else
    echo "  ⚠️  Keytab文件未找到"
fi

# 5. 检查审计日志
echo "[5] 审计日志检查..."
if grep -q "user.manager.class=azkaban.user.LdapUserManager" conf/azkaban.properties; then
    echo "  ✓ LDAP认证已配置"
else
    echo "  ⚠️  仍在使用XML文件认证"
fi

echo "=== 安全检查完成 ==="
```

### 3.3 测试验证

```bash
# 1. 测试LDAP登录
ldapsearch -H ldap://ldap.company.com -D "cn=azkaban-svc,ou=service,dc=company,dc=com" \
  -w "service_password" -b "ou=people,dc=company,dc=com" "(sAMAccountName=testuser)"

# 2. 测试Kerberos票据
kinit -kt /etc/security/keytabs/azkaban.keytab azkaban/host@COMPANY.COM
klist
hdfs dfs -ls /  # 验证是否可以访问HDFS

# 3. 测试审计日志
tail -f /opt/azkaban/logs/audit.log
# 在另一个终端登录Azkaban，观察审计日志输出
```

## 4. 项目总结

### 安全方案对比

| 认证方式 | 安全性 | 易部署性 | 适用场景 |
|---------|--------|---------|---------|
| XML认证 | ★☆☆ | ★★★ | 开发测试 |
| LDAP | ★★★ | ★★☆ | 企业内网 |
| OAuth2/SSO | ★★★ | ★★☆ | 企业Web平台 |
| Kerberos | ★★★ | ★☆☆ | Hadoop生态 |

### 适用场景

- **适用**：金融/医疗等强合规行业、Hadoop Kerberized集群、多人协作的生产平台
- **不适用**：个人开发环境、非敏感数据的测试环境

### 注意事项

- LDAP集成后，XML中的用户账号仍然有效（如默认admin），务必清理
- Kerberos票据有24小时有效期，长任务需要自动续签
- 审计日志中务必脱敏密码、Token等敏感信息
- OAuth2集成通常需要二次开发，Azkaban没有现成的OAuth2 UserManager

### 常见踩坑经验

1. **LDAP连接成功但用户无法登录**：LDAP搜索filter配置不正确。使用`ldapsearch`调试正确的搜索模式。
2. **Kerberos认证后HDFS报Permission denied**：用户的HDFS目录权限不足，或代理用户配置未生效。检查`hadoop.proxyuser.azkaban.hosts`和`hadoop.proxyuser.azkaban.groups`。
3. **OAuth2 token刷新失败**：access token过期后Azkaban session仍有效，导致后续操作失败。解决：配置session过期时间短于token过期时间。

### 思考题

1. 如何实现"数据血缘级别的审计"——不仅记录"谁在什么时候执行了Flow"，还记录"这个Flow读了哪些表、写了哪些表"？
2. 如果需要实现"中国等级保护三级"的合规要求，Azkaban还需要在哪些方面做安全增强？
