# like查询会不会走索引？我可以证明给你看！

## 背景

> 小白：师傅，我有一个查询页面慢死，能帮我看看吗？
>
> 扫地僧：通过监控查到哪里有问题吗？
>
> 小白：发现一个查询接口，由于执行了模糊查询，执行很慢！我演示给您看一看。



## like查询实例

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

### 创建maven项目

#### 添加依赖

pom.xml

```
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>LikeTest</artifactId>
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
package com.davidwang456.mybatis.like;

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
package com.davidwang456.mybatis.like;

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
<mapper namespace="com.davidwang456.mybatis.like.StudentMapper">
		<insert id="insertBatchStudentInfo" parameterType="com.davidwang456.mybatis.like.StudentDTO">
		INSERT INTO `student` 
		( `first_name`, `last_name`, `age`) 
		VALUES
    <foreach collection ="dtos" item="dto" separator =",">
         (#{dto.firstName}, #{dto.lastName}, #{dto.age})
    </foreach >
    </insert>
	<select id="getStudentInfoByConditionBoth" parameterType="com.davidwang456.mybatis.like.StudentQueryDTO" 
	resultType="com.davidwang456.mybatis.like.StudentDTO" useCache="false">
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
			  <if test="age!=null and age!=0">
			   and age=#{age}
			   </if>				   		   		  				  
	</select>
	
	<select id="getStudentInfoByConditionRight" parameterType="com.davidwang456.mybatis.like.StudentQueryDTO" 
	resultType="com.davidwang456.mybatis.like.StudentDTO" useCache="false">
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
	</select>
	<select id="getStudentInfoByConditionLeft" parameterType="com.davidwang456.mybatis.like.StudentQueryDTO" 
	resultType="com.davidwang456.mybatis.like.StudentDTO" useCache="false">
	<bind name="first" value="'%'+firstName"/>
	<bind name="last"  value="'%'+lastName"/>
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
	</select>
</mapper>
```

注意：useCache="false"是为了保证每次查询走数据库而不是缓存，可以多次验证实验结果。

**Mapper文件**

```java
package com.davidwang456.mybatis.like;

import java.util.List;

import org.apache.ibatis.annotations.Param;

import com.davidwang456.mybatis.like.StudentDTO;

public interface StudentMapper {
	public Integer insertBatchStudentInfo(@Param("dtos")List<StudentDTO> dtos);
	
	public List<StudentDTO> getStudentInfoByConditionBoth(StudentQueryDTO studentQueryDTO);
	
	public List<StudentDTO> getStudentInfoByConditionLeft(StudentQueryDTO studentQueryDTO);
	
	public List<StudentDTO> getStudentInfoByConditionRight(StudentQueryDTO studentQueryDTO);

}
```

#### 测试程序

**初始化数据(100w)**

批量导入数据

```java
	public static void initData() throws IOException {
		  Random random = new Random();
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession(ExecutorType.BATCH,false);      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      
	      Long start=System.currentTimeMillis();
	      List<StudentDTO> dtos=null;
	      int seq=0;
	      List<String> departmentList=new ArrayList<>();
	      departmentList.add("rearch");
	      departmentList.add("develop");
	      departmentList.add("product");
	      departmentList.add("mark");
	      departmentList.add("other");
	      String prex="";
	      for(int i=0;i<1000;i++) {
	    	  prex=departmentList.get(random.nextInt(departmentList.size()));
	    	  dtos=new ArrayList<StudentDTO>();
	    	  for(int j=0;j<1000;j++) {
	    		  seq=i*1000+j+1;
			      StudentDTO dto=new StudentDTO();
			      dto.setFirstName(prex+":www:"+seq);
			      dto.setLastName(prex+":david:"+seq);
			      dto.setAge(20+seq%10);
			      dtos.add(dto);  
	    	  }
	    	  studentMapper.insertBatchStudentInfo(dtos);
	    	  dtos=null;
	      }
		  
	      session.commit(true);
	      long end = System.currentTimeMillis();
	      System.out.println("------initData-------" + (start - end) + "ms---------------");
	      session.close();	
	}
```

