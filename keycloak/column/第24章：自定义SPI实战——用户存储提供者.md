# 第24章：自定义SPI实战——用户存储提供者

## 1 项目背景

某企业成立于2008年，核心业务系统是一套自研的HR管理平台，承载着全公司8000余名员工的入职、调岗、离职、考勤、薪酬等完整生命周期数据。HR系统采用微服务架构，所有员工信息（姓名、工号、部门、职级、联系方式、密码哈希值）均通过REST API对外提供标准化查询。经过三年打磨，HR系统已成为企业内部公认的用户数据权威来源——"任何人的身份，以HR系统为准"早已是写入IT管理规范的基础原则。

2025年初，公司启动数字化办公平台升级，计划将分散的OA审批、CRM客户管理、BI报表、内部Wiki等12个系统统一纳入单点登录体系，Keycloak被选定为统一认证网关。IT部门面临第一个架构决策：用户数据如何管理？

方案A是"全量导入"——编写ETL脚本将HR系统的8000名员工数据一次性导入Keycloak本地数据库，并通过定时任务每30分钟增量同步变更。方案B是"按需联邦"——利用Keycloak的UserStorageProvider SPI，用户登录时Keycloak实时调用HR API查询用户信息和验证密码，Keycloak本地不持久化任何用户数据副本。CTO在评审会上明确拍板选方案B，理由掷地有声："HR系统是唯一的用户数据源，我们不创建第二个真相来源。数据一致性不是靠同步频率保证的——30分钟的同步延迟意味着离职员工在HR已标记失效后，还能通过同步窗口内的旧数据登录所有系统，这就是安全事故。"

痛点随之浮现。UserStorageProvider SPI是Keycloak扩展体系中最复杂的一类——不是单一接口，而是一组需要按需组合的接口契约：`UserLookupProvider`负责用户查找（按ID、用户名、邮箱），`CredentialInputValidator`负责密码校验，`CredentialInputUpdater`负责密码更新，每个接口的方法签名和语义都有精细约定。缓存策略的三个级别——`NO_CACHE`、`DEFAULT`、`IMPORT_ENABLED`——直接影响性能和数据鲜活性，选错了轻则每次登录都穿透HR API造成慢响应，重则用户离职后缓存中的旧数据让"已禁用"用户继续畅通无阻。密码校验的"黑盒"特性更是棘手：HR系统的密码哈希算法可能是自研的PBKDF2变体，Keycloak根本无法在本地重建密码比对逻辑，只能将明文密码交给HR API完成远端校验——这在安全审计中需要提供明确的通信加密方案。最后一个隐忧是性能：早高峰8000人集中登录时，Keycloak对HR API的并发请求瞬间飙升至每秒数百次，没有连接池和限流保护，HR系统可能先于Keycloak倒下。

本章深入Keycloak的UserStorageProvider SPI体系，从零编写一个对接HR REST API的用户存储提供者，完整覆盖接口实现、缓存策略、密码校验、属性映射和生产部署的每个细节。

---

## 2 项目设计——剧本式交锋对话

**小胖**（端着奶茶推门进来）：大师，大师！我昨天去图书馆还书的时候突然想通了User Federation。你看啊，图书馆的借书机（Keycloak）刷一下学生卡，它就去图书馆管理系统里查这个学生是不是在册、借书额度还有没有——它自己根本不存学生信息。这不就是你上次讲的UserStorageProvider嘛！但我有个疑问：这不就是调个API查一下用户存不存在吗？一个`getUser(username)`方法不就行了，为啥Keycloak要搞出一堆接口——什么`UserLookupProvider`、`CredentialInputValidator`、`CredentialInputUpdater`……这不是过度设计吗？

**大师**（笑了笑）：小胖，你借书这个比喻用得好，但你有没有想过——借书机不光要查"这个人存在吗"，它还要验证"这张卡是本人的吗"。如果图书馆管理系统的用户查找接口和密码校验接口完全是两套权限——查询接口只需普通权限就能调用，密码校验接口必须使用高级别凭据——你在借书机里只写一个`getUser`方法，代码里也混进了密码校验的API密钥，安全边界不就坍塌了吗？

**小白**（放下手中的笔，抬起头）：小胖，大师的意思是这个接口分层不是功能上的多余，是**职责隔离**。我正好有几个具体问题：`UserLookupProvider`要求实现`getUserById`、`getUserByUsername`、`getUserByEmail`三个方法，为什么是这三个组合？如果我只实现其中两个，Keycloak会怎样？还有，`CredentialInputValidator`的`supportsCredentialType`和`isConfiguredFor`方法看起来差不多——为什么要拆成两个独立方法？

**大师**：问得好，逐个讲透。先说接口分层。Keycloak的UserStorageProvider体系由5个核心子接口构成，每个代表一组独立的能力契约：

**UserLookupProvider**（用户查找）：声明三个查找入口——按ID、按用户名、按邮箱。为什么恰好是这三个？因为Keycloak的登录流程可能从不同入口触发：用户在登录页输入用户名时调用`getUserByUsername`；使用"忘记密码"流程时通过`getUserByEmail`定位用户；Token刷新或会话恢复时则通过Token中存储的User ID调用`getUserById`。如果你只实现其中两个，未实现的那个入口会返回`null`，对应的登录路径就直接断路。例如只实现`getUserByUsername`而没实现`getUserByEmail`，用户就无法通过邮箱找回密码。

