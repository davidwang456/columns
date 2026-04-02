# mybatis配置文件中resultType和resultMap有什么不同？

## 背景

> 小白：师傅，我在mybatis配置xml文件中配置resultType有一个疑惑：例如，配置resultType=‘int’时，有时返回一个int数据，但有时也可以返回一组int数据。
>
> 扫地僧：在mybatis配置xml文件中，resultType=‘int’仅仅表示你的结果将转换成int类型，具体要返回一个int数组还是一个int值取决于你的mapper接口定义的返回类型。
>
> 小白：我一直以为mapper接口没啥用呢，原来它的功能在这里呀！师傅，能顺便给我讲讲resultType和resultMap有什么不同吗？这个一直让我比较迷惑。
>
> 扫地僧：还是那句老话，代码不会骗人，就让代码来告诉我们吧！



## resultType示例

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

```xml
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>ResultTypeMapTest</artifactId>
  <version>1.8.0-SNAPSHOT</version>
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
package com.davidwang456.mybatis.resulttype;

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
<mapper namespace="com.davidwang456.mybatis.resulttype.StudentMapper">
   <select id="getUserIdList" resultType="int">
   		select id from student
   </select>   
   <select id="getUserCount" resultType="int">
   		select count(id) from student
   </select>
</mapper>
```

**Mapper文件**

```java
package com.davidwang456.mybatis.resulttype;

import java.util.List;

public interface StudentMapper {
	public List<Integer> getUserIdList();
	public Integer getUserCount();
}
```

#### 测试程序

```java
package com.davidwang456.mybatis.resulttype;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class ResultTypeMapTest {

	public static void main(String[] args) throws IOException {
		testID();
	   }
	
	private static void testID() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      
	      Integer cnt=studentMapper.getUserCount();
	      System.out.println("总数cnt="+cnt);
	      List<Integer> ids=studentMapper.getUserIdList();
	      for(Integer id:ids) {
	    	  System.out.println("id="+id);
	      }
	      session.commit(true);
	      session.close();
	}
}
```

打印出结果如下：

```tex
==>  Preparing: select count(id) from student
==> Parameters: 
<==    Columns: count(id)
<==        Row: 8
<==      Total: 1
总数cnt=8
==>  Preparing: select id from student
==> Parameters: 
<==    Columns: id
<==        Row: 1
<==        Row: 2
<==        Row: 3
<==        Row: 4
<==        Row: 5
<==        Row: 6
<==        Row: 7
<==        Row: 8
<==      Total: 8
id=1
id=2
id=3
id=4
id=5
id=6
id=7
id=8
```

和预期结果相符合！可以看到在xml配置文件中，返回的结果都是<resultType="int">,这是怎么回事呢？

#### Mybaits返回不同类型的原理

通过深入到代码内部，找到了根源：MapperMethod.java

```java
  public Object execute(SqlSession sqlSession, Object[] args) {
    Object result;
    switch (command.getType()) {
      case INSERT: {
        Object param = method.convertArgsToSqlCommandParam(args);
        result = rowCountResult(sqlSession.insert(command.getName(), param));
        break;
      }
      case UPDATE: {
        Object param = method.convertArgsToSqlCommandParam(args);
        result = rowCountResult(sqlSession.update(command.getName(), param));
        break;
      }
      case DELETE: {
        Object param = method.convertArgsToSqlCommandParam(args);
        result = rowCountResult(sqlSession.delete(command.getName(), param));
        break;
      }
      case SELECT:
        if (method.returnsVoid() && method.hasResultHandler()) {
          executeWithResultHandler(sqlSession, args);
          result = null;
        } else if (method.returnsMany()) {
          result = executeForMany(sqlSession, args);
        } else if (method.returnsMap()) {
          result = executeForMap(sqlSession, args);
        } else if (method.returnsCursor()) {
          result = executeForCursor(sqlSession, args);
        } else {
          Object param = method.convertArgsToSqlCommandParam(args);
          result = sqlSession.selectOne(command.getName(), param);
          if (method.returnsOptional()
              && (result == null || !method.getReturnType().equals(result.getClass()))) {
            result = Optional.ofNullable(result);
          }
        }
        break;
      case FLUSH:
        result = sqlSession.flushStatements();
        break;
      default:
        throw new BindingException("Unknown execution method for: " + command.getName());
    }
    if (result == null && method.getReturnType().isPrimitive() && !method.returnsVoid()) {
      throw new BindingException("Mapper method '" + command.getName()
          + " attempted to return null from a method with a primitive return type (" + method.getReturnType() + ").");
    }
    return result;
  }
```

