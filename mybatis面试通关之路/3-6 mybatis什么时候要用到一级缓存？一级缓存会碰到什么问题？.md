# mybatis什么时候要用到一级缓存？一级缓存会碰到什么问题？

## 背景

> 小白：师傅，我碰到一个奇怪的问题：先查询一张表的信息，然后做了修改，修改后再查询，数据还是未修改前的数据，但使用数据库客户端，则可以查询到修改后的数据，这是为什么呢？
>
> 扫地僧：这种情况下，一般是命中了缓存。在Mybatis中，我们很有可能多次查询完全相同的sql语句，每一次查询都查询一次数据库，这太浪费资源了。为了更有效的利用资源，Mybatis提出了缓存的概念，根据缓存域的不同分为会话(Session）级别，session内部共享缓存俗称一级缓存和应用级别(Application）支持多个Session共享缓存，也称为二级缓存。一级缓存默认是开启的，二级缓存默认是关闭的。针对你说的问题，我们来看看你的程序是怎么样的。
>
> 小白：那来看看我的代码吧！



## 一级缓存实例

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

![image-20210628094715989](img\chapter03-06.png)

#### 添加依赖

pom.xml

```
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>FirstLevelCacheTest</artifactId>
  <version>3.6.0-SNAPSHOT</version>
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
	<!--  <cache/> -->
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.cache.StudentQueryDTO" 
	resultType="com.davidwang456.mybatis.cache.StudentDTO" flushCache ="false">
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

**Mapper文件**

```java
package com.davidwang456.mybatis.cache;

import java.util.List;

import org.apache.ibatis.annotations.Param;

import com.davidwang456.mybatis.cache.StudentDTO;

public interface StudentMapper {
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
	public Integer upStudentInfoById(@Param("id")Integer id,@Param("age")Integer age);
	public List<StudentDTO> getStudentInfoByCondition2(StudentQueryDTO studentQueryDTO);
}
```

#### 测试程序

**1.默认的一级缓存,但第一次查询不提交**

```java
package com.davidwang456.mybatis.cache;

import java.io.IOException;
import java.io.Reader;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class FirstLevelCacheTest {
    // MySQL 8.0 以下版本 - JDBC 驱动名及数据库 URL
    //static final String JDBC_DRIVER = "com.mysql.jdbc.Driver";  
   // static final String DB_URL = "jdbc:mysql://localhost:3306/davidwang456";
 
    // MySQL 8.0 以上版本 - JDBC 驱动名及数据库 URL
    static final String JDBC_DRIVER = "com.mysql.cj.jdbc.Driver";  
    static final String DB_URL = "jdbc:mysql://localhost:3306/davidwang456?characterEncoding=UTF-8&useSSL=false&useLegacyDatetimeCode=false&serverTimezone=UTC";
 
 
    // 数据库的用户名与密码，需要根据自己的设置
    static final String USER = "root";
    static final String PASS = "wangwei456";

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
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("wang");
	      param.setLastName("david");
	      param.setOrderBy("DESC");
	      param.setSort("age");

	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition(param);	      
	      printResult(stus,"Session1 first query");
	      //session.commit(true);
	      
	      //模拟人的操作
	      updateWithoutJdbc();
	      /**
	      studentMapper2.upStudentInfoById(8, 30);
	      session2.commit(true);
	      **/
	      List<StudentDTO> stusCacge=studentMapper.getStudentInfoByCondition(param);
	      printResult(stusCacge,"Session1 cache query");
	      session.commit(true);
	      
	      StudentMapper studentMapper2 =session2.getMapper(StudentMapper.class);
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
	
	private static void updateWithoutJdbc() {
	     Connection conn = null;
	        PreparedStatement preparedStatement = null;
	        ResultSet rs=null;
	        String sql="";
	        try{
	            //1 注册 JDBC 驱动
	            Class.forName(JDBC_DRIVER);
	        
	            //2 打开链接
	            System.out.println("连接数据库...");
	            conn = DriverManager.getConnection(DB_URL,USER,PASS);
	            
	            //3 定义操作的SQL语句,实例化PreparedStatement对象,设置入参           
	            sql = "update student set age=? where id = ?";
	            System.out.println(" 实例化PreparedStatement对象...");
	            preparedStatement = conn.prepareStatement(sql);
	            preparedStatement.setInt(1, 30);
	            preparedStatement.setInt(2, 8);
	            //4 执行数据库操作
	            preparedStatement.executeUpdate();
	            //5 完成后关闭
	            // 关闭资源
	            shutdownResource(conn,preparedStatement,rs);
	        }catch(SQLException se){
	            // 处理 JDBC 错误
	            se.printStackTrace();
	        }catch(Exception e){
	            // 处理 Class.forName 错误
	            e.printStackTrace();
	        }finally{
	        	shutdownResource(conn,preparedStatement,rs);
	        }
	    }
	    
	    public static void shutdownResource(Connection conn,Statement stmt,ResultSet rs) {
	        // 关闭资源
	    	try {
	    		if(rs!=null) {
	    			rs.close();
	    		}
	    	}catch(SQLException se1){
	    		//TODO
	    	}
	    	
	        try{
	            if(stmt!=null) stmt.close();
	        }catch(SQLException se2){
	        	//TODO
	        }
	        
	        try{
	            if(conn!=null) conn.close();
	        }catch(SQLException se){
	            //TODO
	        }
	    }
}
```

此时，打印结果如下：

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
连接数据库...
 实例化PreparedStatement对象...
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
Opening JDBC Connection
Created connection 1197365356.
Setting autocommit to false on JDBC Connection [com.mysql.cj.jdbc.ConnectionImpl@475e586c]
==>  Preparing: select id, first_name , last_name , age from student where 1=1 and first_name like ? and last_name like ? order by age DESC
==> Parameters: %wang%(String), %david%(String)
<==    Columns: id, first_name, last_name, age
<==        Row: 8, wang8, david8, 30
<==        Row: 7, wang7, david7, 27
<==        Row: 6, wang6, david6, 26
<==        Row: 5, wang5, david5, 25
<==        Row: 4, wang4, david4, 24
<==        Row: 3, wang3, david3, 23
<==        Row: 2, wang2, david2, 22
<==        Row: 1, wang1, david1, 21
<==      Total: 8
------------------Session2 query------------start-----------
student [id=8, firstName=wang8, lastName=david8, age=30]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=1, firstName=wang1, lastName=david1, age=21]
------------------Session2 query------------end----------
```

步骤1：查询student表结果符合预期

步骤2：修改其中的一条记录的年龄，通过SQLyog UI查询，修改成功

步骤3：再次查询表student表，发现查询的是修改前的数据。

步骤4：使用另外一个session：session2查询，得到修改后的数据。

**2.第一次查询完提交**

放开注解的session.commit(true);

重现初始化数据记录，执行结果如下：

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
Committing JDBC Connection [com.mysql.cj.jdbc.ConnectionImpl@1b68b9a4]
连接数据库...
 实例化PreparedStatement对象...
==>  Preparing: select id, first_name , last_name , age from student where 1=1 and first_name like ? and last_name like ? order by age DESC
==> Parameters: %wang%(String), %david%(String)
<==    Columns: id, first_name, last_name, age
<==        Row: 8, wang8, david8, 30
<==        Row: 7, wang7, david7, 27
<==        Row: 6, wang6, david6, 26
<==        Row: 5, wang5, david5, 25
<==        Row: 4, wang4, david4, 24
<==        Row: 3, wang3, david3, 23
<==        Row: 2, wang2, david2, 22
<==        Row: 1, wang1, david1, 21
<==      Total: 8
------------------Session1 cache query------------start-----------
student [id=8, firstName=wang8, lastName=david8, age=30]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=1, firstName=wang1, lastName=david1, age=21]
------------------Session1 cache query------------end----------
Committing JDBC Connection [com.mysql.cj.jdbc.ConnectionImpl@1b68b9a4]
Opening JDBC Connection
Created connection 1524126153.
Setting autocommit to false on JDBC Connection [com.mysql.cj.jdbc.ConnectionImpl@5ad851c9]
==>  Preparing: select id, first_name , last_name , age from student where 1=1 and first_name like ? and last_name like ? order by age DESC
==> Parameters: %wang%(String), %david%(String)
<==    Columns: id, first_name, last_name, age
<==        Row: 8, wang8, david8, 30
<==        Row: 7, wang7, david7, 27
<==        Row: 6, wang6, david6, 26
<==        Row: 5, wang5, david5, 25
<==        Row: 4, wang4, david4, 24
<==        Row: 3, wang3, david3, 23
<==        Row: 2, wang2, david2, 22
<==        Row: 1, wang1, david1, 21
<==      Total: 8
------------------Session2 query------------start-----------
student [id=8, firstName=wang8, lastName=david8, age=30]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=1, firstName=wang1, lastName=david1, age=21]
------------------Session2 query------------end----------
```

此时，本应该走缓存的查询，走的是数据库查询。

**3,使用mybatis提供的修改功能**

如果我们不走原始jdbc修改，而使用mybatis本身的修改功能，同时第一次查询后的提交也注释掉：

```java
	private static void testCache() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();	
	      SqlSession session2 = sqlSessionFactory.openSession();
	      System.out.println("session:"+session+",session2:"+session2);
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
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
	      studentMapper.upStudentInfoById(8, 30);
	      //session2.commit(true);
	      List<StudentDTO> stusCacge=studentMapper.getStudentInfoByCondition(param);
	      printResult(stusCacge,"Session1 cache query");
	      session.commit(true);
	      
	      StudentMapper studentMapper2 =session2.getMapper(StudentMapper.class);
		  List<StudentDTO> stuSession2=studentMapper2.getStudentInfoByCondition(param);
		  printResult(stuSession2,"Session2 query"); 
		  session2.commit(true);
		 
	      
	      session.close();
	      session2.close();
	}