**CredentialInputValidator**（凭证校验）：`supportsCredentialType("password")`回答"我能处理密码这种凭证类型吗"——这是一个静态能力声明（和具体用户无关）。`isConfiguredFor(user, "password")`回答"这个特定用户是否配置了密码"——依赖具体用户。区分两者的关键场景是：一个存储提供者可能声明"支持密码校验"，但某个特定用户（如第三方SSO引入的用户）根本没有设置密码——`isConfiguredFor`返回`false`，Keycloak就不会尝试对该用户做密码验证，而是跳过这个提供者去尝试其他认证方式。

**CredentialInputUpdater**（凭证更新）：负责处理用户在Keycloak端修改密码后的回写。如果你希望用户在Keycloak修改密码后结果写回HR系统，就实现此接口；如果采用READ_ONLY模式（第13章的联邦原则），就不实现它。

**UserQueryMethodsProvider**（用户列表查询）：提供分页搜索用户列表的能力，用于Admin Console中的"View all users"列表。如果不实现，联邦用户在列表中不可见，但仍可通过用户名登录。

**UserCredentialStore**（凭证本地存储）：允许Keycloak在本地持久化不透明凭证令牌，适用于HR API不返回密码给Keycloak的"黑盒"场景。

> **大师技术映射**：接口分层 → 餐厅的多个岗位。UserLookupProvider是前台（查座位），CredentialInputValidator是门卫（查证件），CredentialInputUpdater是收银台（结算挂账）。各司其职，互不干扰——门卫不需要知道收银台的流水，正如Validator不需要知道密码是怎么更新到后端的。

---

**小胖**（第二轮）：接口看明白了，我关心缓存！大师你上次说用户离职后HR系统状态已更新但Keycloak缓存里还是旧数据——那三种缓存策略到底怎么选？NO_CACHE是每次登录都查HR API，肯定最准但最慢。IMPORT_ENABLED听起来是"导入启用"——是把HR数据导入到Keycloak本地存起来了？那跟全量导入方案A有什么区别？

**小白**：这确实是个关键问题。还有，如果HR系统因为网络波动临时不可达——API调用超时了——Keycloak应该直接拒绝用户登录还是有什么降级方案？总不能因为HR API挂了5分钟，整个Keycloak就完全用不了了吧？

**大师**：缓存策略和降级方案，这两个点串在一起讲。

**NO_CACHE**：用户信息不缓存。每次认证都穿透HR API，数据100%准确，延迟取决于API响应时间（50-200ms）。适用于对数据实时性要求极高、用户量不大的场景（如管理员后台、少量特权用户）。不适用于大规模登录场景——8000人早高峰同时登录，每秒数百次API调用，HR系统压力不可接受。

**DEFAULT**：Keycloak使用内置的Infinispan分布式缓存存储用户数据。首次认证穿透HR API，认证成功后用户对象写入缓存。后续请求命中缓存，认证延迟降至5ms以下。缓存有效期由Realm配置中的`accessTokenLifespan`和Cache Policies共同决定。这是生产环境的默认选择——在性能和鲜活性之间取得平衡。风险在于：用户离职后，缓存未过期期间该用户仍能通过已有Token访问系统（Token有效期内的会话是有效的），但新登录请求在缓存过期后会重新调用HR API，此时HR返回"用户不存在"或"已禁用"，认证失败。

**IMPORT_ENABLED**：这个选项容易误解。它不是说Keycloak创建一个独立的本地数据库副本——而是指用户首次认证通过后，Keycloak在本地数据库的`USER_ENTITY`表中为联邦用户创建一条"导入记录"，包含基本的用户ID和用户名。这能大幅加速后续的查找操作（特别是用户列表场景），且允许管理员在Keycloak端为该联邦用户设置本地属性（如自定义角色映射、认证绑定等）。`IMPORT_ENABLED`的核心区别在于：本地有导入记录 ≠ 本地有完整用户数据——密码校验依然走HR API，属性查询依然按需从HR API拉取。它更像"Keycloak记住了我见过这个用户"而非"Keycloak复制了这个用户的所有信息"。

> **大师技术映射**：NO_CACHE → 次次去派出所查户口，每次都要排队但数据绝对最新。DEFAULT → 门禁系统记住了常出入的住户，刷脸秒过，但新搬来的访客需要查一次。IMPORT_ENABLED → 小区物业给住户办了张临时出入证，记住基础信息，但身份验证还是联网查公安系统。

---

