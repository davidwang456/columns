

## 背景

> 小白：什么才是好的代码？怎么才能成为一个合格的软件工程师呢？
>
> 扫地僧：好的代码必是高可用的，这里面有两层含义：首先它必须是安全的代码，其次才是代码的质量。
>
> 小白：我不是很明白，你能举个例子吗？
>
> 扫地僧：你还记得前段时间，fastjson被爆出过多次存在漏洞，咱们的json解析工具fastjson经历了多次升级的事情吗？最后甚至我们还讨论过使用Gson还是jackson代替Fastjson来实现json的解析。另外，Struts2被很多人放弃，和它频繁爆出漏洞也有一定的关系。
>
> 小白：我一直以为漏洞只跟安全人员有关呢！那开发人员怎么保证开发的代码是安全的呢？
>
> 扫地僧：要想保证代码是安全的，在做开发时，重要的是要始终掌握最重要的安全风险和漏洞。从OWASP入门是个不错的途径。

OWASP Top 10是一个针对开发人员和web应用安全的标准文档。它代表了关于web应用程序最关键的安全风险的广泛共识。

OWASP代表Open Web Application Security Project，这是一个在线社区，在Web应用程序安全领域中提供文章，方法论，文档，工具和技术。OWASP每年会公布去年的十大漏洞，2020年OWASP的十大漏洞是：

- 注入
- 失效身份验证和会话管理
- 敏感信息泄露
- XML外部实体注入攻击（XXE）
- 存取控制中断
- 安全性错误配置
- 跨站脚本攻击（XSS）
- 不安全的反序列化
- 使用具有已知漏洞的组件
- 日志记录和监控不足

注入漏洞作为top 1的漏洞，特别要引起我们的注意，注入分类有很多种类，先以最常见的sql注入漏洞来看。

## Sql注入漏洞示例

### SQL注入攻击及防御

不幸的是，SQL注入攻击非常常见，这是由于两个因素:

- SQL注入漏洞的显著流行

- 目标的吸引力(例如，数据库通常包含应用程序的所有有趣/关键数据)。

发生了这么多成功的SQL注入攻击，这多少有点丢人，因为在代码中避免SQL注入漏洞非常简单。

当软件开发人员创建包含用户提供的输入的动态数据库查询时，会引入SQL注入缺陷。避免SQL注入缺陷很简单。开发人员需要:

- 停止编写动态查询;

- 和(或)防止包含恶意SQL的用户提供的输入影响执行查询的逻辑。

本文提供了一组简单的技术，通过避免这两个问题来防止SQL注入漏洞。这些技术实际上可以用于任何类型的数据库的任何编程语言。还有其他类型的数据库，比如XML数据库，它们也可能有类似的问题(例如XPath和XQuery注入)，这些技术也可以用来保护它们。

### 注入漏洞示例1

最简单的一个sql注入：

```java
String query = "SELECT account_balance FROM user_data WHERE user_name = "
             + request.getParameter("customerName");
try {
    Statement statement = connection.createStatement( ... );
    ResultSet results = statement.executeQuery( query );
}
...
```

想必很多人初学时都写过类似的代码吧？这就是一个典型的有Sql注入漏洞的例子，用户可以通过customerName参数传入一些特殊字符如'%%'或者‘1’=‘1’等类似语句来获取到系统数据。如何防御上面的sql漏洞呢？

### 主要的防御

**选项1:使用PreparedStatement语句进行参数化查询**

所有开发人员都应该首先学习如何使用带变量绑定(又名参数化查询)的预准备语句来编写数据库查询。它们编写简单，比动态查询更容易理解。参数化查询迫使开发人员首先定义所有SQL代码，然后再将每个参数传递给查询。这种编码风格允许数据库区分代码和数据，而不管用户提供了什么输入。

准备好的语句可以确保攻击者不能改变查询的意图，即使攻击者插入了SQL命令。在下面的安全示例中，如果攻击者输入用户id为tom'或'1'='1，则参数化查询将不会受到攻击，而是查找与tom'或'1'='1字面上匹配的整个字符串的用户名。

示例：

```java
// This should REALLY be validated too
String custname = request.getParameter("customerName");
// Perform input validation to detect attacks
String query = "SELECT account_balance FROM user_data WHERE user_name = ? ";
PreparedStatement pstmt = connection.prepareStatement( query );
pstmt.setString( 1, custname);
ResultSet results = pstmt.executeQuery( );
```

注意：此处使用了PreparedStatement而不是Statement。

**选项2:使用存储过程**

对于SQL注入，存储过程并不总是安全的。但是，在安全实现时，某些标准存储过程编程构造与使用参数化查询具有相同的效果，而参数化查询是大多数存储过程语言的标准。

