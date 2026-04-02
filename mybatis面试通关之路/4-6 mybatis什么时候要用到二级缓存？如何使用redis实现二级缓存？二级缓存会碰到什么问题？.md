# mybatis什么时候要用到二级缓存？如何使用redis实现二级缓存？二级缓存会碰到什么问题？

## 背景

> 小白：师傅，使用一级缓存时会出现不同Session之间会获取到不同结果的问题，比如SessionA第一次查询获取到记录A，然后SessionB修改了记录A到A',此时SessionA第二次查询直接从缓存获取记录A，而sessionB则获取到记录A‘。能不能让不同的session共享缓存呢？
>
> 扫地僧：Mybatis提供了二级缓存机制，二级缓存是Application级别，可以让session共享缓存。二级缓存只要有两种形式，一种方式是使用本地缓存，适用于应用仅部署一个，当应用部署多个时存在不能保证缓存的一致性问题；另外一种形式是使用第三方缓存系统，将缓存数据存储到第三方，可以保证整个系统的缓存是一致的。
>
> 小白：既然二级缓存这么好用，我们的系统没有看到使用呢？
>
> 扫地僧：其实，不管是一级缓存还是二级缓存，使用都要非常谨慎。对于极少修改的数据可以使用缓存，而对于频繁修改的数据则禁用缓存。

## 本地二级缓存实例

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

![image-20210727165025446](img\chapter04-06.png)

#### 添加依赖

pom.xml

```
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>SecondLevelCacheTest</artifactId>
  <version>4.6.0-SNAPSHOT</version>
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
	<dependency>
	    <groupId>org.mybatis.caches</groupId>
	    <artifactId>mybatis-redis</artifactId>
	    <version>1.0.0-beta2</version>
	</dependency>			
   </dependencies>
</project>
```

注意，mybatis-redis为使用第三方存储redis所需依赖，使用本地存储时此依赖不需要。

#### 实体

**数据库实体**

```java
package com.davidwang456.mybatis.cache;

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
package com.davidwang456.mybatis.cache;

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

**Mybatis配置**

SqlMapConfig.xml

```xml
<?xml version = "1.0" encoding = "UTF-8"?>
<!DOCTYPE configuration PUBLIC "-//mybatis.org//DTD Config 3.0//EN" "http://mybatis.org/dtd/mybatis-3-config.dtd">
<configuration>
	<settings>
		<setting name="logImpl" value="STDOUT_LOGGING"/>
		<setting name="mapUnderscoreToCamelCase" value="true"/>
	<!--  	<setting name="cacheEnabled" value="true"/>
		<setting name="localCacheScope" value="SESSION"/> -->
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

在src/main/resources目录下，定义StudentMapper.xml文件。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
        PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.cache.StudentMapper">
	<cache/>
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.cache.StudentQueryDTO" 
	resultType="com.davidwang456.mybatis.cache.StudentDTO"  useCache="true">
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
	
	<select id="getStudentInfoByCondition2" parameterType="com.davidwang456.mybatis.cache.StudentQueryDTO" 
	resultType="com.davidwang456.mybatis.cache.StudentDTO" useCache="false">
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
	
	<update id="upStudentInfoById">
		update student set age=#{age} where id=#{id}	
	</update>
</mapper>
```

二级缓存默认是不开启的，需要手动开启二级缓存，实现二级缓存的时候，MyBatis要求返回的POJO必须是可序列化的。开启二级缓存的条件也是比较简单，在 Mapper 的xml 配置文件中加入 <cache>标签.

**Mapper文件**

```java
package com.davidwang456.mybatis.cache;

import java.util.List;

import org.apache.ibatis.annotations.Param;

