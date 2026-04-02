# mybatis 可以支持几种打印日志的方式？这几种方式可以混用吗？原理是什么？

## 背景

> 小白：以前我打印mysql都是通过控制台打印出来，这样在开发时比较方便。但在生产上，需要打印在文件中，我希望使用log4j2将MyBatis执行SQL按天保存到文件，该怎么做呢？
>
> 扫地僧：mybatis和Mybatis的集成，想必你也清楚。
>
> 小白:根据官网的介绍，只要将log4j2的依赖添加，将log4j2.xml文件配置到classpath目录下，并在mybatis上配置日志打印为log4j2即可。
>
> 扫地僧：是的，现在想要将将MyBatis执行SQL按天保存到文件，只需要配置好log4j2.xml文件即可。为了你快速上手，我简单描述一下log4j2：
>
> Log4j2由三个重要的组成构成：日志记录器(Loggers)，输出端(Appenders)和日志格式化器(Layout)。
>
> 1.日志记录器(Loggers)：控制要输出哪些日志记录语句，对日志信息进行级别限制。
> 2.输出端(Appenders)：指定了日志将打印到控制台还是文件中。
>
> 3.日志格式化器(Layout)：控制日志信息的显示格式。
>
> 百闻不如一见，来看个简单实例就明白了。

## Mybatis集成log4j实例实现按天保存

### 准备工作

mysql数据库,本实例的版本为:8.0.16

mysql客户端SQLyog(免费，不需要注册码)或者navicat for mysql

创建数据库davidwang456和表

```java
CREATE database davidwang456;
use davidwang456;

DROP TABLE IF EXISTS  student;
CREATE TABLE `student` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `first_name` varchar(100) DEFAULT NULL,
  `last_name` varchar(100) DEFAULT NULL,
  `age` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

mock数据

```sql
INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (1, 'wang1', 'david1', 21);
INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (2, 'wang2', 'david2', 22);
INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (3, 'wang3', 'david3', 23);
INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (4, 'wang4', 'david4', 24);
INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (5, 'wang5', 'david5', 25);
INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (6, 'wang6', 'david6', 26);
INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (7, 'wang7', 'david7', 27);
INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (8, 'wang8', 'david8', 28);
```

### 创建maven项目

创建maven项目，其完整的代码结果如下：

![image-20210712092319716](img\loggingtest.png)

#### 添加依赖

pom.xml

```
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>LoggingTest</artifactId>
  <version>4.1.0-SNAPSHOT</version>
  <properties>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    <maven.compiler.source>1.8</maven.compiler.source>
    <maven.compiler.target>1.8</maven.compiler.target>
  </properties>
  <dependencies>
	<dependency>
	    <groupId>org.mybatis</groupId>
	    <artifactId>mybatis</artifactId>
	    <version>3.5.6</version>
	</dependency>
	<dependency>
	    <groupId>org.projectlombok</groupId>
	    <artifactId>lombok</artifactId>
	    <version>1.18.16</version>
	    <scope>provided</scope>
	</dependency>
    <dependency>
    	<groupId>mysql</groupId>
    	<artifactId>mysql-connector-java</artifactId>
    	<version>8.0.16</version>
	</dependency>

<!-- log4j2 -->
		<dependency>
            <groupId>org.apache.logging.log4j</groupId>
            <artifactId>log4j-api</artifactId>
            <version>2.14.0</version>
        </dependency>
        <dependency>
            <groupId>org.apache.logging.log4j</groupId>
            <artifactId>log4j-core</artifactId>
            <version>2.14.0</version>
        </dependency>
		<dependency>
             <groupId>org.apache.logging.log4j</groupId>
             <artifactId>log4j-web</artifactId>
             <version>2.14.0</version>
        </dependency>	
		
   </dependencies>
</project>
```

#### 实体

**数据库实体**

```java
package com.davidwang456.mybatis.logging;

import java.io.Serializable;

import lombok.Data;

@Data
public class StudentDTO implements Serializable{
	private static final long serialVersionUID = 1L;
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	@Override
	   public String toString() {
	    return "student [id=" + id + ", firstName=" + firstName
	    		 + ", lastName=" + lastName + ", age=" +age+ ']';
	   }
}
```

**查询实体**

```java
package com.davidwang456.mybatis.logging;

import lombok.Data;