它们要求开发人员只使用自动参数化的参数构建SQL语句，除非开发人员做了一些很大程度上超出常规的事情。预准备语句和存储过程之间的区别在于，存储过程的SQL代码被定义并存储在数据库本身中，然后从应用程序调用。这两种技术在防止SQL注入方面具有相同的有效性，因此您的组织应该选择最适合您的方法。

注意:“安全实现”意味着存储过程不包括任何不安全的动态SQL生成。开发人员通常不会在存储过程中生成动态SQL。然而，这是可以做到的，但是应该避免。如果无法避免，存储过程必须使用本文描述的输入验证或正确转义，以确保不能使用用户提供给存储过程的所有输入将SQL代码注入动态生成的查询。审计人员应该始终查找sp_execute、execute或exec在SQL Server存储过程中的使用情况。对于其他供应商的类似功能，也需要类似的审计指南。

```java
// This should REALLY be validated
String custname = request.getParameter("customerName");
try {
  CallableStatement cs = connection.prepareCall("{call sp_getAccountBalance(?)}");
  cs.setString(1, custname);
  ResultSet results = cs.executeQuery();
  // … result set handling
} catch (SQLException se) {
  // … logging and error handling
}
```

**选项3:白名单输入验证**

SQL查询的各个部分都不是使用绑定变量的合法位置，比如表或列的名称和排序顺序指示符(ASC或DESC)。在这种情况下，输入验证或查询重新设计是最合适的防御。对于表或列的名称，理想情况下，这些值来自代码，而不是来自用户参数。

但是，如果用户参数值用于目标不同的表名和列名，那么应该将参数值映射到合法/预期的表名或列名，以确保未验证的用户输入不会出现在查询中。请注意，这是设计不良的症状，如果时间允许，应该考虑完全重写。

```java
String tableName;
switch(PARAM):
  case "Value1": tableName = "fooTable";
                 break;
  case "Value2": tableName = "barTable";
                 break;
  ...
  default      : throw new InputValidationException("unexpected value provided"
                                                  + " for table name");
```

**选项4:转义所有用户提供的输入**

这一技术只能作为最后的手段，当以上方法都不可行时。输入验证可能是一个更好的选择，因为与其他防御相比，这种方法比较脆弱，而且我们不能保证它将在所有情况下防止所有SQL注入。

这种技术是在将用户输入放入查询之前对其进行转义。它的实现与数据库非常相关。通常只建议在实现输入验证成本不高的情况下对遗留代码进行翻新。应该使用参数化查询、存储过程或为您构建查询的某种对象关系映射器(ORM)来构建或重写从头构建的应用程序或需要低风险容忍度的应用程序。

这个技巧是这样的。每个DBMS都支持一个或多个特定于特定类型查询的字符转义方案。如果您使用正确的数据库转义方案转义所有用户提供的输入，DBMS就不会将该输入与开发人员编写的SQL代码混淆，从而避免任何可能的SQL注入漏洞。

OWASP企业安全API (ESAPI)是一个免费的、开放源码的web应用程序安全控制库，它使程序员更容易编写低风险的应用程序。ESAPI库的设计目的是让程序员更容易地将安全性改进到现有应用程序中。

```java
Encoder oe = new OracleEncoder();
String query = "SELECT user_id FROM user_data WHERE user_name = '"
+ oe.encode( req.getParameter("userID")) + "' and user_password = '"
+ oe.encode( req.getParameter("pwd")) +"'";
```

**额外的防御:**

- 执行最少的特权

- 执行白名单输入验证作为辅助防御

### 注入漏洞2示例

错误使用参数符号$而未作检查：

```mysql
Select * from users where user_name like ‘%${username}%’
```

用参数符号$时，MyBatis直接用字符串拼接把参数和SQL语句拼接在一起，然后执行。众所周知，这种情况非常危险，极容易产生SQL注入漏洞。

正确写法：

```mysql
select * from users where user_name like concat(‘%’,#{username}, ‘%’)
```

## 总结

**预防注入规则**

- 规则#1(执行正确的输入验证)

​       执行正确的输入验证。也建议使用适当的规范化进行积极的或“白名单”输入验证，但这并不是完全的防护措施，因为许多应用程序在输入中需要特殊字符。

- 规则#2(使用安全的API)

​       首选的方法是使用安全的API，它可以完全避免使用解释器，或者提供参数化的接口。要小心api，比如存储过程，它们是参数化的，但在底层仍然可能引入注入。

- 规则#3(上下文转义用户数据)

​      如果参数化的API不可用，您应该使用该解释器的特定转义语法仔细转义特殊字符。

**替换符$使用应特别注意**

- 能不使用拼接就不要使用拼接，这应该也是避免 SQL 注入最基本的原则
- 在使用 `${}` 传入变量的时候，一定要注意变量的引入和过滤，避免直接通过 `${}` 传入外部变量。
- 在mybatis-generator自动生成的SQL语句中，order by使用的是$，也就是简单的字符串拼接，这种情况下极易产生SQL注入。需要开发者特别注意。