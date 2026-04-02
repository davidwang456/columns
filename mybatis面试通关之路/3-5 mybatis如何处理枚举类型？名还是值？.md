# Mybatis如何处理枚举类型？名还是值？

## 背景

> 小白：师傅，我使用枚举类时，存储到数据的值总是不是我期望的，请您帮忙看一下？
>
> 扫地僧：那你先给我展示一下你是怎么使用的吧？

## Mybatis枚举类型示例

### 准备工作

mysql数据库,本实例的版本为:8.0.16

mysql客户端SQLyog(免费，不需要注册码)或者navicat for mysql

创建数据库davidwang456和表

```sql
CREATE database davidwang456;
use davidwang456;


DROP TABLE IF EXISTS  student;
CREATE TABLE `student` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `first_name` varchar(100) DEFAULT NULL,
  `last_name` varchar(100) DEFAULT NULL,
  `age` int(11) DEFAULT NULL,
  `status` VARCHAR(10) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP TABLE IF EXISTS  student1;
CREATE TABLE `student1` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `first_name` varchar(100) DEFAULT NULL,
  `last_name` varchar(100) DEFAULT NULL,
  `age` int(11) DEFAULT NULL,
  `status` VARCHAR(10) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP TABLE IF EXISTS  student2;
CREATE TABLE `student2` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `first_name` varchar(100) DEFAULT NULL,
  `last_name` varchar(100) DEFAULT NULL,
  `age` int(11) DEFAULT NULL,
  `status` int(2) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP TABLE IF EXISTS  student3;
CREATE TABLE `student3` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `first_name` varchar(100) DEFAULT NULL,
  `last_name` varchar(100) DEFAULT NULL,
  `age` int(11) DEFAULT NULL,
  `status` int(2) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 创建maven项目

#### 添加依赖

pom.xml

```xml
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>EnumTest</artifactId>
  <version>1.5.0-SNAPSHOT</version>
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
package com.davidwang456.mybatis.enumtest;

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
	private Status status;
	@Override
	   public String toString() {
	    return "student [id=" + id + ", firstName=" + firstName
	    		 + ", lastName=" + lastName + ", age=" +age+", status="+status.getDesc()+ ']';
	   }
}
```

其中，

```java
package com.davidwang456.mybatis.enumtest;

public enum Status {
    NEW(1,"NEW"),ACTIVE(2,"ACTIVE"),INACTIVE(3,"INACTIVE"),DELETE(4,"DEL");
	private Integer code;
	private String  desc;
	
	Status(Integer code,String desc){
		this.setCode(code);
		this.setDesc(desc);
	}

	public Integer getCode() {
		return code;
	}

	public void setCode(Integer code) {
		this.code = code;
	}

	public String getDesc() {
		return desc;
	}

	public void setDesc(String desc) {
		this.desc = desc;
	}
	
	public static void main(String[] args) {
		Status st=Status.DELETE;
		System.out.println(st.name());
	}
	
}
```

自定义TypeHandler

```java
package com.davidwang456.mybatis.enumtest;

import java.sql.CallableStatement;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;

import org.apache.ibatis.type.BaseTypeHandler;
import org.apache.ibatis.type.JdbcType;

public class StatusTypeHandler extends BaseTypeHandler<Status> {

	@Override
	public void setNonNullParameter(PreparedStatement ps, int i, Status parameter, JdbcType jdbcType)
			throws SQLException {
		ps.setInt(i, parameter.getCode());
	}

	@Override
	public Status getNullableResult(ResultSet rs, String columnName) throws SQLException {
		Integer code=rs.getInt(columnName);
		return getStatus(code);
	}

	@Override
	public Status getNullableResult(ResultSet rs, int columnIndex) throws SQLException {
		Integer code=rs.getInt(columnIndex);
		return getStatus(code);
	}

	@Override
	public Status getNullableResult(CallableStatement cs, int columnIndex) throws SQLException {
		Integer code=cs.getInt(columnIndex);
		return getStatus(code);
	}
	
	public Status getStatus(Integer code) {
		Status[] ss=Status.values();
		for(Status s:ss) {
			if(s.getCode().equals(code)) {
				return s;
			}
		}
		return null;
	}

}
```



**查询实体**

```java
package com.davidwang456.mybatis.enumtest;