当进行查询时，会根据你定义的Mapper.java的返回值类型要调用不同的返回类型。举例：

```java
public Integer getUserCount();
```

它的返回类型为一个，此时会调用DefaultSqlSession.java的selectOne方法：

```java
  @Override
  public <T> T selectOne(String statement, Object parameter) {
    // Popular vote was to return null on 0 results and throw exception on too many.
    List<T> list = this.selectList(statement, parameter);
    if (list.size() == 1) {
      return list.get(0);
    } else if (list.size() > 1) {
      throw new TooManyResultsException("Expected one result (or null) to be returned by selectOne(), but found: " + list.size());
    } else {
      return null;
    }
  }
```

将返回的list的第一个值。也就是说，你定义的mapper接口决定了返回值是list还是一个。

**验证都返回list**

修改mapper接口为：

```java
package com.davidwang456.mybatis.resulttype;

import java.util.List;

public interface StudentMapper {
	public List<Integer> getUserIdList();
	public List<Integer> getUserCount();
}
```

修改测试程序

```java
	      List<Integer> cnt=studentMapper.getUserCount();
	      System.out.println("总数cnt="+cnt.get(0));
```

返回预期的结果。

**验证都返回一个int**

修改mapper接口为：

```java
package com.davidwang456.mybatis.resulttype;

public interface StudentMapper {
	public Integer getUserIdList();
	public Integer getUserCount();
}
```

StudentMapper中返回用户id，此时返回报错：

```java
==>  Preparing: select count(id) from student
==> Parameters: 
<==    Columns: count(id)
<==        Row: 8
<==      Total: 1
总数cnt=8
==>  Preparing: select id from student
==> Parameters: 
<==    Columns: id
<==        Row: 1
<==        Row: 2
<==        Row: 3
<==        Row: 4
<==        Row: 5
<==        Row: 6
<==        Row: 7
<==        Row: 8
<==      Total: 8
Exception in thread "main" org.apache.ibatis.exceptions.TooManyResultsException: Expected one result (or null) to be returned by selectOne(), but found: 8
	at org.apache.ibatis.session.defaults.DefaultSqlSession.selectOne(DefaultSqlSession.java:80)
	at org.apache.ibatis.binding.MapperMethod.execute(MapperMethod.java:87)
	at org.apache.ibatis.binding.MapperProxy$PlainMethodInvoker.invoke(MapperProxy.java:152)
	at org.apache.ibatis.binding.MapperProxy.invoke(MapperProxy.java:85)
	at com.sun.proxy.$Proxy4.getUserIdList(Unknown Source)
	at com.davidwang456.mybatis.resulttype.ResultTypeMapTest.testID(ResultTypeMapTest.java:27)
	at com.davidwang456.mybatis.resulttype.ResultTypeMapTest.main(ResultTypeMapTest.java:16)
```

DefaultSqlSession.java的selectOne方法会检查返回结果是否与mapper定义的接口匹配，不匹配则报错。

## resultType和resultMap级联查询示例

使用上一节<mybatis高级查询：一对多，多对多怎么实现的？>示例：

**使用resultMap**

```xml
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.one2many.StudentQueryDTO" resultMap="studentInfoWithAddress">
		<bind name="condition" value="'%'+keyword+'%'"/>
		select s.id as s_student_id,
			   s.first_name as s_first_name ,
			   s.last_name as s_last_name ,
			   s.age as s_age,
			   s.create_time as s_create_time,
			   s.update_time as s_update_time, 
			   a.id as a_id,
			   a.student_id as a_student_id,
			   a.address_type as a_address_type,
			   a.detail as a_detail
			   from student s
			   left join address a on s.id= a.student_id 
			   where 1=1 
			   <if test="id!=null">
			   and s.id=#{id}
			   </if>
			   <if test="keyword!=null and keyword!=''">
			   and 
			   (s.first_name LIKE #{condition}
			   OR s.last_name LIKE #{condition}
			   )
			   </if>
			  <if test="age!=null and age!=0">
			   and s.age=#{age}
			   </if>
			   <if test="startDate!=null">
			   AND s.create_time > #{startDate}
			   AND s.update_time > #{startDate}
			   </if>
			   <if test="endDate!=null">
			   AND  s.create_time <![CDATA[< #{endDate}]]>
			   AND  s.update_time <![CDATA[< #{endDate}]]>
			   </if>
			   ORDER BY ${sort} ${orderBy}			   		   		  				  
	</select>	
```
打印结果为：