显示导入耗时约为12.6秒

```tex
------initData--------12576ms---------------
```

**创建索引**

```mysql
ALTER TABLE student ADD INDEX idx_first_last_name (first_name,last_name);
```

**模糊查询**

测试程序：

```java
		private static void testBoth() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("%rearch:www:100%");
	      param.setLastName("%rearch:david:100%");
	      param.setOrderBy("DESC");
	      param.setSort("age");
	      Long start=System.currentTimeMillis();
	      List<StudentDTO> stus=studentMapper.getStudentInfoByConditionBoth(param);
	      System.out.println("testBoth cost:"+(System.currentTimeMillis()-start)+" ms,fetch size:"+stus.size());
	      session.commit(true);
	      session.close();
	}
```

打印输出结果：

```tex
==>  Preparing: select id, first_name , last_name , age from student where 1=1 and first_name like ? and last_name like ?
==> Parameters: %rearch:www:100%(String), %rearch:david:100%(String)
<==    Columns: id, first_name, last_name, age
<==        Row: 1001, rearch:www:1001, rearch:david:1001, 21
<==        Row: 1002, rearch:www:1002, rearch:david:1002, 22
<==        Row: 1003, rearch:www:1003, rearch:david:1003, 23
<==        Row: 1004, rearch:www:1004, rearch:david:1004, 24
<==        Row: 1005, rearch:www:1005, rearch:david:1005, 25
<==        Row: 1006, rearch:www:1006, rearch:david:1006, 26
<==        Row: 1007, rearch:www:1007, rearch:david:1007, 27
<==        Row: 1008, rearch:www:1008, rearch:david:1008, 28
<==        Row: 1009, rearch:www:1009, rearch:david:1009, 29
<==      Total: 9
testBoth cost:575 ms,fetch size:9
```

## like查询优化

> 扫地僧：根据你的代码来看，接口慢的原因是使用了模糊查询，没有走创建好的索引，导致查询很慢！
>
> 小白：可以模糊查询不走索引，有没有办法优化？
>
> 扫地僧：模糊查询不走索引，这句话本身就不对。我来给你分析分析！

先看你的模糊，根据执行的sql，去mysql客户端查看执行计划：

```mysql
EXPLAIN SELECT id, first_name , last_name , age FROM student WHERE 1=1 AND first_name LIKE '%rearch:www:100%' AND last_name LIKE '%rearch:david:100%';
```

执行计划完整内容：

```tex
    id  select_type  table    partitions  type    possible_keys  key     key_len  ref       rows  filtered  Extra        
------  -----------  -------  ----------  ------  -------------  ------  -------  ------  ------  --------  -------------
     1  SIMPLE       student  (NULL)      ALL     (NULL)         (NULL)  (NULL)   (NULL)  989498    100.00  Using where 
```

从type,实际使用的索引key和数据集rows上来看，刚才的查询称之为双边模糊查询，没有走索引idx_first_last_name (first_name,last_name)。

我们尝试另外两种模糊查询：

**左边模糊查询**

测试程序:

```java
	private static void testLeft() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("rearch:www:100");
	      param.setLastName("rearch:david:100");
	      param.setOrderBy("DESC");
	      param.setSort("age");
	      Long start=System.currentTimeMillis();
	      List<StudentDTO> stus=studentMapper.getStudentInfoByConditionLeft(param);
	      System.out.println("testLeft cost:"+(System.currentTimeMillis()-start)+"ms,fetch size："+stus.size());
	      session.commit(true);
	      session.close();
	}
```

打印输出结果：

```tex
==>  Preparing: select id, first_name , last_name , age from student where 1=1 and first_name like ? and last_name like ?
==> Parameters: %rearch:www:100(String), %rearch:david:100(String)
<==      Total: 0
testLeft cost:563ms,fetch size：0
```

根据执行的sql，去mysql客户端查看执行计划：

