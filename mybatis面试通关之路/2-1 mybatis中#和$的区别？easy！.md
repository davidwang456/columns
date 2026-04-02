# Mybatis中#和$的区别？easy！

> 小白：师傅，经常再面试时被问到：Mybatis中#和$的区别？可是在开发中，经常使用#{}，很少使用到${}.
>
> 在面试时由于不是很理解，往往靠记忆来应付面试，您有什么办法让初学者很容易的区分清楚呢？
>
> 扫地僧：靠临时抱佛脚肯定不行，夯实基础都是平时老老实实的积累。Mybatis中#和$的区别很简单，#{label}是替换成具体的值，即需要有getLabel()方法去获取值；${label}是占位符，替换成键；总结一句话就可以说明：
>
> #{}是值，${}是键。
>
> 扫地僧：为了更好理解它们的区别，我们代码上见吧！

## Mybatis中#和$使用示例

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
```

#### 实体

**数据库实体**

```java
package com.davidwang456.mybatis.SubSymbol;
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
package com.davidwang456.mybatis.SubSymbol;
import lombok.Data;
@Data
public class StudentQueryDTO {
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	//关键词查询,依据firstName和lastName
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
		<setting name="logImpl" value="JDK_LOGGING"/>
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
<mapper namespace="com.davidwang456.mybatis.SubSymbol.StudentMapper">
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.SubSymbol.StudentQueryDTO" resultType="com.davidwang456.mybatis.SubSymbol.StudentDTO">
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
			   <if test="keyword!=null and keyword!=''">
			   and 
			   (first_name LIKE #{condition}
			   OR last_name LIKE #{condition}
			   )
			   </if>
			  <if test="age!=null and age!=0">
			   and age=#{age}
			   </if>	
			   ORDER BY ${sort} ${orderBy}			   		   		  				  
	</select>
</mapper>
```

**Mapper文件**

```java
package com.davidwang456.mybatis.SubSymbol;
import java.util.List;
public interface StudentMapper {
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
}
```

#### 测试程序

```java
package com.davidwang456.mybatis.SubSymbol;

import java.io.IOException;
import java.io.Reader;
import java.util.List;
import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;

public class SubSymbolTest {

	public static void main(String[] args) throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setKeyword("david");	
	      param.setOrder("DESC");
	      param.setSort("age");
	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition(param);
	      for(StudentDTO stu:stus) {
	      System.out.println(stu.toString());
	      }
	      session.commit(true);
	      session.close();				
	   }
}
```

运行结果：

```java
student [id=8, firstName=wang8, lastName=david8, age=28]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=1, firstName=wang1, lastName=david1, age=21]
```

符合预期！

如果把**Mybatis映射文件配置**中order by后面的${}换成#{}，结果是什么样子的？

```xml
ORDER BY #{sort} #{orderBy}
```

运行测试程序，结果：

```java
student [id=1, firstName=wang1, lastName=david1, age=21]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=8, firstName=wang8, lastName=david8, age=28]
```

发现排序没有起作用！

原因：#{sort} #{orderBy} 返回值是字符串，但age是Integer，DESC是枚举值；但${sort} ${orderBy}直接占位，不考虑值的问题。

如果不用${sort} ${orderBy}，则需要改造成如下的方式：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
        PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.SubSymbol.StudentMapper">
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.SubSymbol.StudentQueryDTO" resultType="com.davidwang456.mybatis.SubSymbol.StudentDTO">
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
			  <choose>
			  	<when test="sort=='first_name' and orderBy=='ASC'">
			  	ORDER BY first_name ASC
			  	</when>
			  	<when test="sort=='first_name' and orderBy=='DESC'">
			  	ORDER BY first_name DESC
			  	</when>
			  	<when test="sort=='last_name' and orderBy=='ASC'">
			  	ORDER BY last_name ASC
			  	</when>
			  	<when test="sort=='last_name' and orderBy=='DESC'">
			  	ORDER BY last_name DESC
			  	</when>
			  	<when test="sort=='age' and orderBy=='ASC'">
			  	ORDER BY age ASC
			  	</when>
			  	<when test="sort=='age' and orderBy=='DESC'">
			  	ORDER BY age DESC
			  	</when>
			  </choose>				   		   		  				  
	</select>
</mapper>
```

运行程序，结果：

```java
student [id=8, firstName=wang8, lastName=david8, age=28]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=1, firstName=wang1, lastName=david1, age=21]
```

符合预期。但会大大增大代码的工作量。

> 小白：Mybatis处理${}和#{}的逻辑是不是相同的？它们的处理逻辑是什么？
>
> 扫地僧：Mybatis处理${}和#{}的逻辑是不是相同的：
>
> （1）mybatis在处理#{}时，会将sql中的#{}替换为?号，调用PreparedStatement的set方法来赋值。
>
> （2）mybatis在处理${}时，就是把${}替换成变量的值。
>
> 秉承着代码不会撒谎的态度，我们深入到Mybatis源码来看看吧！

