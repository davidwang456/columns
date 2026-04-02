# Mybatis是否支持注解？如果支持，如何实现注解的？

## 背景

> 小白：师傅，我看spring中大量使用了注解，mybatis为什么还是使用过时的xml文件配置方式呢？能不能使用注解的形式开发？
>
> 扫地僧：因为最初设计时，MyBatis是一个XML驱动的框架。配置信息是基于XML的，而且映射语句也是定义在XML中的。而到了MyBatis3，有新的选择了：利用注解实现SQL的映射。MyBatis3构建在全面而且强大的Java 注解（Java annotation）之上。注解提供了一种便捷的方式来实现简单SQL映射语句，可以简化编写XML的过程。MyBatis基于注解的用法，正在变得越来越流行！MyBatis基于注解的用法，正在变得越来越流行，但是需要注意的是：注解的方式还没有百分百覆盖所有XML标签，所以还是有一点点不足！口说无凭，代码撸起来！

## Mybatis注解示例

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

#### 添加依赖

pom.xml

```
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>AnotationTest</artifactId>
  <version>3.4.0-SNAPSHOT</version>
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
   </dependencies>
</project>
```

#### 实体

**数据库实体**

```java
package com.davidwang456.mybatis.annotation;

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

#### 配置

**数据库配置**

```java
<?xml version = "1.0" encoding = "UTF-8"?>
<!DOCTYPE configuration PUBLIC "-//mybatis.org//DTD Config 3.0//EN" "http://mybatis.org/dtd/mybatis-3-config.dtd">
<configuration>
	<settings>
		<setting name="logImpl" value="STDOUT_LOGGING"/>
		<setting name="mapUnderscoreToCamelCase" value="true"/>
   </settings>
   <typeAliases>
     <typeAlias type="com.davidwang456.mybatis.typealias.StudentDTO" alias="StudentDTO"/>
     <typeAlias type="com.davidwang456.mybatis.typealias.StudentQueryDTO" alias="StudentQueryDTO"/>
   </typeAliases>
   
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

定义了全局的类型别名，在其它的**mapper.xml可以使用这个别名。

**Mapper文件**

```java
package com.davidwang456.mybatis.annotation;

import org.apache.ibatis.annotations.Select;

public interface StudentMapper {
	@Select("select id,first_name,last_name,age from student where id=#{id}")
	public StudentDTO getStudentInfoById(Integer id);

}
```

#### 测试程序

**简单注解示例1**

```java
package com.davidwang456.mybatis.annotation;

import java.io.IOException;
import java.io.Reader;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.Configuration;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class AnotationTest {

	public static void main(String[] args) throws IOException {
		getStudentInfoById();
	   }
	
	
	private static void getStudentInfoById() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		 
	      Configuration conf=session.getConfiguration();
	      conf.addMapper(StudentMapper.class);
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO stu=studentMapper.getStudentInfoById(3);
	      System.out.println(stu.toString());

	      session.commit(true);
	      session.close();
	}
}
```

测试结果，符合预期！

```tex
==>  Preparing: select id,first_name,last_name,age from student where id=?
==> Parameters: 3(Integer)
<==    Columns: id, first_name, last_name, age
<==        Row: 3, wang3, david3, 23
<==      Total: 1
student [id=3, firstName=wang3, lastName=david3, age=23]
```

**简单注解示例2**

mybatis使用注解替代xml配置时，遇到判断条件是否为null或者为空时，@Select很难搞定，不知道怎么办？

mybatis3中增加了使用注解来配置Mapper的新特性，使用 SelectProvider来动态生成sql。

StudentMapper新增SelectProvider注解，实现上述功能：

```java
package com.davidwang456.mybatis.annotation;

import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.SelectProvider;

public interface StudentMapper {
	@Select("select id,first_name,last_name,age from student where id=#{id}")
	public StudentDTO getStudentInfoById(Integer id);
	@SelectProvider(type = StudentInfoProvider.class, method = "getStudentById")
	public StudentDTO getStudentById1(Integer id);
}
```

