# Mybatis支不支持xml+annotation的方式？xml和注解的优缺点？

# 背景

> 小白：师傅，现在有个项目是使用mybatis xml配置方式做的，我想使用注解来做新的需求，不改变旧有的xml方式，这样可行吗？
>
> 扫地僧：mybatis最初配置信息是基于 XML ,映射语句(SQL)也是定义在 XML 中的。而到了 MyBatis 3提供了新的基于注解的配置，在MyBatis 3的版本中是支持XML配置和注解一起使用的。
>
> 扫地僧：虽然Mybatis支持XML配置和注解的方式，但对于一些复杂的功能，官方推荐使用XML配置，原因是：XML的一些功能在注解里还没有完全实现，比如：SQL片段复用。百闻不如一见，我们来个实例吧！

# mybatis注解和xml混合使用实例

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

![image-20210719093249175](img\chapter04-03.png)

#### 添加依赖

pom.xml

```
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>XMLAnotationTest</artifactId>
  <version>4.3.0-SNAPSHOT</version>
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
package com.davidwang456.mybatis.xmlannotation;

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
package com.davidwang456.mybatis.xmlannotation;

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
<mapper namespace="com.davidwang456.mybatis.xmlannotation.StudentMapper">
	<!--  <paging/> -->
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.xmlannotation.StudentQueryDTO" 
	resultType="com.davidwang456.mybatis.xmlannotation.StudentDTO">
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

xml配置形式

```java
package com.davidwang456.mybatis.xmlannotation;

import java.util.List;

public interface StudentMapper {
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
}

```

注解形式

```java
package com.davidwang456.mybatis.xmlannotation;

import java.util.List;

import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.SelectProvider;

public interface StudentAnnotationMapper {
	@Select("select id,first_name,last_name,age from student where id=#{id}")
	public StudentDTO getStudentInfoById(Integer id);
	
	@SelectProvider(type = StudentInfoProvider.class, method = "getStudentById")
	public StudentDTO getStudentById1(Integer id);
	
	@SelectProvider(type = StudentInfoProvider.class, method = "getStudentByCondition")
	public List<StudentDTO> getStudentByIdCondition(@Param("firstName")String firstName,
			@Param("lastName")String lastName);
}

```

其中，也是用了注解@SelectProvider自定义查询方式：

```java
package com.davidwang456.mybatis.xmlannotation;