## Mybatis处理${}和#{}的原理

### #{}处理原理

通过单步调试，发现处理#{}的代码在SqlSourceBuilder.java的parse()方法：

```java
  public SqlSource parse(String originalSql, Class<?> parameterType, Map<String, Object> additionalParameters) {
    ParameterMappingTokenHandler handler = new ParameterMappingTokenHandler(configuration, parameterType, additionalParameters);
    GenericTokenParser parser = new GenericTokenParser("#{", "}", handler);
    String sql = parser.parse(originalSql);
    return new StaticSqlSource(configuration, sql, handler.getParameterMappings());
  }
```

上面的代码可以看出，处理#{}时调用了ParameterMappingTokenHandler的handleToken()方法：

```java
    @Override
    public String handleToken(String content) {
      parameterMappings.add(buildParameterMapping(content));
      return "?";
    }
```

mybatis在处理#{}时，会将sql中的#{}替换为?号，调用PreparedStatement的set方法来赋值。

通过归纳调用链路，我们可以完整的看到整个过程：

```java
调用序号：1  调用类和方法 com.davidwang456.mybatis.SubSymbol.SubSymbolTest$main
调用序号：2  调用类和方法 org.apache.ibatis.session.SqlSessionFactoryBuilder$build
调用序号：3  调用类和方法 org.apache.ibatis.session.SqlSessionFactoryBuilder$build
调用序号：4  调用类和方法 org.apache.ibatis.builder.xml.XMLConfigBuilder$parse
调用序号：5  调用类和方法 org.apache.ibatis.builder.xml.XMLConfigBuilder$parseConfiguration
调用序号：6  调用类和方法 org.apache.ibatis.builder.xml.XMLConfigBuilder$mapperElement
调用序号：7  调用类和方法 org.apache.ibatis.builder.xml.XMLMapperBuilder$parse
调用序号：8  调用类和方法 org.apache.ibatis.builder.xml.XMLMapperBuilder$configurationElement
调用序号：9  调用类和方法 org.apache.ibatis.builder.xml.XMLMapperBuilder$buildStatementFromContext
调用序号：10  调用类和方法 org.apache.ibatis.builder.xml.XMLMapperBuilder$buildStatementFromContext
调用序号：11  调用类和方法 org.apache.ibatis.builder.xml.XMLStatementBuilder$parseStatementNode
调用序号：12  调用类和方法 org.apache.ibatis.scripting.xmltags.XMLLanguageDriver$createSqlSource
调用序号：13  调用类和方法 org.apache.ibatis.scripting.xmltags.XMLScriptBuilder$parseScriptNode
调用序号：14  调用类和方法 org.apache.ibatis.scripting.defaults.RawSqlSource$<init>
调用序号：15  调用类和方法 org.apache.ibatis.scripting.defaults.RawSqlSource$<init>
调用序号：16  调用类和方法 org.apache.ibatis.builder.SqlSourceBuilder$parse
调用序号：17  调用类和方法 org.apache.ibatis.parsing.GenericTokenParser$parse
调用序号：18  调用类和方法 org.apache.ibatis.builder.SqlSourceBuilder$ParameterMappingTokenHandler$handleToken
```

从上面的逻辑可以看出，#{}的处理逻辑是在预编译时。

### ${}处理逻辑

通过单步调试，发现处理${}的代码在TextSqlNode.java的apply()方法：

```JAVA
  @Override
  public boolean apply(DynamicContext context) {
    GenericTokenParser parser = createParser(new BindingTokenParser(context, injectionFilter));
    context.appendSql(parser.parse(text));
    return true;
  }

  private GenericTokenParser createParser(TokenHandler handler) {
    return new GenericTokenParser("${", "}", handler);
  }
```

上面的代码可以看出，处理#{}时调用了BindingTokenParser的handleToken()方法：

```java
    @Override
    public String handleToken(String content) {
      Object parameter = context.getBindings().get("_parameter");
      if (parameter == null) {
        context.getBindings().put("value", null);
      } else if (SimpleTypeRegistry.isSimpleType(parameter.getClass())) {
        context.getBindings().put("value", parameter);
      }
      Object value = OgnlCache.getValue(content, context.getBindings());
      String srtValue = value == null ? "" : String.valueOf(value); // issue #274 return "" instead of "null"
      checkInjection(srtValue);
      return srtValue;
    }

    private void checkInjection(String value) {
      if (injectionFilter != null && !injectionFilter.matcher(value).matches()) {
        throw new ScriptingException("Invalid input. Please conform to regex" + injectionFilter.pattern());
      }
    }
```

mybatis在处理${}时，就是把${}替换成变量的值.

通过归纳调用链路，我们可以完整的看到整个过程：