其中StudentInfoProvider提供了sql的构建：

```java
package com.davidwang456.mybatis.annotation;

public class StudentInfoProvider {
	public String getStudentById(Integer id) {
		StringBuffer sbf=new StringBuffer();
		sbf.append("select ")
		.append("id,first_name,last_name,age")
		.append(" from student")
		.append(" where id= "+id);
		return sbf.toString();
	}
}
```

打印的结果和上面的一致。

```tex
==>  Preparing: select id,first_name,last_name,age from student where id= 3
==> Parameters: 
<==    Columns: id, first_name, last_name, age
<==        Row: 3, wang3, david3, 23
<==      Total: 1
student [id=3, firstName=wang3, lastName=david3, age=23]
```

**简单注解示例3**

Java 程序员面对的最痛苦的事情之一就是在 Java 代码中嵌入 SQL 语句。MyBatis 3 提供了方便的工具类来帮助解决此问题。借助 SQL 类，我们只需要简单地创建一个实例，并调用它的方法即可生成 SQL 语句。让我们来用 SQL 类重写上面的例子：

```java
package com.davidwang456.mybatis.annotation;

import org.apache.ibatis.jdbc.SQL;

public class StudentInfoProvider {
	public String getStudentById(Integer id) {
		return new SQL() {
			{
				SELECT("id,first_name,last_name,age");
				FROM("student");
				WHERE("id = "+id);
			}
		}.toString();
	}
}
```

**多个参数注解示例4**

在超过一个参数的情况下，@SelectProvide方法必须接受Map<String, Object>做为参数：

```java
package com.davidwang456.mybatis.annotation;

import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.SelectProvider;

public interface StudentMapper {
	@Select("select id,first_name,last_name,age from student where id=#{id}")
	public StudentDTO getStudentInfoById(Integer id);
	@SelectProvider(type = StudentInfoProvider.class, method = "getStudentById")
	public StudentDTO getStudentById1(Integer id);
	
	@SelectProvider(type = StudentInfoProvider.class, method = "getStudentByCondition")
	public StudentDTO getStudentByIdCondition(@Param("id")Integer id,@Param("firstName")String firstName,
			@Param("lastName")String lastName,@Param("age")Integer age);
}
```

其中StudentInfoProvider

```java
	public String getStudentByCondition(Map<String,Object> params) {
		StringBuffer sbf=new StringBuffer();
		sbf.append("select id, first_name,last_name,age from student where 1=1");
		if(params.get("id")!=null) {
			sbf.append(" and id="+(int)params.get("id"));
		}
		if(params.get("firstName")!=null) {
			sbf.append(" and first_name= '"+params.get("firstName").toString()+"'");
		}
		if(params.get("lastName")!=null) {
			sbf.append(" and last_name= '"+params.get("lastName").toString()+"'");
		}
		if(params.get("age")!=null) {
			sbf.append(" and age="+(int)params.get("age"));
		}
		
		return sbf.toString();
	}
```

测试程序

```java
	private static void getStudentInfoByCondition() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		 
	      Configuration conf=session.getConfiguration();
	      conf.addMapper(StudentMapper.class);
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO stu=studentMapper.getStudentByIdCondition(3, "wang3", "david3", 23);
	      System.out.println(stu.toString());

	      session.commit(true);
	      session.close();
	}
```

打印结果如下：

```tex
==>  Preparing: select id, first_name,last_name,age from student where 1=1 and id=3 and first_name= 'wang3' and last_name= 'david3' and age=23
==> Parameters: 
<==    Columns: id, first_name, last_name, age
<==        Row: 3, wang3, david3, 23
<==      Total: 1
student [id=3, firstName=wang3, lastName=david3, age=23]
Committing JDBC Connection [com.mysql.cj.jdbc.ConnectionImpl@b9afc07]
```

## Mybatis注解原理

在MapperAnnotationBuilder定义了注解的类型：

