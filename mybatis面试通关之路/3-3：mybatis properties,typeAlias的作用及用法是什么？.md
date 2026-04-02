# mybatis properties,typeAlias及标签bind的作用及用法是什么？

## 背景

> 小白：师傅，mybatis的配置文件有一点不明白，我看您配置parameterType和resultType时使用很多的名称但有效，而我必须把包名+类名的全类名称加上才能生效，这是什么原因呢？
>
> 扫地僧：这个问题很有意思！让我想到高级程序员圈内的一句调侃：判断程序员是不是菜鸟只要看mybatis配置文件的行的长短就可以了。
>
> 小白：为什么根据配置文件行的长短可以判断程序员是不是菜鸟呢？快教教我！
>
> 扫地僧：在mysql中，是不是有“select aa1 as 列别名 from 表名”的查询方式？同样，mybatis为了方便大家，提供了一个**typeAlias**属性来给一个常用的类起个别名，你可以使用对parameterType和resultType中常用的类型，起一个简短的名称，然后可以在其它地方使用。眼见为实，我们先看一个例子吧。

## typeAlias实例

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
  <artifactId>TypeAliasTest</artifactId>
  <version>2.5.0-SNAPSHOT</version>
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
package com.davidwang456.mybatis.typealias;

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
package com.davidwang456.mybatis.typealias;

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

**Mybatis映射文件配置**

```java
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
        PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.typealias.StudentMapper">
	<select id="getStudentInfoByCondition" parameterType="StudentQueryDTO" 
	resultType="StudentDTO" useCache="false">
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
			   	and first_name like concat(#{firstName},"%")
			   </if>
			   <if test="lastName!=null and lastName!=''">
			   	and last_name like concat(#{lastName},"%")
			   </if>		   
			  <if test="age!=null and age!=0">
			   and age=#{age}
			   </if>	
			   order by ${sort} ${orderBy}			   		   		  				  
	</select>
</mapper>
```

使用上文定义的类型别名。

**Mapper文件**

```java
package com.davidwang456.mybatis.typealias;

import java.util.List;

public interface StudentMapper {
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);

}
```

#### 测试程序

**别名测试1**

```java
package com.davidwang456.mybatis.typealias;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class TypeAliasTest {

	public static void main(String[] args) throws IOException {
		testTypeAlias();
	   }
	
	private static void testTypeAlias() throws IOException {
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
	      for(StudentDTO stu: stus) {
	    	  System.out.println(stu.toString());
	      }
	      session.commit(true);
	      session.close();
	}
}
```

打印出结果如下：

```tex
==>  Preparing: select id, first_name , last_name , age from student where 1=1 and first_name like concat(?,"%") and last_name like concat(?,"%") order by age DESC
==> Parameters: wang(String), david(String)
<==    Columns: id, first_name, last_name, age
<==        Row: 8, wang8, david8, 28
<==        Row: 7, wang7, david7, 27
<==        Row: 6, wang6, david6, 26
<==        Row: 5, wang5, david5, 25
<==        Row: 4, wang4, david4, 24
<==        Row: 3, wang3, david3, 23
<==        Row: 2, wang2, david2, 22
<==        Row: 1, wang1, david1, 21
<==      Total: 8
student [id=8, firstName=wang8, lastName=david8, age=28]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=1, firstName=wang1, lastName=david1, age=21]
```

打印结果符合预期，说明类型别名起作用了！
**别名测试2**
大家知道，在全类名称时大小写只要拼错一个，就会报错。如果使用别名，拼写错了大小写会怎么样呢？try！

```java
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
        PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.typealias.StudentMapper">
	<select id="getStudentInfoByCondition" parameterType="studentquerydto" 
	resultType="studentdto" useCache="false">
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
			   	and first_name like concat(#{firstName},"%")
			   </if>
			   <if test="lastName!=null and lastName!=''">
			   	and last_name like concat(#{lastName},"%")
			   </if>		   
			  <if test="age!=null and age!=0">
			   and age=#{age}
			   </if>	
			   order by ${sort} ${orderBy}			   		   		  				  
	</select>
</mapper>
```

打印结果和上面一致，并没有报错，试了大写或者大小写混搭都能正确查询出结果。可以得出结论：使用别名时对大小写是不敏感的！

**别名测试3**

对我们常用的类型，如Integer，Float，String等，Mybatis都提供了别名：

映射文件StudentMapper.xml添加一个使用id查询配置

```xml
	<select id="getStudentInfoById" parameterType="int" resultType="studentdto" useCache="false">
		select id,
			   first_name ,
			   last_name ,
			   age
			   from student
			   where id=#{id} 
	</select>
```

你没有看错，parameterType为什么是int？请继续往下看！

映射类StudentMapper.java添加定义

```java
public StudentDTO getStudentInfoById(Integer id);
```

测试类

```java
	private static void testTypeAlias4Integer() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO stu=studentMapper.getStudentInfoById(3);
	      System.out.println(stu.toString());

	      session.commit(true);
	      session.close();
	}
```