```mysql
EXPLAIN SELECT id, first_name , last_name , age FROM student WHERE 1=1 AND first_name LIKE '%rearch:www:100' AND last_name LIKE '%rearch:david:100';
```

执行计划完整内容：

```tex
   id  select_type  table    partitions  type    possible_keys  key     key_len  ref       rows  filtered  Extra        
------  -----------  -------  ----------  ------  -------------  ------  -------  ------  ------  --------  -------------
     1  SIMPLE       student  (NULL)      ALL     (NULL)         (NULL)  (NULL)   (NULL)  989498    100.00  Using where  
```

从type,实际使用的索引key和数据集rows上来看，没有走索引idx_first_last_name (first_name,last_name)。

**右边模糊查询**

测试程序：

```java
	private static void testRight() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("rearch:www:100");
	      param.setLastName("rearch:david:100");
	      param.setOrderBy("DESC");
	      param.setSort("age");
	      Long start=System.currentTimeMillis();
	      List<StudentDTO> stus=studentMapper.getStudentInfoByConditionRight(param);
	      System.out.println("testRight cost:"+(System.currentTimeMillis()-start)+"ms,fetch size："+stus.size());
	      session.commit(true);
	      session.close();
	}
```

执行结果如下：

```tex
==>  Preparing: select id, first_name , last_name , age from student where 1=1 and first_name like concat(?,"%") and last_name like concat(?,"%")
==> Parameters: rearch:www:100(String), rearch:david:100(String)
<==    Columns: id, first_name, last_name, age
<==        Row: 1001, rearch:www:1001, rearch:david:1001, 21
<==        Row: 1002, rearch:www:1002, rearch:david:1002, 22
<==        Row: 1003, rearch:www:1003, rearch:david:1003, 23
<==        Row: 1004, rearch:www:1004, rearch:david:1004, 24
<==        Row: 1005, rearch:www:1005, rearch:david:1005, 25
<==        Row: 1006, rearch:www:1006, rearch:david:1006, 26
<==        Row: 1007, rearch:www:1007, rearch:david:1007, 27
<==        Row: 1008, rearch:www:1008, rearch:david:1008, 28
<==        Row: 1009, rearch:www:1009, rearch:david:1009, 29
<==      Total: 9
testRight cost:226ms,fetch size：9
```

根据执行的sql，去mysql客户端查看执行计划：

```mysql
EXPLAIN SELECT id, first_name , last_name , age FROM student WHERE 1=1 AND first_name LIKE 'rearch:www:100%' AND last_name LIKE 'rearch:david:100%';
```

执行计划完整内容：

```tex
    id  select_type  table    partitions  type    possible_keys        key                  key_len  ref       rows  filtered  Extra                  
------  -----------  -------  ----------  ------  -------------------  -------------------  -------  ------  ------  --------  -----------------------
     1  SIMPLE       student  (NULL)      range   idx_first_last_name  idx_first_last_name  806      (NULL)       9    100.00  Using index condition  
```

从type,实际使用的索引key和数据集rows上来看，查询走了创建的索引idx_first_last_name (first_name,last_name)。

在百万级别的数据情况下：双边模糊查询和左边模糊查询并没有走创建的索引，耗时约为570ms，但右边模糊查询走了索引，耗时仅为220毫秒，性能提升达到2.5倍。

## 全文索引查询 vs 模糊查询

> 小白：听说现在的Mysql支持全文索引查询，这种查询方式是不是可以替换掉like查询？
>
> 扫地僧：全文索引查询和like查询是不同，使用SQL  LIKE操作符可以提供100%的精度，可能非常低效；全文搜索功能牺牲精度，以便更好更快的提供服务，特别是大数据量的情况下表现优异。比如我们使用es(elasticsearch)或者solr等来对亿级别的数据进行查询。

**Mysql全文索引实例**

语法格式：

```tex
MATCH (col1,col2,...) AGAINST (expr [search_modifier])
```

其中：

```tex
search_modifier:
  {
       IN NATURAL LANGUAGE MODE
     | IN NATURAL LANGUAGE MODE WITH QUERY EXPANSION
     | IN BOOLEAN MODE
     | WITH QUERY EXPANSION
  }
```

