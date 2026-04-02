# 如何使用mybatis实现分页查询？有几种方式？各种方式的优缺点是什么？

## 背景

> 小白：师傅，在开发中常常遇到分页问题，有点概念不太理解，有人说什么逻辑分页和物理分页，还有什么深度分页。分页怎么有这么多种方式呢？
>
> 扫地僧：根据应用常见的不同，分页也会有不同的方式。逻辑分页是从**一次性从数据库中查询出全部数据并存储到List集合中，截取分页数据给前端展示**，这种分页适用于数据量较小(通常记录数最大为几百上千)；物理分页是指**直接从数据库中拿到分页的数据返回给前端展示**，这种分页适合于数据记录比较大的多的场景；深度分页则是针对物理分页无法解决的问题，做针对性的优化，如mysql使用limit时，拿从数据库表1kw位置的100条数据这种情况。
>
> 小白：我有点明白了，原来平常开发中用到的分页是指物理分页，直接sql实现分页查询，不同数据库之间的sql都不一致。
>
> 扫地僧：在平常的业务开发中，大部分人都是使用物理分页，它的主要问题有两个：1.不同数据库间sql无法兼容，mysql使用limit，oracle使用rownum，sqlserver使用top not in方式 （适应于数据库2012以下的版本），offset fetch next方式（SQL2012以上的版本才支持：推荐使用 ）；2.在数据记录比较时中间的分页会比较慢，逻辑分页和深入分页恰恰是为了解决这两个问题而存在的。那就代码见真章吧！



# 逻辑分页实例

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

![image-20210712155852367](img\paging.png)

#### 添加依赖

pom.xml

```
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>PagingTest</artifactId>
  <version>4.2.0-SNAPSHOT</version>
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
package com.davidwang456.mybatis.paging;

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
package com.davidwang456.mybatis.paging;

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
		<setting name="logImpl" value="STDOUT_LOGGING"/>
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

其中，配置mybatis.configuration.map-underscore-to-camel-case=true定义了支持驼峰形式，配置logImpl定义了日志打印的类为STDOUT_LOGGING.

在src/main/resources目录下，定义**mapper.xml文件。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
        PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.paging.StudentMapper">
	<!--  <paging/> -->
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.paging.StudentQueryDTO" 
	resultType="com.davidwang456.mybatis.paging.StudentDTO">
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
package com.davidwang456.mybatis.paging;

import java.util.List;

public interface StudentMapper {
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
}
```

#### 测试程序

```java
package com.davidwang456.mybatis.paging;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.RowBounds;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class PagingTest {

	public static void main(String[] args) throws IOException {
		testPaging();
	   }
	
	private static void testPaging() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();	
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("wang");
	      param.setLastName("david");
	      param.setOrderBy("DESC");
	      param.setSort("age");	     
	      RowBounds rbs=new RowBounds(0,5);
	      List<StudentDTO> stus= session.selectList("getStudentInfoByCondition", param, rbs);     
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
==>  Preparing: select id, first_name , last_name , age from student where 1=1 and first_name like ? and last_name like ? order by age DESC
==> Parameters: %wang%(String), %david%(String)
<==    Columns: id, first_name, last_name, age
<==        Row: 8, wang8, david8, 28
<==        Row: 7, wang7, david7, 27
<==        Row: 6, wang6, david6, 26
<==        Row: 5, wang5, david5, 25
<==        Row: 4, wang4, david4, 24
------------------getStudentInfoByCondition query------------start-----------
student [id=8, firstName=wang8, lastName=david8, age=28]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=4, firstName=wang4, lastName=david4, age=24]
------------------getStudentInfoByCondition query------------end----------
```

我们可以看到，在上述的查询语句中并没有出现limit等分页信息字样，但确实实现了分页。内部的原理我们稍后再讲。

## 物理分页实例

**修改查询实体，新增分页信息**

```java
package com.davidwang456.mybatis.paging;

import lombok.Data;

@Data
public class StudentQueryDTO {
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	
	private Integer pageSize=5;
	private Integer page=1;
	
	private Integer start;
	
	private Integer offset;
	//排序列
	private String sort;
	//排序 DESC|ASC
	private String orderBy;
	
	public Integer getStart() {
		return (page-1)*pageSize;
	}
	
	public Integer getOffset() {
		return page*pageSize;
	}
}
```

**修改配置文件映射sql**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
        PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.paging.StudentMapper">
	<!--  <paging/> -->
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.paging.StudentQueryDTO" 
	resultType="com.davidwang456.mybatis.paging.StudentDTO">
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
			   limit #{start},#{offset}	   		   		  				  
	</select>