```

重现初始化数据库脚本，发现结果如测试2，预期走缓存的查询，其实是从数据库直接查询。

**使用Mybatis的另一个Session2进行修改**

```java
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
```

不管session2是否提交，结果和使用jdbc得到的结果相同。

通过上面4个实例，我们可以推断出默认情况下一级缓存是session级别的。另外还可以通过setting属性LocalCacheScope来设置缓存的级别，支持两种级别：SESSION,STATEMENT。



## Mybatis一级缓存原理揭秘

**查询操作**

深入debug代码，追踪到BaseExecutor#query方法：

```java
  @SuppressWarnings("unchecked")
  @Override
  public <E> List<E> query(MappedStatement ms, Object parameter, RowBounds rowBounds, ResultHandler resultHandler, CacheKey key, BoundSql boundSql) throws SQLException {
    ErrorContext.instance().resource(ms.getResource()).activity("executing a query").object(ms.getId());
    if (closed) {
      throw new ExecutorException("Executor was closed.");
    }
    if (queryStack == 0 && ms.isFlushCacheRequired()) {
      clearLocalCache();
    }
    List<E> list;
    try {
      queryStack++;
      list = resultHandler == null ? (List<E>) localCache.getObject(key) : null;
      if (list != null) {
        handleLocallyCachedOutputParameters(ms, key, parameter, boundSql);
      } else {
        list = queryFromDatabase(ms, parameter, rowBounds, resultHandler, key, boundSql);
      }
    } finally {
      queryStack--;
    }
    if (queryStack == 0) {
      for (DeferredLoad deferredLoad : deferredLoads) {
        deferredLoad.load();
      }
      // issue #601
      deferredLoads.clear();
      if (configuration.getLocalCacheScope() == LocalCacheScope.STATEMENT) {
        // issue #482
        clearLocalCache();
      }
    }
    return list;
  }