**大师**（接着讲降级方案）：小白你问的HR API故障时的降级，分两层来看。第一层是**已登录用户的会话保护**——Keycloak的用户会话（UserSession）独立于UserStorageProvider，已登录用户的Token在有效期内完全不受HR API故障影响。用户不会在HR API宕机瞬间被强制下线。第二层是**新登录请求的处理**——这是真正棘手的地方。当HR API不可达时，所有新登录请求都会失败，因为密码校验必须穿透HR API验证。优雅降级的设计思路是：在`HRUserStorageProvider`的`isValid()`方法中捕获API连接超时异常，不是简单返回`false`（这会让用户感觉"密码错误"——体验极差），而是抛出特定的运行时异常让Keycloak返回一个系统级错误给用户（如"认证服务暂不可用，请稍后重试"）。更进一步，可以在Provider层做本地密码备份：首次认证成功后，将HR系统返回的密码哈希值缓存到Keycloak本地`FED_USER_CREDENTIAL`表（利用`UserCredentialStore`接口），HR API故障时降级到本地缓存的密码哈希校验——当然这引入了"密码数据有两份副本"的合规隐患，需要法务和信息安全团队评估。

---

## 3 项目实战

### 环境准备

| 组件 | 版本/说明 |
|------|----------|
| JDK | 17+ |
| Maven | 3.9+ |
| Keycloak | 26.x，本地开发模式 |
| Python (Mock HR API) | 3.10+，安装Flask |
| curl | API调试 |

### 步骤1：创建Mock HR API服务

**目标**：用Flask模拟企业HR系统的REST API，提供员工查找和密码校验端点。

启动Mock HR API：

```python
# hr-api-mock.py
from flask import Flask, jsonify, request

app = Flask(__name__)

EMPLOYEES = {
    "emp001": {
        "id": "emp001", "username": "zhangsan",
        "email": "zhangsan@company.com",
        "firstName": "三", "lastName": "张",
        "department": "研发部", "position": "高级工程师",
        "password": "hashed_password_abc"
    },
    "emp002": {
        "id": "emp002", "username": "lisi",
        "email": "lisi@company.com",
        "firstName": "四", "lastName": "李",
        "department": "市场部", "position": "市场经理",
        "password": "hashed_password_def"
    },
}

@app.route('/api/employees/<emp_id>')
def get_employee(emp_id):
    emp = EMPLOYEES.get(emp_id)
    if emp:
        return jsonify(emp)
    return jsonify({"error": "Not found"}), 404

@app.route('/api/employees/search')
def search_employees():
    username = request.args.get('username')
    email = request.args.get('email')
    for emp in EMPLOYEES.values():
        if username and emp['username'] == username:
            return jsonify(emp)
        if email and emp['email'] == email:
            return jsonify(emp)
    return jsonify({"error": "Not found"}), 404

@app.route('/api/employees/<emp_id>/validate-password', methods=['POST'])
def validate_password(emp_id):
    emp = EMPLOYEES.get(emp_id)
    if not emp:
        return jsonify({"valid": False}), 404
    input_password = request.json.get('password')
    # 模拟密码校验：生产环境应调用HR系统的实际密码验证接口
    valid = (input_password == f"password_{emp_id}")
    return jsonify({"valid": valid})

if __name__ == '__main__':
    app.run(port=5000)
```

启动命令：

```bash
pip install flask
python hr-api-mock.py
```

验证Mock API可用：

```bash
# 按用户名搜索
curl "http://localhost:5000/api/employees/search?username=zhangsan"

# 密码校验
curl -X POST http://localhost:5000/api/employees/emp001/validate-password \
  -H "Content-Type: application/json" \
  -d '{"password": "password_emp001"}'
```

运行结果：第一个命令返回zhangsan的完整员工JSON，第二个命令返回`{"valid": true}`。

### 步骤2：创建Maven项目

**目标**：搭建Keycloak SPI扩展的标准Maven项目结构。

项目结构：

```
hr-user-storage-provider/
├── pom.xml
└── src/
    └── main/
        ├── java/
        │   └── com/company/keycloak/
        │       ├── HRClient.java
        │       ├── HREmployee.java
        │       ├── HRUserAdapter.java
        │       ├── HRUserStorageProvider.java
        │       └── HRUserStorageProviderFactory.java
        └── resources/
            └── META-INF/
                └── services/
                    └── org.keycloak.storage.UserStorageProviderFactory
```

`pom.xml`核心依赖（基于第23章的SPI项目结构，增加HTTP客户端依赖）：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.company.keycloak</groupId>
    <artifactId>hr-user-storage-provider</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>

    <properties>
        <maven.compiler.source>17</maven.compiler.source>
        <maven.compiler.target>17</maven.compiler.target>
        <keycloak.version>26.0.0</keycloak.version>
    </properties>

    <dependencies>
        <dependency>
            <groupId>org.keycloak</groupId>
            <artifactId>keycloak-core</artifactId>
            <version>${keycloak.version}</version>
            <scope>provided</scope>
        </dependency>
        <dependency>
            <groupId>org.keycloak</groupId>
            <artifactId>keycloak-server-spi</artifactId>
            <version>${keycloak.version}</version>
            <scope>provided</scope>
        </dependency>
        <dependency>
            <groupId>org.keycloak</groupId>
            <artifactId>keycloak-server-spi-private</artifactId>
            <version>${keycloak.version}</version>
            <scope>provided</scope>
        </dependency>
        <dependency>
            <groupId>org.keycloak</groupId>
            <artifactId>keycloak-services</artifactId>
            <version>${keycloak.version}</version>
            <scope>provided</scope>
        </dependency>
        <dependency>
            <groupId>org.apache.httpcomponents</groupId>
            <artifactId>httpclient</artifactId>
            <version>4.5.14</version>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-shade-plugin</artifactId>
                <version>3.5.0</version>
                <executions>
                    <execution>
                        <phase>package</phase>
                        <goals><goal>shade</goal></goals>
                    </execution>
                </executions>
            </plugin>
        </plugins>
    </build>