@Data
public class StudentQueryDTO {
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	//排序列
	private String sort;
	//排序 DESC|ASC
	private String orderBy;	
}
```

#### 配置

**数据库配置**

SqlMapConfig.xml

```xml
<?xml version = "1.0" encoding = "UTF-8"?>
<!DOCTYPE configuration PUBLIC "-//mybatis.org//DTD Config 3.0//EN" "http://mybatis.org/dtd/mybatis-3-config.dtd">
<configuration>
	<settings>
		<setting name="logImpl" value="LOG4J2"/>
		<setting name="mapUnderscoreToCamelCase" value="true"/>
   </settings>
   
   <environments default = "development">
      <environment id = "development">
         <transactionManager type = "JDBC"/> 			
         <dataSource type = "POOLED">
            <property name = "driver" value = "com.mysql.cj.jdbc.Driver"/>
            <property name = "url" value = "jdbc:mysql://localhost:3306/davidwang456?characterEncoding=UTF-8&amp;useSSL=false&amp;useLegacyDatetimeCode=false&amp;serverTimezone=UTC"/>
            <property name = "username" value = "root"/>
            <property name = "password" value = "wangwei456"/>
         </dataSource>           
      </environment>
   </environments>  
   	
    <mappers>
      <mapper resource = "StudentMapper.xml"/>
   </mappers> 
  
</configuration>
```

其中，配置mybatis.configuration.map-underscore-to-camel-case=true定义了支持驼峰形式，配置logImpl定义了日志打印的类为log4j2.

在src/main/resources目录下,定义log4j2.xml文件

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Configuration status="ERROR" monitorInterval="600">
    <Properties>
        <property name="pattern">%d{yyyy/MM/dd HH:mm:ss.SSS} [%p] %t %c %m%n</property>
        <property name="basePath">D:/tmp/log</property>
    </Properties>

    <Appenders>

        <Console name="console" target="SYSTEM_OUT">
            <PatternLayout pattern="${pattern}"/>
        </Console>

        <RollingRandomAccessFile name="fileLogger"
                                 fileName="${basePath}/mybatis-sql.log"
                                 filePattern="${basePath}/mybatis-sql-%d{yyyy-MM-dd}.log"
                                 append="true">
            <PatternLayout pattern="${pattern}"/>

            <Policies>
                <TimeBasedTriggeringPolicy interval="1" modulate="true"/>
                <SizeBasedTriggeringPolicy size="100MB"/>
            </Policies>
        </RollingRandomAccessFile>
    </Appenders>


    <Loggers>
        <Logger name="com.davidwang456.mybatis.logging" level="debug" additivity="true">
            <appender-ref ref="fileLogger" level="debug"/>
        </Logger>

        <Root level="info" additivity="false">
            <appender-ref ref="console"/>
        </Root>
    </Loggers>
</Configuration>
```

在src/main/resources目录下，定义**mapper.xml文件。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
        PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.logging.StudentMapper">
	<!--  <logging/> -->
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.logging.StudentQueryDTO" 
	resultType="com.davidwang456.mybatis.logging.StudentDTO">
	<bind name="first" value="'%'+firstName+'%'"/>
	<bind name="last"  value="'%'+lastName+'%'"/>
		select id,
			   first_name ,
			   last_name ,
			   age
			   from student
			   where 1=1 
			   <if test="id!=null">
			   and id=#{id}
			   </if>
			   <if test="firstName!=null and firstName!=''">
			   	and first_name like  #{first}
			   </if>
			   <if test="lastName!=null and lastName!=''">
			   	and last_name like #{last}
			   </if>			   
			  <if test="age!=null and age!=0">
			   and age=#{age}
			   </if>	
			   order by ${sort} ${orderBy}		   		   		  				  
	</select>
</mapper>
```

**Mapper文件**

```java
package com.davidwang456.mybatis.logging;

import java.util.List;