运行测试类，打印测试结果：

```tex
==>  Preparing: select id, first_name , last_name , age from student where id=?
==> Parameters: 3(Integer)
<==    Columns: id, first_name, last_name, age
<==        Row: 3, wang3, david3, 23
<==      Total: 1
student [id=3, firstName=wang3, lastName=david3, age=23]
```

结果符合预期！测试paramterType使用不同大小写的int 都可以正常打印出结果，结论：int是Java.lang.Integer的别名。

## TypeAlias原理

Mybatis里有一个TypeAliasRegistry，它提供了类型的注册和解读。

### 获取所有注册类型及别名

```java
	private static void getTypeAlias4All() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      Map<String, Class<?>> alias=session.getConfiguration().getTypeAliasRegistry().getTypeAliases();
	      for(String name:alias.keySet()) {
	    	  System.out.println("alias name:"+name+",alias type:"+alias.get(name).getCanonicalName());
	      }

	      session.commit(true);
	      session.close();
	}
```

上面使用public Map<String, Class<?>> getTypeAliases();方法获取到所有的别名类型。

```tex
alias name:date,alias type:java.util.Date
alias name:_boolean,alias type:boolean
alias name:cglib,alias type:org.apache.ibatis.executor.loader.cglib.CglibProxyFactory
alias name:_byte[],alias type:byte[]
alias name:_int[],alias type:int[]
alias name:object[],alias type:java.lang.Object[]
alias name:decimal[],alias type:java.math.BigDecimal[]
alias name:integer,alias type:java.lang.Integer
alias name:float,alias type:java.lang.Float
alias name:perpetual,alias type:org.apache.ibatis.cache.impl.PerpetualCache
alias name:_byte,alias type:byte
alias name:iterator,alias type:java.util.Iterator
alias name:biginteger[],alias type:java.math.BigInteger[]
alias name:xml,alias type:org.apache.ibatis.scripting.xmltags.XMLLanguageDriver
alias name:_double,alias type:double
alias name:_int,alias type:int
alias name:hashmap,alias type:java.util.HashMap
alias name:_float[],alias type:float[]
alias name:soft,alias type:org.apache.ibatis.cache.decorators.SoftCache
alias name:javassist,alias type:org.apache.ibatis.executor.loader.javassist.JavassistProxyFactory
alias name:date[],alias type:java.util.Date[]
alias name:bigdecimal[],alias type:java.math.BigDecimal[]
alias name:slf4j,alias type:org.apache.ibatis.logging.slf4j.Slf4jImpl
alias name:byte,alias type:java.lang.Byte
alias name:double,alias type:java.lang.Double
alias name:resultset,alias type:java.sql.ResultSet
alias name:raw,alias type:org.apache.ibatis.scripting.defaults.RawLanguageDriver
alias name:collection,alias type:java.util.Collection
alias name:list,alias type:java.util.List
alias name:lru,alias type:org.apache.ibatis.cache.decorators.LruCache
alias name:_float,alias type:float
alias name:studentdto,alias type:com.davidwang456.mybatis.typealias.StudentDTO
alias name:_long,alias type:long
alias name:_integer,alias type:int
alias name:_integer[],alias type:int[]
alias name:boolean[],alias type:java.lang.Boolean[]
alias name:decimal,alias type:java.math.BigDecimal
alias name:_double[],alias type:double[]
alias name:object,alias type:java.lang.Object
alias name:biginteger,alias type:java.math.BigInteger
alias name:string,alias type:java.lang.String
alias name:long[],alias type:java.lang.Long[]
alias name:jdbc,alias type:org.apache.ibatis.transaction.jdbc.JdbcTransactionFactory
alias name:studentquerydto,alias type:com.davidwang456.mybatis.typealias.StudentQueryDTO
alias name:long,alias type:java.lang.Long
alias name:weak,alias type:org.apache.ibatis.cache.decorators.WeakCache
alias name:no_logging,alias type:org.apache.ibatis.logging.nologging.NoLoggingImpl
alias name:unpooled,alias type:org.apache.ibatis.datasource.unpooled.UnpooledDataSourceFactory
alias name:pooled,alias type:org.apache.ibatis.datasource.pooled.PooledDataSourceFactory
alias name:db_vendor,alias type:org.apache.ibatis.mapping.VendorDatabaseIdProvider
alias name:managed,alias type:org.apache.ibatis.transaction.managed.ManagedTransactionFactory
alias name:commons_logging,alias type:org.apache.ibatis.logging.commons.JakartaCommonsLoggingImpl
alias name:_short[],alias type:short[]
alias name:_short,alias type:short
alias name:map,alias type:java.util.Map
alias name:log4j,alias type:org.apache.ibatis.logging.log4j.Log4jImpl
alias name:jdk_logging,alias type:org.apache.ibatis.logging.jdk14.Jdk14LoggingImpl
alias name:fifo,alias type:org.apache.ibatis.cache.decorators.FifoCache
alias name:bigdecimal,alias type:java.math.BigDecimal
alias name:short[],alias type:java.lang.Short[]
alias name:int[],alias type:java.lang.Integer[]
alias name:arraylist,alias type:java.util.ArrayList
alias name:int,alias type:java.lang.Integer
alias name:float[],alias type:java.lang.Float[]
alias name:log4j2,alias type:org.apache.ibatis.logging.log4j2.Log4j2Impl
alias name:byte[],alias type:java.lang.Byte[]
alias name:boolean,alias type:java.lang.Boolean
alias name:stdout_logging,alias type:org.apache.ibatis.logging.stdout.StdOutImpl
alias name:double[],alias type:java.lang.Double[]
alias name:_long[],alias type:long[]
alias name:jndi,alias type:org.apache.ibatis.datasource.jndi.JndiDataSourceFactory
alias name:short,alias type:java.lang.Short
alias name:_boolean[],alias type:boolean[]
alias name:integer[],alias type:java.lang.Integer[]
```