</project>
```

### 步骤3：实现HTTP客户端与领域对象

**目标**：封装对HR API的HTTP调用，定义HR员工领域对象。

```java
// HREmployee.java
package com.company.keycloak;

public class HREmployee {
    private String id;
    private String username;
    private String email;
    private String firstName;
    private String lastName;
    private String department;
    private String position;

    // getters/setters 省略以节省篇幅
    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getUsername() { return username; }
    public void setUsername(String username) { this.username = username; }
    public String getEmail() { return email; }
    public void setEmail(String email) { this.email = email; }
    public String getFirstName() { return firstName; }
    public void setFirstName(String firstName) { this.firstName = firstName; }
    public String getLastName() { return lastName; }
    public void setLastName(String lastName) { this.lastName = lastName; }
    public String getDepartment() { return department; }
    public void setDepartment(String department) { this.department = department; }
    public String getPosition() { return position; }
    public void setPosition(String position) { this.position = position; }
}
```

```java
// HRClient.java
package com.company.keycloak;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.http.client.config.RequestConfig;
import org.apache.http.client.methods.CloseableHttpResponse;
import org.apache.http.client.methods.HttpGet;
import org.apache.http.client.methods.HttpPost;
import org.apache.http.entity.StringEntity;
import org.apache.http.impl.client.CloseableHttpClient;
import org.apache.http.impl.client.HttpClients;
import org.apache.http.util.EntityUtils;
import org.jboss.logging.Logger;

import java.util.HashMap;
import java.util.Map;

public class HRClient implements AutoCloseable {

    private static final Logger logger = Logger.getLogger(HRClient.class);

    private final CloseableHttpClient httpClient;
    private final String baseUrl;
    private final String apiKey;
    private final ObjectMapper objectMapper;

    public HRClient(String baseUrl, String apiKey) {
        this.baseUrl = baseUrl;
        this.apiKey = apiKey;
        this.objectMapper = new ObjectMapper();

        RequestConfig config = RequestConfig.custom()
                .setConnectTimeout(5000)      // 连接超时5秒
                .setSocketTimeout(10000)      // 读取超时10秒
                .setConnectionRequestTimeout(3000) // 从连接池获取连接超时3秒
                .build();

        this.httpClient = HttpClients.custom()
                .setDefaultRequestConfig(config)
                .setMaxConnTotal(50)          // 最大连接数
                .setMaxConnPerRoute(20)       // 每个路由的最大连接数
                .build();
    }

    public HREmployee getEmployee(String empId) {
        try {
            HttpGet get = new HttpGet(baseUrl + "/api/employees/" + empId);
            get.setHeader("X-API-Key", apiKey);
            try (CloseableHttpResponse resp = httpClient.execute(get)) {
                if (resp.getStatusLine().getStatusCode() == 200) {
                    String json = EntityUtils.toString(resp.getEntity());
                    return objectMapper.readValue(json, HREmployee.class);
                }
            }
        } catch (Exception e) {
            logger.errorf("Failed to get employee by id: %s, error: %s",
                    empId, e.getMessage());
        }
        return null;
    }

    public HREmployee searchByUsername(String username) {
        try {
            HttpGet get = new HttpGet(baseUrl
                    + "/api/employees/search?username=" + username);
            get.setHeader("X-API-Key", apiKey);
            try (CloseableHttpResponse resp = httpClient.execute(get)) {
                if (resp.getStatusLine().getStatusCode() == 200) {
                    String json = EntityUtils.toString(resp.getEntity());
                    return objectMapper.readValue(json, HREmployee.class);
                }
            }
        } catch (Exception e) {
            logger.errorf("Failed to search employee by username: %s", username);
        }
        return null;
    }

    public HREmployee searchByEmail(String email) {
        try {
            HttpGet get = new HttpGet(baseUrl
                    + "/api/employees/search?email=" + email);
            get.setHeader("X-API-Key", apiKey);
            try (CloseableHttpResponse resp = httpClient.execute(get)) {
                if (resp.getStatusLine().getStatusCode() == 200) {
                    String json = EntityUtils.toString(resp.getEntity());
                    return objectMapper.readValue(json, HREmployee.class);
                }
            }
        } catch (Exception e) {
            logger.errorf("Failed to search employee by email: %s", email);
        }
        return null;
    }

    public boolean validatePassword(String empId, String password) {
        try {
            HttpPost post = new HttpPost(baseUrl
                    + "/api/employees/" + empId + "/validate-password");
            post.setHeader("X-API-Key", apiKey);
            post.setHeader("Content-Type", "application/json");

            Map<String, String> body = new HashMap<>();
            body.put("password", password);
            String jsonBody = objectMapper.writeValueAsString(body);
            post.setEntity(new StringEntity(jsonBody, "UTF-8"));

            try (CloseableHttpResponse resp = httpClient.execute(post)) {
                if (resp.getStatusLine().getStatusCode() == 200) {
                    String json = EntityUtils.toString(resp.getEntity());
                    Map result = objectMapper.readValue(json, Map.class);
                    return Boolean.TRUE.equals(result.get("valid"));
                }
            }
        } catch (Exception e) {
            logger.errorf("Failed to validate password for employee: %s", empId);
        }
        return false;
    }

