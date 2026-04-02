# 动态排序什么实现？too young too naive！

## 背景

> 小白：师傅，产品经理要实现一个表的动态排序，如下面的图片所示：
>
> <img src="img\dynamic.png" alt="dynamic" style="zoom:50%;" />
>
> 可以根据年龄大小排序，也可以根据名字或者id动态排序的需求，我拿着执行打印的sql，可是我使用order by进行排序都没有生效。能帮忙看一下怎么回事吗？
>
> 扫地僧：那让我看看你的程序吧.
>
> 小白：我介绍一下我的实现方式您看看!
>

## 多字段动态排序实现

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
  <artifactId>DynamicSortTest</artifactId>
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
package com.davidwang456.mybatis.dynamicsort;

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
package com.davidwang456.mybatis.dynamicsort;

import lombok.Data;

@Data
public class StudentQueryDTO {
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	private String keyword;
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
<mapper namespace="com.davidwang456.mybatis.dynamicsort.StudentMapper">
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.dynamicsort.StudentQueryDTO" resultType="com.davidwang456.mybatis.dynamicsort.StudentDTO">	
		<bind name="condition" value="'%'+keyword+'%'"/>
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
			   	and first_name like #{firstName}
			   </if>
			   <if test="lastName!=null and lastName!=''">
			   	and last_name like #{lastName}
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
			   ORDER BY #{sort} #{orderBy}				   		   		  				  
	</select>
</mapper>
```

**Mapper文件**

```java
package com.davidwang456.mybatis.dynamicsort;

import java.util.List;

public interface StudentMapper {
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
}
```

#### 测试程序

```java
package com.davidwang456.mybatis.dynamicsort;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class DynamicSortTest {

	public static void main(String[] args) throws IOException {
		testID();
	   }
	
	private static void testID() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setKeyword("david");
	      param.setOrderBy("DESC");
	      param.setSort("id");
	      Long start=System.currentTimeMillis();
	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition(param);
	      for(StudentDTO dto:stus) {
	    	  System.out.println(dto.toString());
	      }
	      
	      System.out.println("testBoth cost:"+(System.currentTimeMillis()-start)+" ms,fetch size:"+stus.size());
	      session.commit(true);
	      session.close();
	}
}
```

打印出结果如下：

```tex
==>  Preparing: select id, first_name , last_name , age from student where 1=1 and (first_name LIKE ? OR last_name LIKE ? ) ORDER BY ? ?
==> Parameters: %david%(String), %david%(String), id(String), DESC(String)
<==    Columns: id, first_name, last_name, age
<==        Row: 1, wang1, david1, 21
<==        Row: 2, wang2, david2, 22
<==        Row: 3, wang3, david3, 23
<==        Row: 4, wang4, david4, 24
<==        Row: 5, wang5, david5, 25
<==        Row: 6, wang6, david6, 26
<==        Row: 7, wang7, david7, 27
<==        Row: 8, wang8, david8, 28
<==      Total: 8
student [id=1, firstName=wang1, lastName=david1, age=21]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=8, firstName=wang8, lastName=david8, age=28]
testBoth cost:217 ms,fetch size:8
```

和我预期的结果不符合！

但是我根据打印的sql，在客户端执行

```mysql
SELECT id, first_name , last_name , age FROM student WHERE 1=1 AND (first_name LIKE '%david%' OR last_name LIKE '%david%' ) ORDER BY id DESC;
```

查看结果如下：

```tex
    id  first_name  last_name     age  
------  ----------  ---------  --------
     8  wang8       david8           28
     7  wang7       david7           27
     6  wang6       david6           26
     5  wang5       david5           25
     4  wang4       david4           24
     3  wang3       david3           23
     2  wang2       david2           22
     1  wang1       david1           21