```java
  private static final Set<Class<? extends Annotation>> statementAnnotationTypes = Stream
      .of(Select.class, Update.class, Insert.class, Delete.class, SelectProvider.class, UpdateProvider.class,
          InsertProvider.class, DeleteProvider.class)
      .collect(Collectors.toSet());
```

主要包括：@Select，@SelectProvider用于查询；@Update，@UpdateProvider用于更新；@Insert，@InsertProvider用于插入；@Delete，@DeleteProvider用户删除。

注解的解析过程：

第一步主程序调用MapperRegistry#addMapper()方法；

第二步MapperRegistry调用MapperAnnotationBuilder#parse()方法；

第三步调用MapperAnnotationBuilder#parseStatement()方法进行解析

第四步调用MapperBuilderAssistant#addMappedStatement()方法做sql组装

```java
  public MappedStatement addMappedStatement(
      String id,
      SqlSource sqlSource,
      StatementType statementType,
      SqlCommandType sqlCommandType,
      Integer fetchSize,
      Integer timeout,
      String parameterMap,
      Class<?> parameterType,
      String resultMap,
      Class<?> resultType,
      ResultSetType resultSetType,
      boolean flushCache,
      boolean useCache,
      boolean resultOrdered,
      KeyGenerator keyGenerator,
      String keyProperty,
      String keyColumn,
      String databaseId,
      LanguageDriver lang,
      String resultSets) {

    if (unresolvedCacheRef) {
      throw new IncompleteElementException("Cache-ref not yet resolved");
    }

    id = applyCurrentNamespace(id, false);
    boolean isSelect = sqlCommandType == SqlCommandType.SELECT;

    MappedStatement.Builder statementBuilder = new MappedStatement.Builder(configuration, id, sqlSource, sqlCommandType)
        .resource(resource)
        .fetchSize(fetchSize)
        .timeout(timeout)
        .statementType(statementType)
        .keyGenerator(keyGenerator)
        .keyProperty(keyProperty)
        .keyColumn(keyColumn)
        .databaseId(databaseId)
        .lang(lang)
        .resultOrdered(resultOrdered)
        .resultSets(resultSets)
        .resultMaps(getStatementResultMaps(resultMap, resultType, id))
        .resultSetType(resultSetType)
        .flushCacheRequired(valueOrDefault(flushCache, !isSelect))
        .useCache(valueOrDefault(useCache, isSelect))
        .cache(currentCache);

    ParameterMap statementParameterMap = getStatementParameterMap(parameterMap, parameterType, id);
    if (statementParameterMap != null) {
      statementBuilder.parameterMap(statementParameterMap);
    }

    MappedStatement statement = statementBuilder.build();
    configuration.addMappedStatement(statement);
    return statement;
  }
```

最终调用和xml一样，调用MappedStatement#build()方法。

## 拓展：sql注入问题

有人可能会担心，使用java拼写sql，是不是容易造成sql注入，其实大家不用担心，mybatis在使用注解组装sql时，如果不是特意制定，默认使用的是Statement类型是PREPARED：MapperAnnotationBuilder#parseStatement()方法