    @Override
    public void close() {
        try {
            httpClient.close();
        } catch (Exception e) {
            logger.warn("Failed to close HTTP client", e);
        }
    }
}
```

> **关键设计**：`HRClient`实现了`AutoCloseable`接口，确保在Provider被销毁时HTTP连接池正确释放。连接超时和读取超时分别设置，防止HR API慢响应导致Keycloak线程池耗尽。`setMaxConnTotal(50)`和`setMaxConnPerRoute(20)`构成基础限流——并发请求超过50个时，后续请求在`ConnectionRequestTimeout`（3秒）内排队等待。

### 步骤4：实现UserAdapter

**目标**：将HR员工对象适配为Keycloak的`UserModel`接口。

```java
// HRUserAdapter.java
package com.company.keycloak;

import org.keycloak.component.ComponentModel;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.storage.adapter.AbstractUserAdapterFederatedStorage;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class HRUserAdapter extends AbstractUserAdapterFederatedStorage {

    private final HREmployee employee;

    public HRUserAdapter(KeycloakSession session, RealmModel realm,
                         ComponentModel storageProviderModel,
                         HREmployee employee) {
        super(session, realm, storageProviderModel);
        this.employee = employee;
    }

    @Override
    public String getUsername() {
        return employee.getUsername();
    }

    @Override
    public String getEmail() {
        return employee.getEmail();
    }

    @Override
    public String getFirstName() {
        return employee.getFirstName();
    }

    @Override
    public String getLastName() {
        return employee.getLastName();
    }

    @Override
    public Map<String, List<String>> getAttributes() {
        Map<String, List<String>> attrs = new HashMap<>();
        attrs.put("department", List.of(employee.getDepartment()));
        attrs.put("position", List.of(employee.getPosition()));
        return attrs;
    }

    // 使用HR系统的ID作为Keycloak中的唯一标识
    // 警告：此方法返回的值必须在整个Provider生命周期内保持稳定
    @Override
    public String getId() {
        return employee.getId();
    }

    @Override
    public boolean equals(Object obj) {
        if (this == obj) return true;
        if (!(obj instanceof HRUserAdapter)) return false;
        HRUserAdapter other = (HRUserAdapter) obj;
        return getId() != null && getId().equals(other.getId());
    }

    @Override
    public int hashCode() {
        return getId() != null ? getId().hashCode() : 0;
    }
}
```

> **注意**：`getId()`的返回值是用户与Keycloak之间的"契约ID"，必须稳定不变。如果员工ID可能在HR系统中变化（极少见，但确实存在），需要增加一个不可变的代理ID映射层。同时务必重写`equals()`和`hashCode()`——Keycloak内部使用HashMap管理用户对象，如果两个`HRUserAdapter`表示同一用户但哈希值不同，会导致Session数据不一致。

### 步骤5：实现UserStorageProvider

**目标**：实现核心的UserStorageProvider及其子接口组合。

```java
// HRUserStorageProvider.java
package com.company.keycloak;

import org.jboss.logging.Logger;
import org.keycloak.component.ComponentModel;
import org.keycloak.credential.CredentialInput;
import org.keycloak.credential.CredentialModel;
import org.keycloak.credential.UserCredentialStore;
import org.keycloak.models.*;
import org.keycloak.storage.StorageId;
import org.keycloak.storage.UserStorageProvider;
import org.keycloak.storage.user.UserLookupProvider;
import org.keycloak.credential.CredentialInputValidator;