public interface StudentMapper {
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
	public Integer upStudentInfoById(@Param("id")Integer id,@Param("age")Integer age);
	public List<StudentDTO> getStudentInfoByCondition2(StudentQueryDTO studentQueryDTO);
}
```

#### 测试程序

```java
package com.davidwang456.mybatis.cache;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class SecondLevelCacheTest {

	public static void main(String[] args) throws IOException {
		testCache();
	   }
	
	private static void testCache() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();	
	      SqlSession session2 = sqlSessionFactory.openSession();
	      System.out.println("session:"+session+",session2:"+session2);
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentMapper studentMapper2 =session2.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("wang");
	      param.setLastName("david");
	      param.setOrderBy("DESC");
	      param.setSort("age");

	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition(param);	      
	      printResult(stus,"Session1 first query");
	      //session.commit(true);
	      
	      //模拟人的操作
	      //updateWithoutJdbc();
	      //mybatis本身的update语句
	      studentMapper2.upStudentInfoById(8, 30);
	      session2.commit(true);
	      List<StudentDTO> stusCacge=studentMapper.getStudentInfoByCondition(param);
	      printResult(stusCacge,"Session1 cache query");
	      session.commit(true);
	      
	      
		  List<StudentDTO> stuSession2=studentMapper2.getStudentInfoByCondition(param);
		  printResult(stuSession2,"Session2 query"); 
		  session2.commit(true);
		 
	      
	      session.close();
	      session2.close();
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

打印结果：

```tex
==>  Preparing: select id, first_name , last_name , age from student where 1=1 and first_name like ? and last_name like ? order by age DESC
==> Parameters: %wang%(String), %david%(String)
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
------------------Session1 first query------------start-----------
student [id=8, firstName=wang8, lastName=david8, age=28]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=1, firstName=wang1, lastName=david1, age=21]
------------------Session1 first query------------end----------
Opening JDBC Connection
Created connection 937773018.
Setting autocommit to false on JDBC Connection [com.mysql.cj.jdbc.ConnectionImpl@37e547da]
==>  Preparing: update student set age=? where id=?
==> Parameters: 30(Integer), 8(Integer)
<==    Updates: 1
Committing JDBC Connection [com.mysql.cj.jdbc.ConnectionImpl@37e547da]
Cache Hit Ratio [com.davidwang456.mybatis.cache.StudentMapper]: 0.0
------------------Session1 cache query------------start-----------
student [id=8, firstName=wang8, lastName=david8, age=28]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=1, firstName=wang1, lastName=david1, age=21]
------------------Session1 cache query------------end----------
Committing JDBC Connection [com.mysql.cj.jdbc.ConnectionImpl@61009542]
As you are using functionality that deserializes object streams, it is recommended to define the JEP-290 serial filter. Please refer to https://docs.oracle.com/pls/topic/lookup?ctx=javase15&id=GUID-8296D8E8-2B93-4B9A-856E-0A65AF9B8C66
Cache Hit Ratio [com.davidwang456.mybatis.cache.StudentMapper]: 0.3333333333333333
------------------Session2 query------------start-----------
student [id=8, firstName=wang8, lastName=david8, age=28]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=1, firstName=wang1, lastName=david1, age=21]
------------------Session2 query------------end----------
```

结果发现，session2更新数据后，查询结果仍然为修改前的数据即缓存数据。

此时数据库的数据如下：

```tex
    id  first_name  last_name     age  
------  ----------  ---------  --------
     1  wang1       david1           21
     2  wang2       david2           22
     3  wang3       david3           23
     4  wang4       david4           24
     5  wang5       david5           25
     6  wang6       david6           26
     7  wang7       david7           27
     8  wang8       david8           30
```

## Redis二级缓存

**修改二级缓存为Redis**

修改mapper文件的缓存方式为Redis

```xml
<cache eviction="LRU" type="org.mybatis.caches.redis.RedisCache"/>
```

其中cache 标签有多个属性:

- eviction: 缓存回收策略，有这几种回收策略LRU - 最近最少回收，移除最长时间不被使用的对象FIFO - 先进先出，按照缓存进入的顺序来移除它们SOFT - 软引用，移除基于垃圾回收器状态和软引用规则的对象WEAK - 弱引用，更积极的移除基于垃圾收集器和弱引用规则的对象，默认是 LRU 最近最少回收策略

- flushinterval 缓存刷新间隔，缓存多长时间刷新一次，默认不清空，设置一个毫秒值
- readOnly: 是否只读；**true 只读**，MyBatis 认为所有从缓存中获取数据的操作都是只读操作，不会修改数据。MyBatis 为了加快获取数据，直接就会将数据在缓存中的引用交给用户。不安全，速度快。**读写(默认)**：MyBatis 觉得数据可能会被修改
- size : 缓存存放多少个元素
- type: 指定自定义缓存的全类名(实现Cache 接口即可)
- blocking： 若缓存中找不到对应的key，是否会一直blocking，直到有对应的数据进入缓存。

**测试程序**

测试程序不变，返回结果也不变。此时第三方缓存Redis的多出一个hash结构的key，key值为：

```tex
-1368601063:3041902960:com.davidwang456.mybatis.cache.StudentMapper.getStudentInfoByCondition:0:2147483647:select id,
			   first_name ,
			   last_name ,
			   age
			   from student
			   where 1=1 
			    
			    
			   	and first_name like  ?
			    
			    
			   	and last_name like ?
			    			   
			   	
			   order by age DESC:%wang%:%david%:development
```

key的值为：

![image-20210727172226097](img\cache-redis.png)

可以看出，值为一个对象的列表值。

# 深入Mybatis二级缓存原理

**二级缓存的初始化**

XMLMapperBuilder#cacheElement()方法

```java
  private void configurationElement(XNode context) {
    try {
      String namespace = context.getStringAttribute("namespace");
      if (namespace == null || namespace.isEmpty()) {
        throw new BuilderException("Mapper's namespace cannot be empty");
      }
      builderAssistant.setCurrentNamespace(namespace);
      cacheRefElement(context.evalNode("cache-ref"));
      cacheElement(context.evalNode("cache"));
      parameterMapElement(context.evalNodes("/mapper/parameterMap"));
      resultMapElements(context.evalNodes("/mapper/resultMap"));
      sqlElement(context.evalNodes("/mapper/sql"));
      buildStatementFromContext(context.evalNodes("select|insert|update|delete"));
    } catch (Exception e) {
      throw new BuilderException("Error parsing Mapper XML. The XML location is '" + resource + "'. Cause: " + e, e);
    }
  }
  private void cacheElement(XNode context) {
    if (context != null) {
      String type = context.getStringAttribute("type", "PERPETUAL");
      Class<? extends Cache> typeClass = typeAliasRegistry.resolveAlias(type);
      String eviction = context.getStringAttribute("eviction", "LRU");
      Class<? extends Cache> evictionClass = typeAliasRegistry.resolveAlias(eviction);
      Long flushInterval = context.getLongAttribute("flushInterval");
      Integer size = context.getIntAttribute("size");
      boolean readWrite = !context.getBooleanAttribute("readOnly", false);
      boolean blocking = context.getBooleanAttribute("blocking", false);
      Properties props = context.getChildrenAsProperties();
      builderAssistant.useNewCache(typeClass, evictionClass, flushInterval, size, readWrite, blocking, props);
    }
  }
```

**key的读写流程**

CachingExecutor.java#query(),从MappedStatement中获取Cache实现类，并从装饰器类TransactionalCache中读取key：

```java
  @Override
  public <E> List<E> query(MappedStatement ms, Object parameterObject, RowBounds rowBounds, ResultHandler resultHandler, CacheKey key, BoundSql boundSql)
      throws SQLException {
    Cache cache = ms.getCache();
    if (cache != null) {
      flushCacheIfRequired(ms);
      if (ms.isUseCache() && resultHandler == null) {
        ensureNoOutParams(ms, boundSql);
        @SuppressWarnings("unchecked")
        List<E> list = (List<E>) tcm.getObject(cache, key);
        if (list == null) {
          list = delegate.query(ms, parameterObject, rowBounds, resultHandler, key, boundSql);
          tcm.putObject(cache, key, list); // issue #578 and #116
        }
        return list;
      }
    }
    return delegate.query(ms, parameterObject, rowBounds, resultHandler, key, boundSql);
  }

```

TransactionalCache实现了Cache，也是Cache实现类的代理：

```java
  @Override
  public Object getObject(Object key) {
    // issue #116
    Object object = delegate.getObject(key);
    if (object == null) {
      entriesMissedInCache.add(key);
    }
    // issue #146
    if (clearOnCommit) {
      return null;
    } else {
      return object;
    }
  }
```

# 总结

- 二级缓存基于mapper，使用<cache>节点配置开启二级缓存。
- 多个Mapper可以公用一个<cache>,使用<cache-ref namespace="">节点，来指定你的这个Mapper使用到了哪一个Mapper的Cache缓存。
- 二级缓存被多个 SqlSession 共享，是一个**全局的变量**。当开启缓存后，数据的查询执行的流程就是 二级缓存 -> 一级缓存 -> 数据库。