import lombok.Data;

@Data
public class StudentQueryDTO {
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	
	private Status status;
	//关键词查询,依据firstName和lastName
	private String keyword;
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
   	<typeAlias alias="StudentDTO" type="com.davidwang456.mybatis.enumtest.StudentDTO"/>
    <typeAlias alias="StudentQueryDTO" type="com.davidwang456.mybatis.enumtest.StudentQueryDTO"/>
   </typeAliases>   
   
   <typeHandlers>  	    
   	 <!-- <typeHandler handler="org.apache.ibatis.type.EnumTypeHandler" javaType="com.davidwang456.mybatis.enumtest.Status"/>  
   	   <typeHandler handler="com.davidwang456.mybatis.enumtest.StatusTypeHandler" javaType="com.davidwang456.mybatis.enumtest.Status"/>
   	  <typeHandler handler="org.apache.ibatis.type.EnumOrdinalTypeHandler" javaType="com.davidwang456.mybatis.enumtest.Status"/> 
   	  -->
        
   </typeHandlers>
   
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

**Mybatis映射文件配置**

```java
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
        PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.enumtest.StudentMapper">	
	<insert id="insert" parameterType="StudentDTO">
		insert into student(first_name ,
			   last_name ,
			   age,
			   status)values(
			   #{firstName},
			   #{lastName},
			   #{age},
			   #{status}
			   )
	</insert>
	
		<insert id="insert1" parameterType="StudentDTO">
		insert into student1(first_name ,
			   last_name ,
			   age,
			   status)values(
			   #{firstName},
			   #{lastName},
			   #{age},
			   #{status,typeHandler=org.apache.ibatis.type.EnumTypeHandler}
			   )
	</insert>
	
	<insert id="insert2" parameterType="StudentDTO">
		insert into student2(first_name ,
			   last_name ,
			   age,
			   status)values(
			   #{firstName},
			   #{lastName},
			   #{age},
			   #{status,typeHandler=org.apache.ibatis.type.EnumOrdinalTypeHandler}
			   )
	</insert>

	<insert id="insert3" parameterType="StudentDTO">
		insert into student3(first_name ,
			   last_name ,
			   age,
			   status)values(
			   #{firstName},
			   #{lastName},
			   #{age},
			   #{status,typeHandler=com.davidwang456.mybatis.enumtest.StatusTypeHandler}
			   )
	</insert>	

	<resultMap type="StudentDTO" id="student">
		<id column="id" property="id" />
		<result column="first_name" property="firstName" ></result>
		<result column="last_name" property="lastName" ></result>
		<result column="age" property="age" ></result>
		<result column="status" property="status" javaType="com.davidwang456.mybatis.enumtest.Status" jdbcType="VARCHAR"></result>
	</resultMap>		
	<resultMap type="StudentDTO" id="student1">
		<id column="id" property="id" />
		<result column="first_name" property="firstName" ></result>
		<result column="last_name" property="lastName" ></result>
		<result column="age" property="age" ></result>
		<result column="status" property="status" javaType="com.davidwang456.mybatis.enumtest.Status" jdbcType="VARCHAR" typeHandler="org.apache.ibatis.type.EnumTypeHandler" ></result>
	</resultMap>
	<resultMap type="StudentDTO" id="student2">
		<id column="id" property="id" />
		<result column="first_name" property="firstName" ></result>
		<result column="last_name" property="lastName" ></result>
		<result column="age" property="age" ></result>
		<result column="status" property="status" javaType="com.davidwang456.mybatis.enumtest.Status" jdbcType="VARCHAR" typeHandler="org.apache.ibatis.type.EnumOrdinalTypeHandler" ></result>
	</resultMap>	
	
	<resultMap type="StudentDTO" id="student3">
		<id column="id" property="id" />
		<result column="first_name" property="firstName" ></result>
		<result column="last_name" property="lastName" ></result>
		<result column="age" property="age" ></result>
		<result column="status" property="status" javaType="com.davidwang456.mybatis.enumtest.Status" jdbcType="VARCHAR" typeHandler="com.davidwang456.mybatis.enumtest.StatusTypeHandler" ></result>
	</resultMap>	
	<select id="getStudentInfoByCondition" parameterType="StudentQueryDTO" resultMap="student">
		<bind name="condition" value="'%'+keyword+'%'"/>
		select id,
			   first_name ,
			   last_name ,
			   age,
			   status
			   from student
			   where 1=1 
			   <if test="id!=null">
			   and id=#{id}
			   </if>
			   <if test="firstName!=null and firstName!=''">
			   	and first_name = #{firstName}
			   </if>
			   <if test="lastName!=null and lastName!=''">
			   	and last_name = #{lastName}
			   </if>			   
			   <if test="keyword!=null and keyword!=''">
			   and 
			   (first_name LIKE #{condition}
			   OR last_name LIKE #{condition}
			   )
			   </if>
			  <if test="age!=null and age!=0">
			   and age=#{age}
			   </if>			   
			  <if test="status!=null">
			   and status=#{status}
			   </if>			   		   		  				  
	</select>
		<select id="getStudentInfoByCondition1" parameterType="StudentQueryDTO" resultMap="student1">
		<bind name="condition" value="'%'+keyword+'%'"/>
		select id,
			   first_name ,
			   last_name ,
			   age,
			   status
			   from student1
			   where 1=1 
			   <if test="id!=null">
			   and id=#{id}
			   </if>
			   <if test="firstName!=null and firstName!=''">
			   	and first_name = #{firstName}
			   </if>
			   <if test="lastName!=null and lastName!=''">
			   	and last_name = #{lastName}
			   </if>			   
			   <if test="keyword!=null and keyword!=''">
			   and 
			   (first_name LIKE #{condition}
			   OR last_name LIKE #{condition}
			   )
			   </if>
			  <if test="age!=null and age!=0">
			   and age=#{age}
			   </if>			   
			  <if test="status!=null">
			   and status=#{status,typeHandler=org.apache.ibatis.type.EnumTypeHandler}
			   </if>			   		   		  				  
	</select>
	
	<select id="getStudentInfoByCondition2" parameterType="StudentQueryDTO" resultMap="student2">
		<bind name="condition" value="'%'+keyword+'%'"/>
		select id,
			   first_name ,
			   last_name ,
			   age,
			   status
			   from student2
			   where 1=1 
			   <if test="id!=null">
			   and id=#{id}
			   </if>
			   <if test="firstName!=null and firstName!=''">
			   	and first_name = #{firstName}
			   </if>
			   <if test="lastName!=null and lastName!=''">
			   	and last_name = #{lastName}
			   </if>			   
			   <if test="keyword!=null and keyword!=''">
			   and 
			   (first_name LIKE #{condition}
			   OR last_name LIKE #{condition}
			   )
			   </if>
			  <if test="age!=null and age!=0">
			   and age=#{age}
			   </if>			   
			  <if test="status!=null">
			   and status=#{status,typeHandler=org.apache.ibatis.type.EnumOrdinalTypeHandler}
			   </if>			   		   		  				  
	</select>

	<select id="getStudentInfoByCondition3" parameterType="StudentQueryDTO" resultMap="student3">
		<bind name="condition" value="'%'+keyword+'%'"/>
		select id,
			   first_name ,
			   last_name ,
			   age,
			   status
			   from student3
			   where 1=1 
			   <if test="id!=null">
			   and id=#{id}
			   </if>
			   <if test="firstName!=null and firstName!=''">
			   	and first_name = #{firstName}
			   </if>
			   <if test="lastName!=null and lastName!=''">
			   	and last_name = #{lastName}
			   </if>			   
			   <if test="keyword!=null and keyword!=''">
			   and 
			   (first_name LIKE #{condition}
			   OR last_name LIKE #{condition}
			   )
			   </if>
			  <if test="age!=null and age!=0">
			   and age=#{age}
			   </if>			   
			  <if test="status!=null">
			   and status=#{status,typeHandler=com.davidwang456.mybatis.enumtest.StatusTypeHandler}
			   </if>			   		   		  				  
	</select>		
</mapper>
```

**Mapper文件**

```java
package com.davidwang456.mybatis.enumtest;

import java.util.List;

public interface StudentMapper {
	public Integer insert(com.davidwang456.mybatis.enumtest.StudentDTO studentDTO);
	public Integer insert1(com.davidwang456.mybatis.enumtest.StudentDTO studentDTO);
	public Integer insert2(com.davidwang456.mybatis.enumtest.StudentDTO studentDTO);
	public Integer insert3(com.davidwang456.mybatis.enumtest.StudentDTO studentDTO);
	
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
	public List<StudentDTO> getStudentInfoByCondition1(StudentQueryDTO studentQueryDTO);
	public List<StudentDTO> getStudentInfoByCondition2(StudentQueryDTO studentQueryDTO);
	public List<StudentDTO> getStudentInfoByCondition3(StudentQueryDTO studentQueryDTO);

}
```

#### 测试程序

```java
package com.davidwang456.mybatis.enumtest;

import java.io.IOException;
import java.io.Reader;
import java.util.List;
import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;

public class EnumTest {

	public static void main(String[] args) throws IOException {
		insert();
		insert1();
		insert2();
		insert3();
		//query();
		//query1();
		//query2();
		//query3();
	   }
	
	public static void insert() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO param=new StudentDTO();
	      param.setFirstName("david");
	      param.setLastName("wang");
	      param.setAge(20);
	      param.setStatus(Status.ACTIVE);
	      Integer effected=studentMapper.insert(param);
	      if(effected>0) {
	      System.out.println("插入记录成功！ ");
	      }
	      session.commit(true);
	      session.close();		
	}
	
	public static void insert1() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO param=new StudentDTO();
	      param.setFirstName("david1");
	      param.setLastName("wang1");
	      param.setAge(20);
	      param.setStatus(Status.ACTIVE);
	      Integer effected=studentMapper.insert1(param);
	      if(effected>0) {
	      System.out.println("插入记录成功！ ");
	      }
	      session.commit(true);
	      session.close();		
	}
	
	public static void insert2() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO param=new StudentDTO();
	      param.setFirstName("david2");
	      param.setLastName("wang2");
	      param.setAge(20);
	      param.setStatus(Status.ACTIVE);
	      Integer effected=studentMapper.insert2(param);
	      if(effected>0) {
	      System.out.println("插入记录成功！ ");
	      }
	      session.commit(true);
	      session.close();		
	}
	
	public static void insert3() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO param=new StudentDTO();
	      param.setFirstName("david3");
	      param.setLastName("wang3");
	      param.setAge(20);
	      param.setStatus(Status.ACTIVE);
	      Integer effected=studentMapper.insert3(param);
	      if(effected>0) {
	      System.out.println("插入记录成功！ ");
	      }
	      session.commit(true);
	      session.close();		
	}
	
	public static void query() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setKeyword("david");
	      param.setStatus(Status.ACTIVE);
	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition(param);
	      System.out.println("------------------query------------------");
	      for(StudentDTO stu:stus) {
	      System.out.println(stu.toString());
	      }
	      System.out.println("------------------query------------------");
	      session.commit(true);
	      session.close();		
	}
	
	public static void query1() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setKeyword("david");
	      param.setStatus(Status.ACTIVE);
	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition1(param);
	      System.out.println("------------------query1------------------");
	      for(StudentDTO stu:stus) {
	      System.out.println(stu.toString());
	      }
	      System.out.println("------------------query1------------------");
	      session.commit(true);
	      session.close();		
	}
	
	public static void query2() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setKeyword("david");
	      param.setStatus(Status.ACTIVE);
	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition2(param);
	      System.out.println("------------------query2------------------");
	      for(StudentDTO stu:stus) {
	      System.out.println(stu.toString());
	      }
	      System.out.println("------------------query2------------------");
	      session.commit(true);
	      session.close();		
	}
	
	public static void query3() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setKeyword("david");
	      param.setStatus(Status.ACTIVE);
	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition3(param);
	      System.out.println("------------------query3------------------");
	      for(StudentDTO stu:stus) {
	      System.out.println(stu.toString());
	      }
	      System.out.println("------------------query3------------------");
	      session.commit(true);
	      session.close();		
	}
}
```

运行结果：

```java
插入记录成功！
插入记录成功！
插入记录成功！
插入记录成功！
```

查询数据库，表student

```tex
1	david	wang	20	ACTIVE
```

查询数据库，表student1

```tex
1	david1	wang1	20	ACTIVE
```

查询数据库，表student2

```tex
1	david2	wang2	20	1
```

查询数据库，表student3

```tex
1	david3	wang3	20	2
```

符合预期！

注意：ordinal()方法，用来返回枚举对象的序数，从0开始。

## 深入内部原理

默认情况下的枚举类型处理类EnumTypeHandler.java

```java
package org.apache.ibatis.type;

import java.sql.CallableStatement;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;

/**
 * @author Clinton Begin
 */
public class EnumTypeHandler<E extends Enum<E>> extends BaseTypeHandler<E> {

  private final Class<E> type;

  public EnumTypeHandler(Class<E> type) {
    if (type == null) {
      throw new IllegalArgumentException("Type argument cannot be null");
    }
    this.type = type;
  }

  @Override
  public void setNonNullParameter(PreparedStatement ps, int i, E parameter, JdbcType jdbcType) throws SQLException {
    if (jdbcType == null) {
      ps.setString(i, parameter.name());
    } else {
      ps.setObject(i, parameter.name(), jdbcType.TYPE_CODE); // see r3589
    }
  }

  @Override
  public E getNullableResult(ResultSet rs, String columnName) throws SQLException {
    String s = rs.getString(columnName);
    return s == null ? null : Enum.valueOf(type, s);
  }

  @Override
  public E getNullableResult(ResultSet rs, int columnIndex) throws SQLException {
    String s = rs.getString(columnIndex);
    return s == null ? null : Enum.valueOf(type, s);
  }

  @Override
  public E getNullableResult(CallableStatement cs, int columnIndex) throws SQLException {
    String s = cs.getString(columnIndex);
    return s == null ? null : Enum.valueOf(type, s);
  }
}
```



还可以使用EnumOrdinalTypeHandler

```java
package org.apache.ibatis.type;

import java.sql.CallableStatement;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;

/**
 * @author Clinton Begin
 */
public class EnumOrdinalTypeHandler<E extends Enum<E>> extends BaseTypeHandler<E> {

  private final Class<E> type;
  private final E[] enums;

  public EnumOrdinalTypeHandler(Class<E> type) {
    if (type == null) {
      throw new IllegalArgumentException("Type argument cannot be null");
    }
    this.type = type;
    this.enums = type.getEnumConstants();
    if (this.enums == null) {
      throw new IllegalArgumentException(type.getSimpleName() + " does not represent an enum type.");
    }
  }

  @Override
  public void setNonNullParameter(PreparedStatement ps, int i, E parameter, JdbcType jdbcType) throws SQLException {
    ps.setInt(i, parameter.ordinal());
  }

  @Override
  public E getNullableResult(ResultSet rs, String columnName) throws SQLException {
    int ordinal = rs.getInt(columnName);
    if (ordinal == 0 && rs.wasNull()) {
      return null;
    }
    return toOrdinalEnum(ordinal);
  }

  @Override
  public E getNullableResult(ResultSet rs, int columnIndex) throws SQLException {
    int ordinal = rs.getInt(columnIndex);
    if (ordinal == 0 && rs.wasNull()) {
      return null;
    }
    return toOrdinalEnum(ordinal);
  }

  @Override
  public E getNullableResult(CallableStatement cs, int columnIndex) throws SQLException {
    int ordinal = cs.getInt(columnIndex);
    if (ordinal == 0 && cs.wasNull()) {
      return null;
    }
    return toOrdinalEnum(ordinal);
  }

  private E toOrdinalEnum(int ordinal) {
    try {
      return enums[ordinal];
    } catch (Exception ex) {
      throw new IllegalArgumentException("Cannot convert " + ordinal + " to " + type.getSimpleName() + " by ordinal value.", ex);
    }
  }
}
```



## 总结

Mybatis对枚举支持仅限于普通的枚举类型，默认情况下的枚举类型处理类EnumTypeHandler，也可使用序数EnumOrdinalTypeHandler，如果是一个枚举类，可需要自定义。