public class HRUserStorageProvider implements UserStorageProvider,
        UserLookupProvider, CredentialInputValidator {

    private static final Logger logger = Logger.getLogger(HRUserStorageProvider.class);

    private final KeycloakSession session;
    private final ComponentModel model;
    private final HRClient hrClient;

    public HRUserStorageProvider(KeycloakSession session, ComponentModel model) {
        this.session = session;
        this.model = model;

        String apiUrl = model.getConfig().getFirst("hrApiUrl");
        String apiKey = model.getConfig().getFirst("hrApiKey");
        this.hrClient = new HRClient(apiUrl, apiKey);
    }

    @Override
    public UserModel getUserByUsername(RealmModel realm, String username) {
        HREmployee emp = hrClient.searchByUsername(username);
        if (emp == null) return null;
        return new HRUserAdapter(session, realm, model, emp);
    }

    @Override
    public UserModel getUserByEmail(RealmModel realm, String email) {
        HREmployee emp = hrClient.searchByEmail(email);
        if (emp == null) return null;
        return new HRUserAdapter(session, realm, model, emp);
    }

    @Override
    public UserModel getUserById(RealmModel realm, String id) {
        // StorageId.decode() 用于从复合ID中提取外部系统的实际ID
        String externalId = StorageId.externalId(id);
        HREmployee emp = hrClient.getEmployee(externalId);
        if (emp == null) return null;
        return new HRUserAdapter(session, realm, model, emp);
    }

    @Override
    public boolean isValid(RealmModel realm, UserModel user,
            CredentialInput input) {
        if (!(input instanceof UserCredentialModel)) return false;
        if (!CredentialModel.PASSWORD.equals(input.getType())) return false;

        String rawPassword = input.getChallengeResponse();
        return hrClient.validatePassword(user.getId(), rawPassword);
    }

    @Override
    public boolean supportsCredentialType(String credentialType) {
        return CredentialModel.PASSWORD.equals(credentialType);
    }

    @Override
    public boolean isConfiguredFor(RealmModel realm, UserModel user,
            String credentialType) {
        return supportsCredentialType(credentialType);
    }

    @Override
    public void close() {
        hrClient.close();
    }

    @Override
    public void preRemove(RealmModel realm) {
        // Provider被删除前的清理工作
        logger.infof("HR User Storage Provider is being removed "
                + "from realm: %s", realm.getName());
    }

    @Override
    public void preRemove(RealmModel realm, GroupModel group) {
        // 无需处理
    }

    @Override
    public void preRemove(RealmModel realm, RoleModel role) {
        // 无需处理
    }
}
```

> **关键细节**：`getUserById`中使用了`StorageId.externalId(id)`来解包Keycloak的复合ID。Keycloak内部会给联邦用户生成格式为`f:{providerId}:{externalId}`的复合ID，直接从Token或会话中取出的ID是这种复合格式，必须解包后才能用于HR API查询。这是联邦模式下最高发的一个"查不到用户"的坑。

### 步骤6：实现ProviderFactory

**目标**：定义Provider的元信息、配置项和生命周期管理。

```java
// HRUserStorageProviderFactory.java
package com.company.keycloak;

import org.keycloak.Config;
import org.keycloak.component.ComponentModel;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.provider.ProviderConfigProperty;
import org.keycloak.provider.ProviderConfigurationBuilder;
import org.keycloak.storage.UserStorageProviderFactory;
import org.keycloak.storage.UserStorageProviderModel;
import org.jboss.logging.Logger;

import java.util.List;

public class HRUserStorageProviderFactory
        implements UserStorageProviderFactory<HRUserStorageProvider> {

    private static final Logger logger = Logger.getLogger(
            HRUserStorageProviderFactory.class);

    public static final String PROVIDER_ID = "hr-user-storage";

    @Override
    public HRUserStorageProvider create(KeycloakSession session,
            ComponentModel model) {
        return new HRUserStorageProvider(session, model);
    }

    @Override
    public String getId() {
        return PROVIDER_ID;
    }

    @Override
    public String getHelpText() {
        return "HR系统用户存储提供者——对接企业内部HR系统REST API，"
                + "支持按需用户查找和密码校验";
    }

    @Override
    public List<ProviderConfigProperty> getConfigProperties() {
        return ProviderConfigurationBuilder.create()
                .property()
                    .name("hrApiUrl")
                    .label("HR API地址")
                    .helpText("HR系统API的Base URL，如 http://hr.company.com:5000")
                    .type(ProviderConfigProperty.STRING_TYPE)
                    .defaultValue("http://localhost:5000")
                    .add()
                .property()
                    .name("hrApiKey")
                    .label("API密钥")
                    .helpText("HR系统REST API的认证密钥（通过X-API-Key头传递）")
                    .type(ProviderConfigProperty.PASSWORD)
                    .secret(true)
                    .add()
                .property()
                    .name("cachePolicy")
                    .label("缓存策略")
                    .helpText("NO_CACHE: 每次穿透API | DEFAULT: 标准缓存")
                    .type(ProviderConfigProperty.LIST_TYPE)
                    .options("NO_CACHE", "DEFAULT")
                    .defaultValue("DEFAULT")
                    .add()
                .build();
    }

    @Override
    public void init(Config.Scope config) {
        logger.info("HR User Storage Provider Factory initialized");
    }

    @Override
    public void postInit(KeycloakSessionFactory factory) {
        // Factory初始化完成后的回调，可用于预热连接池等
    }

    @Override
    public void close() {
        logger.info("HR User Storage Provider Factory shutting down");
    }
}
```

> **ProviderConfigProperty.PASSWORD类型**会将配置值标记为敏感信息，在Admin Console中以密码掩码形式显示，不会被明文记录到导出文件中。

### 步骤7：注册SPI服务

**目标**：通过Java SPI机制注册ProviderFactory。

创建`src/main/resources/META-INF/services/org.keycloak.storage.UserStorageProviderFactory`文件，内容：

```
com.company.keycloak.HRUserStorageProviderFactory
```

### 步骤8：编译与部署

```bash
cd hr-user-storage-provider
mvn clean package
```

编译成功后，将生成的JAR包复制到Keycloak的providers目录：

```bash
cp target/hr-user-storage-provider-1.0.0.jar $KEYCLOAK_HOME/providers/
```

重启Keycloak服务后，在启动日志中应能看到：

```
INFO  [com.company.keycloak.HRUserStorageProviderFactory] (main)
HR User Storage Provider Factory initialized
```

### 步骤9：在Admin Console中配置

**目标**：通过管理控制台添加并配置HR用户存储提供者。

操作路径：
1. 登录Admin Console → 选择目标Realm
2. 左侧菜单：**User Federation** → **Add provider**
3. 在下拉列表中应出现新选项：**hr-user-storage**
4. 配置参数：

| 配置项 | 值 | 说明 |
|--------|---|------|
| hrApiUrl | `http://host.docker.internal:5000` | Docker环境下用此地址访问宿主机（Keycloak运行在Docker中时）。本机直连模式填`http://localhost:5000` |
| hrApiKey | `secret_key_2024` | 与Mock HR API约定一致的任意凭据 |
| cachePolicy | `DEFAULT` | 首次认证后缓存用户信息 |