```

可以看到MappedStatement的一个属性flushCache支持缓存的关闭和开启(**注意：它对二级缓存也具有同样的效力**)。

**更新或者提交操作**

提交操作,清除缓存

```java
  @Override
  public void commit(boolean required) throws SQLException {
    if (closed) {
      throw new ExecutorException("Cannot commit, transaction is already closed");
    }
    clearLocalCache();
    flushStatements();
    if (required) {
      transaction.commit();
    }
  }
```

更新操作，清除缓存

```java
  @Override
  public int update(MappedStatement ms, Object parameter) throws SQLException {
    ErrorContext.instance().resource(ms.getResource()).activity("executing an update").object(ms.getId());
    if (closed) {
      throw new ExecutorException("Executor was closed.");
    }
    clearLocalCache();
    return doUpdate(ms, parameter);
  }
```



# 总结

- Mybatis对缓存提供支持，但是在没有配置的默认情况下，它只开启一级缓存，且缓存级别为SqlSession。在参数和SQL完全一样的情况下，我们使用同一个SqlSession对象调用一个Mapper方法，往往只执行一次SQL，因为使用SelSession第一次查询后，MyBatis会将其放在缓存中，以后再查询的时候，如果没有声明需要刷新，并且缓存没有超时的情况下，SqlSession都会取出当前缓存的数据，而不会再次发送SQL到数据库。
- 默认情况下二级缓存是不开启的，开启需要配置<cache />
- 一级二级缓存的总开关的设置为<setting name="cacheEnabled" value="true"/> 默认开启。
- 默认配置一级缓存的生命周期在一个session内生效，超过这个就不会生效了。也可以配置为STATEMENT级别。
- 对于数据变化频率很大，并且需要高时效准确性的数据要求，我们使用**SqlSession**查询的时候，要控制好**SqlSession**的生存时间，**SqlSession**的生存时间越长，它其中缓存的数据有可能就越旧，从而造成和真实数据库的误差；同时对于这种情况，用户也可以手动地适时清空**SqlSession**中的缓存；
- 防止查询过大，导致内存溢出，若内存过大则可以通过配合第三方的二级缓存。