public interface StudentMapper {
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
}
```

#### 测试程序

```java
package com.davidwang456.mybatis.logging;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class LoggingTest {

	public static void main(String[] args) throws IOException {
		testLogging();
	   }
	
	private static void testLogging() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();	
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("wang");
	      param.setLastName("david");
	      param.setOrderBy("DESC");
	      param.setSort("age");	      
	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition(param);	      
	      printResult(stus,"getStudentInfoByCondition query");
	      session.commit(true);
	      session.close();
	}
	
	private static void printResult(List<StudentDTO> stus,String name) {
		System.out.println("------------------"+name+"------------start-----------");
		for(StudentDTO dto:stus) {
			System.out.println(dto.toString());
		}		
		System.out.println("------------------"+name+"------------end----------");
	}
}
```

打印程序如下：

```tex
20xx/xx/xx 09:42:24.051 [DEBUG] main com.davidwang456.mybatis.logging.StudentMapper.getStudentInfoByCondition ==>  Preparing: select id, first_name , last_name , age from student where 1=1 and first_name like ? and last_name like ? order by age DESC
2021/xx/xx 09:42:24.081 [DEBUG] main com.davidwang456.mybatis.logging.StudentMapper.getStudentInfoByCondition ==> Parameters: %wang%(String), %david%(String)
20xx/xx/xx 09:42:24.112 [DEBUG] main com.davidwang456.mybatis.logging.StudentMapper.getStudentInfoByCondition <==      Total: 8
Logging initialized using 'class org.apache.ibatis.logging.stdout.StdOutImpl' adapter.
------------------getStudentInfoByCondition query------------start-----------
student [id=8, firstName=wang8, lastName=david8, age=28]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=1, firstName=wang1, lastName=david1, age=21]
------------------getStudentInfoByCondition query------------end----------
```

此时，在D:\tmp\log也出现了一个mybatis-sql.log文件，打印出请求日志查询。

### 日志可以混用吗？答案是可以的!

我们修改一下测试程序，使用另外的日志实现打印出查询结果：

```java
	private static void printResult(List<StudentDTO> stus,String name) {
		LogFactory.useStdOutLogging();
		Log log= LogFactory.getLog(org.apache.ibatis.logging.stdout.StdOutImpl.class);
		log.debug("------------------"+name+"------------start-----------");
		for(StudentDTO dto:stus) {
			log.debug(dto.toString());
		}		
		log.debug("------------------"+name+"------------end----------");
	}
```

测试，打印结果也符合我们的预期。

```tex
20xx/xx/xx 09:47:32.730 [DEBUG] main com.davidwang456.mybatis.logging.StudentMapper.getStudentInfoByCondition ==>  Preparing: select id, first_name , last_name , age from student where 1=1 and first_name like ? and last_name like ? order by age DESC
20xx/xx/xx 09:47:32.759 [DEBUG] main com.davidwang456.mybatis.logging.StudentMapper.getStudentInfoByCondition ==> Parameters: %wang%(String), %david%(String)
20xx/xx/xx 09:47:32.780 [DEBUG] main com.davidwang456.mybatis.logging.StudentMapper.getStudentInfoByCondition <==      Total: 8
Logging initialized using 'class org.apache.ibatis.logging.stdout.StdOutImpl' adapter.
------------------getStudentInfoByCondition query------------start-----------
student [id=8, firstName=wang8, lastName=david8, age=28]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=1, firstName=wang1, lastName=david1, age=21]
------------------getStudentInfoByCondition query------------end----------
```

## 深入Mybatis日志内部原理

**Mybatis日志门面模式**

mybatis日志定义了一个日志门面LogFactory,定义了六种类型的日志实现，在需要使用时调用getLog方法：

```java
  static {
    tryImplementation(LogFactory::useSlf4jLogging);
    tryImplementation(LogFactory::useCommonsLogging);
    tryImplementation(LogFactory::useLog4J2Logging);
    tryImplementation(LogFactory::useLog4JLogging);
    tryImplementation(LogFactory::useJdkLogging);
    tryImplementation(LogFactory::useNoLogging);
  }
```

具体使用哪个，可以在Settings中配置

```xml
		<setting name="logImpl" value="LOG4J2"/>
```

其value值的定义在typeAliasRegistry中：

```java
    typeAliasRegistry.registerAlias("SLF4J", Slf4jImpl.class);
    typeAliasRegistry.registerAlias("COMMONS_LOGGING", JakartaCommonsLoggingImpl.class);
    typeAliasRegistry.registerAlias("LOG4J", Log4jImpl.class);
    typeAliasRegistry.registerAlias("LOG4J2", Log4j2Impl.class);
    typeAliasRegistry.registerAlias("JDK_LOGGING", Jdk14LoggingImpl.class);
    typeAliasRegistry.registerAlias("STDOUT_LOGGING", StdOutImpl.class);
    typeAliasRegistry.registerAlias("NO_LOGGING", NoLoggingImpl.class);