保存配置后，进入**Users** → **View all users**，在搜索框中输入`zhangsan`——应能看到该用户出现在列表中，来源标记为**Federated**。

### 步骤10：测试联邦用户登录

```bash
# zhangsan使用HR密码登录Keycloak
curl -s -X POST http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -d "client_id=oms-frontend" \
  -d "username=zhangsan" \
  -d "password=password_emp001" \
  -d "grant_type=password" | jq '{access_token: (.access_token[0:50]+"..."), expires_in}'
```

运行结果：成功返回`access_token`和`expires_in`。密码由HR API的`/validate-password`端点校验，Keycloak本地不存储和比对任何密码信息。

验证属性映射——解析Token中的属性：

```bash
# 获取Token
TOKEN=$(curl -s -X POST http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -d "client_id=oms-frontend" \
  -d "username=zhangsan" \
  -d "password=password_emp001" \
  -d "grant_type=password" | jq -r '.access_token')

# 解码Payload查看属性
echo "$TOKEN" | cut -d'.' -f2 | base64 -d 2>/dev/null | jq '{sub, preferred_username, name}'
```

### 可能遇到的坑

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| Admin Console不显示`hr-user-storage`选项 | SPI注册文件路径不正确或JAR未被加载 | 检查`META-INF/services/`文件路径和内容是否完全匹配。确认JAR在`providers/`目录下且重启了Keycloak |
| 用户搜索不到 | `getUserById`未使用`StorageId.externalId()`解包复合ID | 确保在`getUserById`中调用了`StorageId.externalId(id)` |
| 同一个HR用户多次出现在用户列表中 | `AbstractUserAdapterFederatedStorage`的`getId()`返回值在多次查询间不一致 | 确保`HRUserAdapter.getId()`始终返回稳定的HR系统ID，且重写了`equals()`和`hashCode()` |
| HTTP连接池耗尽 | `close()`方法未正确释放`CloseableHttpClient` | `HRClient`实现`AutoCloseable`，`HRUserStorageProvider.close()`中调用`hrClient.close()` |
| `NO_CACHE`模式下每次登录都穿透API，响应极慢 | 缓存策略选错 | 生产环境默认使用`DEFAULT`，仅对实时性要求极高的少数场景使用`NO_CACHE` |
| HR API故障导致登录完全不可用 | Provider中未捕获网络异常，异常向上传播导致Keycloak内部错误 | 在`HRClient`的所有方法中添加异常捕获和日志记录，区分"用户不存在"（返回null）和"服务不可用"（记录ERROR日志） |

### 测试验证清单

- [ ] Mock HR API健康检查：`curl http://localhost:5000/api/employees/emp001` 返回员工数据
- [ ] Provider在Admin Console中可发现：User Federation → Add provider 列表中可见`hr-user-storage`
- [ ] 配置保存成功无报错，Cache Policy正确显示
- [ ] Users列表中可搜索HR系统用户，标记为Federated
- [ ] HR用户使用HR密码成功登录Keycloak并获取Token
- [ ] 输入错误密码时登录失败（确认密码由HR API校验）
- [ ] Token中包含HR适配器映射的属性（department、position）
- [ ] 停止Mock HR API后，缓存内的用户依然可登录（DEFAULT策略下），新用户登录失败且错误提示为认证服务不可用

---

## 4 项目总结

### 三种用户管理方案对比

| 维度 | UserStorageProvider (按需联邦) | 全量数据导入 | LDAP Federation |
|------|-------------------------------|-------------|-----------------|
| 数据一致性 | 实时，无同步延迟 | 取决于同步频率（如30分钟） | 实时穿透LDAP，但属性受缓存TTL影响 |
| 外部系统依赖 | 强依赖——HR API故障则新登录不可用 | 弱依赖——导入后即可独立运作 | 强依赖——LDAP服务器宕机影响认证 |
| 认证性能 | 首次较慢（穿透API），后续快（缓存命中） | 快（本地数据库查询） | 首次较慢（LDAP Bind+Search），后续快（缓存命中） |
| 实现复杂度 | 高——需自行实现接口体系、HTTP通信、异常处理 | 低——ETL同步脚本即可 | 中——Keycloak内置LDAP适配器，配置为主 |
| 自定义灵活性 | 极高——可接入任意后端（REST/GraphQL/gRPC/数据库） | 低 | 中——受限于LDAP Schema映射 |
| 运维成本 | 中——需维护REST API链路和降级方案 | 低——定时同步任务 | 中——需维护LDAP连接池和健康检查 |
| 密码管理 | 密码由外部系统管理（黑盒校验） | Keycloak本地管理密码 | 密码由LDAP管理（单向联邦） |