### 别名不区分大小写

TypeAliasRegistry注册别名时会将别名转化为小写，存储到map中；

```java
  public void registerAlias(String alias, Class<?> value) {
    if (alias == null) {
      throw new TypeException("The parameter alias cannot be null");
    }
    // issue #748
    String key = alias.toLowerCase(Locale.ENGLISH);
    if (typeAliases.containsKey(key) && typeAliases.get(key) != null && !typeAliases.get(key).equals(value)) {
      throw new TypeException("The alias '" + alias + "' is already mapped to the value '" + typeAliases.get(key).getName() + "'.");
    }
    typeAliases.put(key, value);
  }
```

解析别名时，先将别名转换为小写，然后获取对应的类型。

```java
  @SuppressWarnings("unchecked")
  // throws class cast exception as well if types cannot be assigned
  public <T> Class<T> resolveAlias(String string) {
    try {
      if (string == null) {
        return null;
      }
      // issue #748
      String key = string.toLowerCase(Locale.ENGLISH);
      Class<T> value;
      if (typeAliases.containsKey(key)) {
        value = (Class<T>) typeAliases.get(key);
      } else {
        value = (Class<T>) Resources.classForName(string);
      }
      return value;
    } catch (ClassNotFoundException e) {
      throw new TypeException("Could not resolve type alias '" + string + "'.  Cause: " + e, e);
    }
  }
```

因此，在配置文件定义的别名，是不区分大小写的，提供了程序的容错性。

### 批量注册别名

mybatis也提供了批量注册别名的方法：public void registerAliases(String packageName, Class<?> superType)；

示例如下：

```xml
<!-- 批量定义别名 -->
<typeAliases>
    <package name="com.davidwang456.mybatis.typealias.dto"/>
</typeAliases>
```

## 属性（properties）

属性可以在外部进行配置，并可以进行动态替换。你既可以在典型的 Java 属性文件中配置这些属性，也可以在 properties 元素的子元素中设置。例如：

属性配置:

```xml
<properties resource="org/mybatis/example/config.properties">
  <property name="username" value="dev_user"/>
  <property name="password" value="F2Fa3!33TYyg"/>
</properties>
```

属性使用：

```xml
<dataSource type="POOLED">
  <property name="driver" value="${driver}"/>
  <property name="url" value="${url}"/>
  <property name="username" value="${username}"/>
  <property name="password" value="${password}"/>
</dataSource>
```

这个例子中的 username 和 password 将会由 properties 元素中设置的相应值来替换。 driver 和 url 属性将会由 config.properties 文件中对应的值来替换。这样就为配置提供了诸多灵活选择。

注意：如果一个属性在不只一个地方进行了配置，那么，MyBatis 将按照下面的顺序来加载：

- 首先读取在 properties 元素体内指定的属性。
- 然后根据 properties 元素中的 resource 属性读取类路径下属性文件，或根据 url 属性指定的路径读取属性文件，并覆盖之前读取过的同名属性。
- 最后读取作为方法参数传递的属性，并覆盖之前读取过的同名属性。

因此，通过方法参数传递的属性具有最高优先级，resource/url 属性中指定的配置文件次之，最低优先级的则是 properties 元素中指定的属性。

## 总结

**1、**在MyBatis的配置文件中可以使用typeAliases标签设置实体类的别名设置，设置别名之后可以在SQL映射文件中返回值和参数使用，并且不区分大小写；可以使用properties标签配置数据库的连接信息或者其它外部信息。

**2**、typeAliases有两种配置封装类别名的方式：
（1）typeAlias标签单个配置封装实体类的别名；
（2）package标签配置某一个包下所有实体类的别名(此种方式默认使用封装实体类的类名作为别名使用)。

**3**、properties标签有三种配置数据库连接信息的方式：
（1）使用property标签配置；
（2）使用properties标签的resource属性引用外部文件进行配置；
（3） 使用properties标签的url属性引用外部文件进行配置。