```JAVA
 void parseStatement(Method method) {
    final Class<?> parameterTypeClass = getParameterType(method);
    final LanguageDriver languageDriver = getLanguageDriver(method);

    getAnnotationWrapper(method, true, statementAnnotationTypes).ifPresent(statementAnnotation -> {
      final SqlSource sqlSource = buildSqlSource(statementAnnotation.getAnnotation(), parameterTypeClass, languageDriver, method);
      final SqlCommandType sqlCommandType = statementAnnotation.getSqlCommandType();
      final Options options = getAnnotationWrapper(method, false, Options.class).map(x -> (Options)x.getAnnotation()).orElse(null);
      final String mappedStatementId = type.getName() + "." + method.getName();

      final KeyGenerator keyGenerator;
      String keyProperty = null;
      String keyColumn = null;
      if (SqlCommandType.INSERT.equals(sqlCommandType) || SqlCommandType.UPDATE.equals(sqlCommandType)) {
        // first check for SelectKey annotation - that overrides everything else
        SelectKey selectKey = getAnnotationWrapper(method, false, SelectKey.class).map(x -> (SelectKey)x.getAnnotation()).orElse(null);
        if (selectKey != null) {
          keyGenerator = handleSelectKeyAnnotation(selectKey, mappedStatementId, getParameterType(method), languageDriver);
          keyProperty = selectKey.keyProperty();
        } else if (options == null) {
          keyGenerator = configuration.isUseGeneratedKeys() ? Jdbc3KeyGenerator.INSTANCE : NoKeyGenerator.INSTANCE;
        } else {
          keyGenerator = options.useGeneratedKeys() ? Jdbc3KeyGenerator.INSTANCE : NoKeyGenerator.INSTANCE;
          keyProperty = options.keyProperty();
          keyColumn = options.keyColumn();
        }
      } else {
        keyGenerator = NoKeyGenerator.INSTANCE;
      }

      Integer fetchSize = null;
      Integer timeout = null;
      //默认Statement类型为prepared即PreparedStatement，这种方式是先编译后运行，故不会产生sql注入问题
      StatementType statementType = StatementType.PREPARED;
      ResultSetType resultSetType = configuration.getDefaultResultSetType();
      boolean isSelect = sqlCommandType == SqlCommandType.SELECT;
      boolean flushCache = !isSelect;
      boolean useCache = isSelect;
      if (options != null) {
        if (FlushCachePolicy.TRUE.equals(options.flushCache())) {
          flushCache = true;
        } else if (FlushCachePolicy.FALSE.equals(options.flushCache())) {
          flushCache = false;
        }
        useCache = options.useCache();
        fetchSize = options.fetchSize() > -1 || options.fetchSize() == Integer.MIN_VALUE ? options.fetchSize() : null; //issue #348
        timeout = options.timeout() > -1 ? options.timeout() : null;
        statementType = options.statementType();
        if (options.resultSetType() != ResultSetType.DEFAULT) {
          resultSetType = options.resultSetType();
        }
      }

      String resultMapId = null;
      if (isSelect) {
        ResultMap resultMapAnnotation = method.getAnnotation(ResultMap.class);
        if (resultMapAnnotation != null) {
          resultMapId = String.join(",", resultMapAnnotation.value());
        } else {
          resultMapId = generateResultMapName(method);
        }
      }

      assistant.addMappedStatement(
          mappedStatementId,
          sqlSource,
          statementType,
          sqlCommandType,
          fetchSize,
          timeout,
          // ParameterMapID
          null,
          parameterTypeClass,
          resultMapId,
          getReturnType(method),
          resultSetType,
          flushCache,
          useCache,
          // TODO gcode issue #577
          false,
          keyGenerator,
          keyProperty,
          keyColumn,
          statementAnnotation.getDatabaseId(),
          languageDriver,
          // ResultSets
          options != null ? nullOrEmpty(options.resultSets()) : null);
    });
  }
```



## 总结

设计初期的 MyBatis 是一个 XML 驱动的框架。配置信息是基于 XML 的，映射语句也是定义在 XML 中的。而在 MyBatis 3 中，我们提供了其它的配置方式。MyBatis 3 构建在全面且强大的基于 Java 语言的配置 API 之上。它是 XML 和注解配置的基础。注解提供了一种简单且低成本的方式来实现简单的映射语句。

Mybatis编写sql有两种方式，即通过xml和注解，并且Mybatis中xml优先于注解加载，也就是如果DAO接口中的方法有对应的xml配置，再加入注解会抛异常，如果两个都没配置，在调用DAO方法时再抛异常。

提示：mybatis官方目前仍然推荐使用xml方式。

> **提示** 不幸的是，Java 注解的表达能力和灵活性十分有限。尽管我们花了很多时间在调查、设计和试验上，但最强大的 MyBatis 映射并不能用注解来构建——我们真没开玩笑。而 C# 属性就没有这些限制，因此 MyBatis.NET 的配置会比 XML 有更大的选择余地。虽说如此，基于 Java 注解的配置还是有它的好处的。