**实例**


```mysql
DROP TABLE IF EXISTS  student;
CREATE TABLE `student` (
  `id` INT(11) NOT NULL AUTO_INCREMENT,
  `first_name` VARCHAR(100) DEFAULT NULL,
  `last_name` VARCHAR(100) DEFAULT NULL,
  `age` INT(11) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=INNODB DEFAULT CHARSET=utf8mb4;

ALTER TABLE `student` ADD FULLTEXT INDEX idx_first_name(`first_name`);


INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (1, 'wan g1 like 555', 'david1', 21);
INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (2, 'wan g2 dd 55', 'david2', 22);
INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (3, 'wan g3 ff 33', 'david3', 23);
INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (4, 'wan g4 ss 44', 'david4', 24);
INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (5, 'wan g5 dd 44', 'david5', 25);
INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (6, 'wan g6  like 5', 'david6', 26);
INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (7, 'wan g7 ss 5', 'david7', 27);
INSERT INTO `student` (`id`, `first_name`, `last_name`, `age`) VALUES (8, 'wan g8 eec 22', 'david8', 28);

SELECT * FROM student WHERE MATCH(first_name) AGAINST('like');
```

结果展示

    id  first_name       last_name     age  
------  ---------------  ---------  --------
```tex
 1  wan g1 like 555  david1           21
 6  wan g6  like 5   david6           26
```


## 总结

模糊查询也是数据库SQL中使用频率很高的SQL语句，使用MyBatis来进行更加灵活的模糊查询。

**1.mybatis实现like查询的三种方式**

- 直接传参法，就是将要查询的关键字keyword,在代码中拼接好要查询的格式，如%keyword%,然后直接作为参数传入mapper.xml的映射文件中。
- CONCAT()函数法，利用MySQL的 CONCAT()函数用于将多个字符串连接成一个字符串，是最重要的mysql函数之一。
- 标签bind动态绑定，使用mybatis自带的bind绑定方式，如上文所示。

**2.mysql支持前缀索引**

前缀索引在多个列索引情况下，如上文的idx_first_last_name (first_name,last_name)，使用AND时索引是生效的，但使用or连接时则索引不起作用，比较一下AND和OR查询时的执行计划：

**AND查询情况**

```mysql
EXPLAIN SELECT id, first_name , last_name , age FROM student 
WHERE first_name LIKE 'rearch:david:100%' AND last_name LIKE 'rearch:david:100%';
```

执行计划：

    id  select_type  table    partitions  type    possible_keys        key                  key_len  ref       rows  filtered  Extra                  
------  -----------  -------  ----------  ------  -------------------  -------------------  -------  ------  ------  --------  -----------------------
     1  SIMPLE       student  (NULL)      range   idx_first_last_name  idx_first_last_name  806      (NULL)       1    100.00  Using index condition  

注意：type=range，key=idx_first_last_name(实际使用的索引)和数据集rows=806

**OR查询情况**

```mysql
EXPLAIN SELECT id, first_name , last_name , age FROM student 
WHERE first_name LIKE 'rearch:david:100%' OR last_name LIKE 'rearch:david:100%';
```

执行计划：

```tex
    id  select_type  table    partitions  type    possible_keys        key     key_len  ref       rows  filtered  Extra        
------  -----------  -------  ----------  ------  -------------------  ------  -------  ------  ------  --------  -------------
     1  SIMPLE       student  (NULL)      ALL     idx_first_last_name  (NULL)  (NULL)   (NULL)  989498    100.00  Using where  
```

注意：type=ALL，实际使用的索引是Null，和数据集rows=989498

**3.  mysql的全文索引**

​    旧版的MySQL的全文索引只能用在MyISAM表格的char、varchar和text的字段上。 新版的MySQL5.6.24上InnoDB引擎也加入了全文索引。Mysql的全文搜索查询并不十分友好，建议在生产环境使用专业的搜索引擎如elasticsearch或者solr等。