```java
调用序号：1  调用类和方法 com.davidwang456.mybatis.SubSymbol.SubSymbolTest$main
调用序号：2  调用类和方法 com.sun.proxy.$Proxy0$getStudentInfoByCondition
调用序号：3  调用类和方法 org.apache.ibatis.binding.MapperProxy$invoke
调用序号：4  调用类和方法 org.apache.ibatis.binding.MapperProxy$PlainMethodInvoker$invoke
调用序号：5  调用类和方法 org.apache.ibatis.binding.MapperMethod$execute
调用序号：6  调用类和方法 org.apache.ibatis.binding.MapperMethod$executeForMany
调用序号：7  调用类和方法 org.apache.ibatis.session.defaults.DefaultSqlSession$selectList
调用序号：8  调用类和方法 org.apache.ibatis.session.defaults.DefaultSqlSession$selectList
调用序号：9  调用类和方法 org.apache.ibatis.executor.CachingExecutor$query
调用序号：10  调用类和方法 org.apache.ibatis.mapping.MappedStatement$getBoundSql
调用序号：11  调用类和方法 org.apache.ibatis.scripting.xmltags.DynamicSqlSource$getBoundSql
调用序号：12  调用类和方法 org.apache.ibatis.scripting.xmltags.MixedSqlNode$apply
调用序号：13  调用类和方法 java.util.ArrayList$forEach
调用序号：14  调用类和方法 org.apache.ibatis.scripting.xmltags.MixedSqlNode$$Lambda$22/1375995437$accept
调用序号：15  调用类和方法 org.apache.ibatis.scripting.xmltags.MixedSqlNode$lambda$0
调用序号：16  调用类和方法 org.apache.ibatis.scripting.xmltags.TextSqlNode$apply
调用序号：17  调用类和方法 org.apache.ibatis.parsing.GenericTokenParser$parse
调用序号：18  调用类和方法 org.apache.ibatis.scripting.xmltags.TextSqlNode$BindingTokenParser$handleToken
```

从调用链路来看，处理${}的逻辑是在运行查询时。

> 小白：听说${}不安全，容易产生sql注入，#{}不会产生sql注入，这是真的吗？
>
> 扫地僧：这个是有一定道理的，[mybatis官方文档](https://mybatis.org/mybatis-3/sqlmap-xml.html)有个注意事项：
>
> **NOTE** It's not safe to accept input from a user and supply it to a statement unmodified in this way. This leads to potential SQL Injection attacks and therefore you should either disallow user input in these fields, or always perform your own escapes and checks.
>
> 大意就是：注意，直接接受用户的输入并将其提供给sql执行语句是不安全的，这将导致潜在的SQL注入攻击。因此您应该不允许用户直接输入这些字段(使用枚举让用户选择)，或者始终执行自己的转义和检查。
>
> 其实，在刚才的${}的处理逻辑中，有一个checkInjection()方法，使用injectionFilter来过滤sql注入问题，通过追踪代码，可以看到2014年7月16号加入的Fixes #117. Sql injection filter for ${} expressions，但仅仅是Mybatis预留的接口还没有实现，很期望这个尽快实现。
>
> 小白：既然${}会引起sql注入，为什么有了#{}还需要有${}呢？那其存在的意义是什么？
>
> 扫地僧：${}其实用处蛮多：1.上面的ORDER BY ${columnName} ${order};2.当元数据(即:表名或列名)在sql语句中是动态的，例如，如果你想从一个表中选择它的任何一列，而不是编写代码:
>
> ```java
> @Select("select * from user where id = #{id}")
> User findById(@Param("id") long id);
> 
> @Select("select * from user where name = #{name}")
> User findByName(@Param("name") String name);
> 
> @Select("select * from user where email = #{email}")
> User findByEmail(@Param("email") String email);
> 
> // and more "findByXxx" method
> ```
>
> 改成这样写：
>
> ```java
> @Select("select * from user where ${column} = #{value}")
> User findByColumn(@Param("column") String column, @Param("value") String value);
> ```
>
> 是不是简化了很多？
>
> 小白：师傅，听您这番讲解，我觉得下次再碰到面试官问我这个问题，我就可以吊打他！
>
> 扫地僧：万不可骄傲大意，学无止境！其实${}应用不仅仅限于mybatis，例如linux脚本中，经常使用$1，$2等等表示输入参数的占位符。知道了这点就能很容易区分$和#，从而不容易记错了。
>
> 小白：知道了，师傅！那我好好总结一下这个问题。

## 总结

1.#{}适合赋值的常见如查询select，更新 update，删除delete，新增insert

2.${}适合元数据(即:表名或列名)动态赋值，如order by ，group by等

3.使用${}时要注意，直接接受用户的输入并将其提供给sql执行语句是不安全的，最好使用枚举让用户选择，或者始终执行自己的转义和检查。