### 适用场景

- **REST API对接外部用户系统**：企业内部HR系统、CRM系统、第三方用户中心通过REST API提供用户数据，Keycloak作为统一认证网关按需查询——本章的主线场景。
- **自定义认证后端**：用户密码不存储在Keycloak数据库中，而是通过自定义算法（如国产密码SM3/SM4）或外部密码机（HSM）验证。
- **企业统一用户中心接入**：公司已建设统一的用户中心服务（UAC），Keycloak不维护本地用户数据，所有认证操作直接转发至用户中心。
- **多后端用户源聚合**：同时对接HR系统（正式员工）、外包管理平台（外包人员）、微信公众号（临时访客），通过多个UserStorageProvider的优先级顺序依次查找用户。

### 不适用场景

- **对外部系统可用性要求极高、无法容忍因API故障导致登录中断**：应采用全量导入+定期同步方案，确保Keycloak本地有完整的用户数据副本。
- **外部系统用户量巨大（百万级）且每次认证都需要复杂查询**：应评估批量预加载+本地缓存的混合方案，而非每次认证都穿透API。

### 生产注意事项

1. **API超时和降级处理**：HTTP连接超时、读取超时、Socket超时必须分别设置合理的值（建议连接5秒、读取10秒）。API故障时，`isValid()`方法应区分"密码不匹配"和"服务不可达"，给用户友好的错误提示而非统一返回"密码错误"。
2. **缓存策略选择**：`NO_CACHE`仅用于管理员/审计等强实时场景，80%的生产场景选`DEFAULT`。如果启用`IMPORT_ENABLED`，务必理解"导入记录"和"全量导入"的本质区别。
3. **UserAdapter的equals和hashCode**：这是生产中最容易踩到的坑。Keycloak内部多处使用HashMap管理用户对象，如果你不重写这两个方法，同一个联邦用户可能被当作两个不同的对象，导致Session数据冲突。
4. **HTTP连接池大小**：`setMaxConnTotal`和`setMaxConnPerRoute`应根据HR API的实际承载能力和Keycloak的并发登录量合理设置。连接池耗尽会导致认证请求无限排队——这是生产环境P99延迟突增的常见根因。
5. **API认证凭据的存储安全**：`ProviderConfigProperty.PASSWORD`类型会将API密钥作为敏感信息仅在内存中保存。配置页面中输入后即不可见，导出Realm时该字段会被脱敏。

### 常见踩坑经验

- **Federated User的缓存刷新时机**：某生产环境将缓存TTL设置为24小时，导致员工离职后（HR API中状态已更新）在Keycloak中仍能登录长达24小时。解决方案是将缓存TTL缩短为1小时，并对账户状态变更（离职/禁用）实现"主动失效"——在HR系统的离职操作中回调Keycloak Admin API清除该用户的缓存。
- **并发安全——HTTP客户端非线程安全使用**：早期版本的Apache HttpClient 4.x的`CloseableHttpClient`是线程安全的，但`HttpContext`和相关对象不是。某团队在Provider中共享了一个可变的`HttpContext`对象，高并发下产生`ConcurrentModificationException`。正确做法是每次请求创建独立的闭包，不共享可变状态。
- **StorageId解析错误**：某开发者在`getUserById`中直接将Keycloak传入的复合ID（格式`f:hr-user-storage:emp001`）发给HR API查询，HR API自然返回404。忽略了`StorageId.externalId()`的调用，调试了一整天。

### 思考题

1. **百万级用户搜索性能**：如果HR系统有100万员工，Keycloak的"View all users"列表每次请求都会调用Provider的搜索方法遍历全部数据——这对HR API是不可接受的。请设计一种分页搜索方案，将Admin Console的搜索条件（用户名前缀、邮箱前缀、部门）转化为HR API的分页查询参数，同时限制单次返回的最大条数（如100条），避免全量加载。

2. **HR系统故障降级方案**：当HR API完全不可用时，如何实现Keycloak的优雅降级——已登录用户不受影响，新登录用户使用Keycloak本地缓存的密码哈希副本完成认证（首次登录时将HR系统的密码哈希值通过`UserCredentialStore`接口缓存到本地`FED_USER_CREDENTIAL`表）。请分析此降级方案的安全性和合规性风险，并思考如何通过"降级模式"的状态标记和自动恢复机制来管理整个生命周期。

---

> **推广计划提示**：本章面向平台开发工程师和安全架构师。建议先阅读第13章（LDAP/AD联邦）建立Federation的概念基础，再阅读第23章（自定义SPI入门）了解SPI的整体架构。运维团队重点关注本章的缓存策略选择、HTTP连接池配置和API故障降级方案，开发团队重点关注接口实现细节和UserAdapter的正确写法。后续第25章（自定义认证流程）将在此基础上进一步扩展自定义Authenticator的开发。