import java.util.Map;

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
	
	public String getStudentByCondition(Map<String,Object> params) {
		StringBuffer sbf=new StringBuffer();
		sbf.append("select id, first_name,last_name,age from student where 1=1");
		if(params.get("firstName")!=null) {
			sbf.append(" and first_name like '%"+params.get("firstName").toString()+"%'");
		}
		if(params.get("lastName")!=null) {
			sbf.append(" and last_name like '%"+params.get("lastName").toString()+"%'");
		}		
		return sbf.toString();
	}
}
```

使用了两种方式：动态sql构建方式和字符串拼接形式。在多个参数的情况下，使用Map来传递参数。

#### 测试程序

```java
package com.davidwang456.mybatis.xmlannotation;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.Configuration;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class XMLAnotationTest {

	public static void main(String[] args) throws IOException {
		getStudentInfoByCondition();
	   }
	
	private static void getStudentInfoByCondition() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();	
	      
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("wang");
	      param.setLastName("david");
	      param.setOrderBy("DESC");
	      param.setSort("age");	     
          tudentMapper sm=session.getMapper(StudentMapper.class);
          List<StudentDTO> stus=sm.getStudentInfoByCondition(param);
	      printResult(stus,"getStudentInfoByCondition xml query");
	      
	      
	      Configuration conf=session.getConfiguration();
	      conf.addMapper(StudentAnnotationMapper.class);
	      StudentAnnotationMapper studentMapper=session.getMapper(StudentAnnotationMapper.class);
	      List<StudentDTO> stus2=studentMapper.getStudentByIdCondition("wang", "david");
	      printResult(stus2,"getStudentByIdCondition anotation query");	
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
<==        Row: 8, wang8, david8, 30
<==        Row: 7, wang7, david7, 27
<==        Row: 6, wang6, david6, 26
<==        Row: 5, wang5, david5, 25
<==        Row: 4, wang4, david4, 24
<==        Row: 3, wang3, david3, 23
<==        Row: 2, wang2, david2, 22
<==        Row: 1, wang1, david1, 21
<==      Total: 8
------------------getStudentInfoByCondition xml query------------start-----------
student [id=8, firstName=wang8, lastName=david8, age=30]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=1, firstName=wang1, lastName=david1, age=21]
------------------getStudentInfoByCondition xml query------------end----------
==>  Preparing: select id, first_name,last_name,age from student where 1=1 and first_name like '%wang%' and last_name like '%david%'
==> Parameters: 
<==    Columns: id, first_name, last_name, age
<==        Row: 1, wang1, david1, 21
<==        Row: 2, wang2, david2, 22
<==        Row: 3, wang3, david3, 23
<==        Row: 4, wang4, david4, 24
<==        Row: 5, wang5, david5, 25
<==        Row: 6, wang6, david6, 26
<==        Row: 7, wang7, david7, 27
<==        Row: 8, wang8, david8, 30
<==      Total: 8
------------------getStudentByIdCondition anotation query------------start-----------
student [id=1, firstName=wang1, lastName=david1, age=21]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=8, firstName=wang8, lastName=david8, age=30]
------------------getStudentByIdCondition anotation query------------end----------
```

可以看到，在上述的查询语句中同时使用了xml配置和注解的方式，为了区分不同，xml配置方式使用了逆序，注解使用了正序，结果是一致的，符合预期的。

## SQL片段使用实例

只要修改StudentMapper.xml，使用<sql>标签即可：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
        PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.xmlannotation.StudentMapper">
	<sql id="selectColumn">
			select id,
			   first_name ,
			   last_name ,
			   age
			   from student
			   where 1=1
	</sql>
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.xmlannotation.StudentQueryDTO" 
	resultType="com.davidwang456.mybatis.xmlannotation.StudentDTO">
	<bind name="first" value="'%'+firstName+'%'"/>
	<bind name="last"  value="'%'+lastName+'%'"/>
 			<include refid="selectColumn"/>
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

此时，不需要改动其它代码，运行测试，其结果如上面的一致。如果有多个sql，是不是配置少了不少行？



# 深入配置+注解内部原理

上面的实例解决了上面提到了一个问题：mybatis支持不支持xml+annotation的方式呢？如果仅仅到这里，还是不够的，想要了解更多真相只有到源码内部去看：

![img](http://pb3.pstatp.com/large/pgc-image/4d78eb7834c6426888e968160328cb31)



在使用MapperRegistry.addMapper(Class<T> type)添加注解的mapper时，MapperAnnotationBuilder会将xml的配置加载，然后使用XMLMapperBuilder来解读xml配置文件，最后将所有mapper注册到mapperRegistry上。

1.MapperAnnotationBuilder#parse

```java
  public void parse() {
    String resource = type.toString();
    if (!configuration.isResourceLoaded(resource)) {
      loadXmlResource();
      configuration.addLoadedResource(resource);
      assistant.setCurrentNamespace(type.getName());
      parseCache();
      parseCacheRef();
      Method[] methods = type.getMethods();
      for (Method method : methods) {
        try {
          // issue #237
          if (!method.isBridge()) {
            parseStatement(method);
          }
        } catch (IncompleteElementException e) {
          configuration.addIncompleteMethod(new MethodResolver(this, method));
        }
      }
    }
    parsePendingMethods();
  }
```

2.调用loadXmlResource()

```java
  private void loadXmlResource() {
    // Spring may not know the real resource name so we check a flag
    // to prevent loading again a resource twice
    // this flag is set at XMLMapperBuilder#bindMapperForNamespace
    if (!configuration.isResourceLoaded("namespace:" + type.getName())) {
      String xmlResource = type.getName().replace('.', '/') + ".xml";
      // #1347
      InputStream inputStream = type.getResourceAsStream("/" + xmlResource);
      if (inputStream == null) {
        // Search XML mapper that is not in the module but in the classpath.
        try {
          inputStream = Resources.getResourceAsStream(type.getClassLoader(), xmlResource);
        } catch (IOException e2) {
          // ignore, resource is not required
        }
      }
      if (inputStream != null) {
        XMLMapperBuilder xmlParser = new XMLMapperBuilder(inputStream, assistant.getConfiguration(), xmlResource, configuration.getSqlFragments(), type.getName());
        xmlParser.parse();
      }
    }
  }
```

最后调用XMLMapperBuilder的parse方法来解析xml配置文件。

3.XMLMapperBuilder#parse

```java
  public void parse() {
    if (!configuration.isResourceLoaded(resource)) {
      configurationElement(parser.evalNode("/mapper"));
      configuration.addLoadedResource(resource);
      bindMapperForNamespace();
    }

    parsePendingResultMaps();
    parsePendingCacheRefs();
    parsePendingStatements();
  }
```

# 总结

- MyBatis既可以使用xml配置也可以使用注解的方式，也可以两者混用。
- 注解的方式越来越强大，但还是有一些功能只有使用xml配置才能实现，如sql片段重用等。
- 注解方式也可以借助借助 SQL 类实现动态sql构建方式。
- mybatis 3中增加了使用注解来配置Mapper的新特性，其中@Provider的使用方式较为复杂。@provide主要分为四种：@InsertProvider、@DeleteProvider、@UpdateProvider和@SelectProvider，分别对应着sql中的增删改查四种操作。