</mapper>
```

**新增物理分页查询**

```java
	private static void testPhysicalPaging() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();	
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("wang");
	      param.setLastName("david");
	      param.setOrderBy("DESC");
	      param.setSort("age");	     
          param.setPage(1);
          param.setPageSize(5);
          StudentMapper sm=session.getMapper(StudentMapper.class);
          List<StudentDTO> stus=sm.getStudentInfoByCondition(param);
	      printResult(stus,"getStudentInfoByCondition query");
	      session.commit(true);
	      session.close();
	}
```

执行此查询语句，得到结果和逻辑分页一致，符合预期。

## 深度分页

深度分页一般有三种方式：

### Mysql深度分页第一式：复用

分页查询的特性决定了它可以有效利用上一次查询的结果，举例：

```
select id, first_name, last_name, age
FROM student
WHERE id > 99000
ORDER BY id LIMIT 10;
```

通过where条件来缩小查询范围，当然不是每个查询都有id做where条件，但也可以使用其他条件：

```
select id, first_name, last_name, age
FROM student
WHERE first_name > 30
ORDER BY first_name LIMIT 10;
```

不一定好用！

### Mysql深度分页第二式：延迟联接

如果想要查询第99页：

```
select id, first_name, last_name, age FROM student ORDER BY first_name LIMIT 10 OFFSET 990;
```

那么可以试试：

```
select id, first_name, last_name, age
FROM student
INNER JOIN (
SELECT id
FROM student
ORDER BY first_name
LIMIT 10 OFFSET 990)
AS my_results USING(id);
```

### Mysql深度分页第三式：维护一个页或者位置列

维护一个页列

```
select id, first_name, last_name, age
FROM student
WHERE page = 100
ORDER BY first_name;
```

维护一个位置列

```
select id, first_name, last_name, age
FROM student
WHERE place BETWEEN 990 AND 999
ORDER BY student;
```

麻烦的是，当您(a)插入一行(b)删除一行(c)使用update移动一行时，您需要更新该列。这可能会让页面变得凌乱。



# 深入逻辑分页原理

物理分页是利用数据库提供的分页字段来完成，深度分页更多是利用业务来缩小查询的范围，这些都是明确的；mybatis逻辑分页是通过RowBounds来实现的，RowBounds内部是怎么完成按需分页的？

原来在默认的结果处理器DefaultResultSetHandler中，将数据库返回的结果进行筛选，不满足条件的就跳过。

```java
  private void handleRowValuesForSimpleResultMap(ResultSetWrapper rsw, ResultMap resultMap, ResultHandler<?> resultHandler, RowBounds rowBounds, ResultMapping parentMapping)
      throws SQLException {
    DefaultResultContext<Object> resultContext = new DefaultResultContext<>();
    ResultSet resultSet = rsw.getResultSet();
    skipRows(resultSet, rowBounds);
    while (shouldProcessMoreRows(resultContext, rowBounds) && !resultSet.isClosed() && resultSet.next()) {
      ResultMap discriminatedResultMap = resolveDiscriminatedResultMap(resultSet, resultMap, null);
      Object rowValue = getRowValue(rsw, discriminatedResultMap, null);
      storeObject(resultHandler, resultContext, rowValue, parentMapping, resultSet);
    }
  }
  private void skipRows(ResultSet rs, RowBounds rowBounds) throws SQLException {
    if (rs.getType() != ResultSet.TYPE_FORWARD_ONLY) {
      if (rowBounds.getOffset() != RowBounds.NO_ROW_OFFSET) {
        rs.absolute(rowBounds.getOffset());
      }
    } else {
      for (int i = 0; i < rowBounds.getOffset(); i++) {
        if (!rs.next()) {
          break;
        }
      }
    }
  }
```

可以看到，最终实现逻辑分页是在DefaultResultSetHandler中。

# 总结

Mybatis提供了一个简单的逻辑分页使用类RowBounds（物理分页当然就是我们在sql语句中指定limit和offset值），在DefaultSqlSession提供的某些查询接口中可以看到RowBounds是作为参数用来进行分页的。逻辑分页主要用于数据量不大、数据稳定的场合，物理分页主要用于数据量较大、更新频繁的场合。

**逻辑分页和物理分页对比**

1.数据库负担
物理分页每次都访问数据库，逻辑分页只访问一次数据库，物理分页对数据库造成的负担大。
2.服务器负担
逻辑分页一次性将数据读取到内存，占用了较大的内容空间，物理分页每次只读取一部分数据，占用内存空间较小。
3.实时性
逻辑分页一次性将数据读取到内存，数据发生改变，数据库的最新状态不能实时反映到操作中，实时性差。物理分页每次需要数据时都访问数据库，能够获取数据库的最新状态，实时性强。

**深度分页**

深度分页可以通过业务缩小查询范围来完成，如果业务上无法缩小范围，可以考虑通过es，solr等搜索引擎实现。