```

和预期结果相符合！这是怎么回事呢？

## Mybatis如何实现动态排序？

> 扫地僧：#{sort} #{orderBy} 的值是字符串，但id是Integer类型，DESC是枚举值；故这样传递是不合适的！
>
> 小白：我明白了，#{sort} #{orderBy} 字符串的值不能直接赋给order by的条件，那么只要对条件进行判断，即可。
>
> 果然，我把order by改成这样，就完全ok了！
>
> ```xml
> 			  <choose>
> 			  	<when test="sort=='id' and orderBy=='ASC'">
> 			  	ORDER BY id ASC
> 			  	</when>
> 			  	<when test="sort=='id' and orderBy=='DESC'">
> 			  	ORDER BY id DESC
> 			  	</when>                  
> 			  	<when test="sort=='first_name' and orderBy=='ASC'">
> 			  	ORDER BY first_name ASC
> 			  	</when>
> 			  	<when test="sort=='first_name' and orderBy=='DESC'">
> 			  	ORDER BY first_name DESC
> 			  	</when>
> 			  	<when test="sort=='last_name' and orderBy=='ASC'">
> 			  	ORDER BY last_name ASC
> 			  	</when>
> 			  	<when test="sort=='last_name' and orderBy=='DESC'">
> 			  	ORDER BY last_name DESC
> 			  	</when>
> 			  	<when test="sort=='age' and orderBy=='ASC'">
> 			  	ORDER BY age ASC
> 			  	</when>
> 			  	<when test="sort=='age' and orderBy=='DESC'">
> 			  	ORDER BY age DESC
> 			  	</when>
> 			  </choose>	
> ```
>
> 此时测试程序返回结果符合预期：
>
> ```tex
> ==>  Preparing: select id, first_name , last_name , age from student where 1=1 and (first_name LIKE ? OR last_name LIKE ? ) ORDER BY id DESC
> ==> Parameters: %david%(String), %david%(String)
> <==    Columns: id, first_name, last_name, age
> <==        Row: 8, wang8, david8, 28
> <==        Row: 7, wang7, david7, 27
> <==        Row: 6, wang6, david6, 26
> <==        Row: 5, wang5, david5, 25
> <==        Row: 4, wang4, david4, 24
> <==        Row: 3, wang3, david3, 23
> <==        Row: 2, wang2, david2, 22
> <==        Row: 1, wang1, david1, 21
> <==      Total: 8
> student [id=8, firstName=wang8, lastName=david8, age=28]
> student [id=7, firstName=wang7, lastName=david7, age=27]
> student [id=6, firstName=wang6, lastName=david6, age=26]
> student [id=5, firstName=wang5, lastName=david5, age=25]
> student [id=4, firstName=wang4, lastName=david4, age=24]
> student [id=3, firstName=wang3, lastName=david3, age=23]
> student [id=2, firstName=wang2, lastName=david2, age=22]
> student [id=1, firstName=wang1, lastName=david1, age=21]
> testBoth cost:235 ms,fetch size:8
> ```
>
> 扫地僧：这种方式确实可以解决目前的问题，但如果要排序的字段扩展到几十个，上百个的话你的工作量可不轻哦！那如何才能以最少的程序完成更多的事情呢？你记不记得以前我和你讲过#{}和${}区别？忘记也没有关系。我们将刚才的chose...when去掉，order by 改成这样：
>
> ```xml
> ORDER BY ${sort} ${orderBy}
> ```
>
> 因为${sort}是占位符，不区分类型，这样程序的查询就可以达到预期结果了，程序量大大减少了，后期新增排序字段也无需修改了。
>
> 小白：师傅，这种方法太棒了，但是不是存在一个问题？我sort和order by字段可以随意填写，会不会出问题？
>
> 扫地僧：你的担忧是非常对的，这个问题也有解决方式：通过对输入做校验，不让非法字符进来即可。比如输入校验：
>
> ```java
> import javax.validation.constraints.Min;
> 
> import javax.validation.constraints.Pattern;
> 
> import org.hibernate.validator.constraints.Length;
> 
> import io.swagger.annotations.ApiModelProperty;
> 
> import lombok.Data;
> 
> @Data
> 
> public class StudentQueryDTO {
> 
> //字段
> @ApiModelProperty(name="id",value="自增主键",dataType="int")
> private Integer id;
> 
> @ApiModelProperty(name="firstName",value="名",dataType="String")
> @Length(max=100,message="名长度必须小于100")
> private String firstName;
> 
> @ApiModelProperty(name="lastName",value="姓",dataType="String")
> @Length(max=100,message="名长度必须小于100")
> private String lastName;
> 
> @Min(value=1,message="年龄不能小于1")
> private Integer age;
> 
> //关键词查:依据firstName和lastName
> @ApiModelProperty(name="firstName",value="名",dataType="String")
> private String keyword;
> 
> //排序项目
> @Pattern(regexp = "^(|id|first_name|last_name|age)$")
> private String sort;
> 
> 
> //排序 DESC|ASC
> @Pattern(regexp = "^(|ASC|DESC)$")
> private String orderBy;
> 
> }
> ```

## 总结

Mybatis实现数据库表的字段排序有两种方式：

1.使用<chose> .... <when> 按照排序字段和顺序进行整合order by 语句

2.使用order by 占位符${sort} ${orderBy} **注意**：此时必须限制传入的参数即需要先做参数校验，然后才能使用这些字段。

**注意**：使用ordery by 替换符#{sort} #{orderBy}无法完成排序功能，原因：1. 排序字段是有类型，必须满足类型 2.顺序 ASC或者DESC在mysql里是枚举值，不能识别字符串。