```

**初始化日志实现**

Mybatis的日志输出可以输出数据库连接的获取、释放信息；

可以输出执行的SQL语句及其传递的预编译参数信息；

可以输出查询结果集信息（需要是trace输出级别，其它的是DEBUG级别）；

Mybatis的日志输出实现是通过JDK的动态代理来实现的:

- 针对Connection的日志输出提供了ConnectionLogger；

- 针对Statement提供了StatementLogger；

- 针对PreparedStatement提供了PreparedStatementLogger；

- 针对ResultSet提供了ResultSetLogger，

这些Logger类都是实现了JDK的InvocationHandler类的。

```java
public final class ConnectionLogger extends BaseJdbcLogger implements InvocationHandler {

  private final Connection connection;

  private ConnectionLogger(Connection conn, Log statementLog, int queryStack) {
    super(statementLog, queryStack);
    this.connection = conn;
  }

  @Override
  public Object invoke(Object proxy, Method method, Object[] params)
      throws Throwable {
    try {
      if (Object.class.equals(method.getDeclaringClass())) {
        return method.invoke(this, params);
      }
      if ("prepareStatement".equals(method.getName()) || "prepareCall".equals(method.getName())) {
        if (isDebugEnabled()) {
          debug(" Preparing: " + removeExtraWhitespace((String) params[0]), true);
        }
        PreparedStatement stmt = (PreparedStatement) method.invoke(connection, params);
        stmt = PreparedStatementLogger.newInstance(stmt, statementLog, queryStack);
        return stmt;
      } else if ("createStatement".equals(method.getName())) {
        Statement stmt = (Statement) method.invoke(connection, params);
        stmt = StatementLogger.newInstance(stmt, statementLog, queryStack);
        return stmt;
      } else {
        return method.invoke(connection, params);
      }
    } catch (Throwable t) {
      throw ExceptionUtil.unwrapThrowable(t);
    }
  }

  /**
   * Creates a logging version of a connection.
   *
   * @param conn
   *          the original connection
   * @param statementLog
   *          the statement log
   * @param queryStack
   *          the query stack
   * @return the connection with logging
   */
  public static Connection newInstance(Connection conn, Log statementLog, int queryStack) {
    InvocationHandler handler = new ConnectionLogger(conn, statementLog, queryStack);
    ClassLoader cl = Connection.class.getClassLoader();
    return (Connection) Proxy.newProxyInstance(cl, new Class[]{Connection.class}, handler);
  }

  /**
   * return the wrapped connection.
   *
   * @return the connection
   */
  public Connection getConnection() {
    return connection;
  }

}
```

PreparedStatementLogger，StatementLogger，ResultSetLogger实现类型。

**总结**

在程序开发过程中，为了调试方便、了解程序的运行过程，进行必要的日志输出总是免不了的。对于使用Mybatis而言，我们常见的需求是希望可以在日志中打印出Mybatis执行过程中进行数据库操作的SQL语句及其传递的参数。Mybatis的日志输出是统一管理的，它有自己的日志接口，然后在需要进行日志输出的时候使用统一的API进行日志输出。这个统一的接口是org.apache.ibatis.logging.Log。Mybatis分别基于常用的日志输出工具给出了对应的实现，比如LOG4J、SLF4J等。默认情况下Mybatis的org.apache.ibatis.logging.LogFactory会按照以下顺序依次判断当前程序下可以使用哪种日志实现，直到找到为止，如果一个实现都没有那就是最后的noLogging了，将采用NoLoggingImpl实现。

- SLF4J
- Apache Commons Logging
- Log4j2
- Log4j
- JDK logging
- NoLogging

除了上面提到的几个日志实现类，还有打印sql的日志。

ConnectionLogger，PreparedStatementLogger，StatementLogger，ResultSetLogger实现类型。

注意：如果你的应用部署在一个包含Commons Logging的环境， 而你又想用其他的日志框架，你可以根据需要调用如下的某一方法：

org.apache.ibatis.logging.LogFactory.useSlf4jLogging();

 org.apache.ibatis.logging.LogFactory.useLog4JLogging();

 org.apache.ibatis.logging.LogFactory.useJdkLogging();

 org.apache.ibatis.logging.LogFactory.useCommonsLogging();

 org.apache.ibatis.logging.LogFactory.useStdOutLogging();