```tex
==>  Preparing: select s.id as s_student_id, s.first_name as s_first_name , s.last_name as s_last_name , s.age as s_age, s.create_time as s_create_time, s.update_time as s_update_time, a.id as a_id, a.student_id as a_student_id, a.address_type as a_address_type, a.detail as a_detail from student s left join address a on s.id= a.student_id where 1=1 and s.id=? and (s.first_name LIKE ? OR s.last_name LIKE ? ) ORDER BY create_time ASC
==> Parameters: 1(Integer), %david%(String), %david%(String)
<==    Columns: s_student_id, s_first_name, s_last_name, s_age, s_create_time, s_update_time, a_id, a_student_id, a_address_type, a_detail
<==        Row: 1, david, www, 25, 2021-06-01 13:56:20, 2021-06-01 13:56:20, 1, 1, 1, china shanghai
<==        Row: 1, david, www, 25, 2021-06-01 13:56:20, 2021-06-01 13:56:20, 2, 1, 1, china beijing
<==      Total: 2
student [id=1, firstName=david, lastName=www, age=25 创建时间：2021-06-01 13:56:20 更新时间：2021-06-01 13:56:20 地址：(china shanghai, china beijing)]
```

符合预期。

**使用resultType**

将返回结果改为resultType，如下所示：

```xml
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.one2many.StudentQueryDTO" resultType="com.davidwang456.mybatis.one2many.StudentDTO">
		<bind name="condition" value="'%'+keyword+'%'"/>
		select s.id,
			   s.first_name,
			   s.last_name,
			   s.age,
			   s.create_time,
			   s.update_time, 
			   a.student_id,
			   a.address_type,
			   a.detail
			   from student s
			   left join address a on s.id= a.student_id 
			   where 1=1 
			   <if test="id!=null">
			   and s.id=#{id}
			   </if>
			   <if test="keyword!=null and keyword!=''">
			   and 
			   (s.first_name LIKE #{condition}
			   OR s.last_name LIKE #{condition}
			   )
			   </if>
			  <if test="age!=null and age!=0">
			   and s.age=#{age}
			   </if>
			   <if test="startDate!=null">
			   AND s.create_time > #{startDate}
			   AND s.update_time > #{startDate}
			   </if>
			   <if test="endDate!=null">
			   AND  s.create_time <![CDATA[< #{endDate}]]>
			   AND  s.update_time <![CDATA[< #{endDate}]]>
			   </if>
			   ORDER BY ${sort} ${orderBy}			   		   		  				  
	</select>	
```

打印结果

```tex
==>  Preparing: select s.id, s.first_name, s.last_name, s.age, s.create_time, s.update_time, a.student_id, a.address_type, a.detail from student s left join address a on s.id= a.student_id where 1=1 and s.id=? and (s.first_name LIKE ? OR s.last_name LIKE ? ) ORDER BY create_time ASC
==> Parameters: 1(Integer), %david%(String), %david%(String)
<==    Columns: id, first_name, last_name, age, create_time, update_time, student_id, address_type, detail
<==        Row: 1, david, www, 25, 2021-06-01 13:56:20, 2021-06-01 13:56:20, 1, 1, china shanghai
<==        Row: 1, david, www, 25, 2021-06-01 13:56:20, 2021-06-01 13:56:20, 1, 1, china beijing
<==      Total: 2
student [id=1, firstName=david, lastName=www, age=25 创建时间：2021-06-01 13:56:20 更新时间：2021-06-01 13:56:20 地址：()]
student [id=1, firstName=david, lastName=www, age=25 创建时间：2021-06-01 13:56:20 更新时间：2021-06-01 13:56:20 地址：()]
```

返回两个结果，不符合预期。

## 总结

使用Mybaits xml配置文件中的resultType和resultMap，需要注意：

- resultType仅仅描述返回的类型，mapper接口定义返回值的多少；

- resultType和resultMap不能同时出现在一个<select>标签中；

- restulyType是大小写不敏感的，而resultMap是大小写敏感的，故需认真核查自己的sql语句，mapper.xml文件是否配置正确；

- 一对多、多对多时，若有表的字段相同必须写别名，否则查询结果无法正常映射；

- resultMap是resultType的升级版，它提供了额外的关联查询。







