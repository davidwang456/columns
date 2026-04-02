# spring boot如何集成mybatis？

## 背景

> 小白：师傅，现在的java开发，统一的都是spring boot，使用原生mybatis或者spring+mybatis的都很少，能否讲讲sprng boot怎么集成mybaits的？集成的原理是什么？
>
> 扫地僧：得益于spring boot各种开发好的starters，spring boot集成mybatis相对容易。其中的原理则是利用Spring boot的自动注册原理，来集成SqlSessionFactory，最终在spring boot容器中生成各种mapper的bean。代码撸下，看看就知道了。

## Spring Boot集成mybatis实例

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

创建spring boot项目，其完整的代码结果如下：

![image-20210614083402610](img\springbootintegratemybatis.png)

#### 添加依赖

pom.xml

```
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
	xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
	<modelVersion>4.0.0</modelVersion>
	<parent>
		<groupId>org.springframework.boot</groupId>
		<artifactId>spring-boot-starter-parent</artifactId>
		<version>2.5.1</version>
		<relativePath/> <!-- lookup parent from repository -->
	</parent>
	<groupId>com.davidwang456.integrate</groupId>
	<artifactId>SpringBootIntegrateMybatis</artifactId>
	<version>3.8.0-SNAPSHOT</version>
	<name>SpringBootIntegrateMybatis</name>
	<description>Spring Boot integrate mybatis</description>
	<properties>
		<java.version>1.8</java.version>
	</properties>
	<dependencies>
		<dependency>
			<groupId>org.springframework.boot</groupId>
			<artifactId>spring-boot-starter</artifactId>
		</dependency>
      <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
      </dependency>		
        <dependency>
            <groupId>org.mybatis.spring.boot</groupId>
            <artifactId>mybatis-spring-boot-starter</artifactId>
            <version>2.1.3</version>
        </dependency>
		<dependency>
            <groupId>com.zaxxer</groupId>
            <artifactId>HikariCP</artifactId>
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
        </dependency>		
		<dependency>
			<groupId>org.springframework.boot</groupId>
			<artifactId>spring-boot-starter-test</artifactId>
			<scope>test</scope>
		</dependency>
	</dependencies>

	<build>
		<plugins>
			<plugin>
				<groupId>org.springframework.boot</groupId>
				<artifactId>spring-boot-maven-plugin</artifactId>
			</plugin>
		</plugins>
	</build>

</project>
```

#### 实体

**数据库实体**

```java
package com.davidwang456.mybatis.integrate;

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
package com.davidwang456.mybatis.integrate;

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

application.properties

```java
mybatis.mapper-locations=classpath:mapper/*.xml
mybatis.configuration.map-underscore-to-camel-case=true
mybatis.configuration.log-impl=org.apache.ibatis.logging.stdout.StdOutImpl
spring.datasource.url=jdbc:mysql://localhost:3306/davidwang456?characterEncoding=UTF-8&useSSL=false&useLegacyDatetimeCode=false&serverTimezone=UTC
spring.datasource.username=root
spring.datasource.password=wangwei456
spring.datasource.driver-class-name=com.mysql.cj.jdbc.Driver

# Hikari will use the above plus the following to setup connection pooling
spring.datasource.type=com.zaxxer.hikari.HikariDataSource
spring.datasource.hikari.minimum-idle=5
spring.datasource.hikari.maximum-pool-size=15
spring.datasource.hikari.auto-commit=true
spring.datasource.hikari.idle-timeout=30000
spring.datasource.hikari.pool-name=HikariCP
spring.datasource.hikari.max-lifetime=1800000
spring.datasource.hikari.connection-timeout=30000
```

其中，配置mybatis.configuration.map-underscore-to-camel-case=true定义了支持驼峰形式，配置mybatis.configuration.log-impl=org.apache.ibatis.logging.stdout.StdOutImpl定义了日志打印的类，类似与xml配置：

```xml
	<settings>
		<setting name="logImpl" value="STDOUT_LOGGING"/>
		<setting name="mapUnderscoreToCamelCase" value="true"/>
   </settings>
```

在src/main/resources目录下，定义**mapper.xml文件。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
        PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.integrate.StudentMapper">

	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.integrate.StudentQueryDTO" 
	resultType="com.davidwang456.mybatis.integrate.StudentDTO" useCache="false">
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
	</select>
</mapper>
```

**Mapper文件**

```java
package com.davidwang456.mybatis.integrate;

import java.util.List;

import org.apache.ibatis.annotations.Mapper;
@Mapper
public interface StudentMapper {
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
}
```

#### 测试程序

```java
package com.davidwang456.mybatis.integrate;

import java.util.List;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseBody;

@Controller
public class TestController {
	  @Autowired
	  private StudentMapper studentMapper;
	  
	  
	  @PostMapping("/list")
	  @ResponseBody
	  public List<StudentDTO> list(@RequestBody StudentQueryDTO studentQueryDTO) {
		  return studentMapper.getStudentInfoByCondition(studentQueryDTO);
	  }
}
```

启动spring主程序：

```java
package com.davidwang456.mybatis.integrate;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class SpringBootIntegrateMybatisApplication {

	public static void main(String[] args) {
		SpringApplication.run(SpringBootIntegrateMybatisApplication.class, args);
	}

}
```

此时默认启动端口8080.使用postman进行测试：

```java
curl --location --request POST 'http://127.0.0.1:8080/list' \
--header 'Content-Type: application/json' \
--data-raw '{
    "id": null,
    "firstName": "wang",
    "lastName": "david",
    "age": null
}'
```

返回结果符合预期:

```json
[
    {
        "id": 1,
        "firstName": "wang1",
        "lastName": "david1",
        "age": 21
    },
    {
        "id": 2,
        "firstName": "wang2",
        "lastName": "david2",
        "age": 22
    },
    {
        "id": 3,
        "firstName": "wang3",
        "lastName": "david3",
        "age": 23
    },
    {
        "id": 4,
        "firstName": "wang4",
        "lastName": "david4",
        "age": 24
    },
    {
        "id": 5,
        "firstName": "wang5",
        "lastName": "david5",
        "age": 25
    },
    {
        "id": 6,
        "firstName": "wang6",
        "lastName": "david6",
        "age": 26
    },
    {
        "id": 7,
        "firstName": "wang7",
        "lastName": "david7",
        "age": 27
    },
    {
        "id": 8,
        "firstName": "wang8",
        "lastName": "david8",
        "age": 28
    }
]
```

至此，spring boot集成mybatis就完成了。如果是老系统升级到spring boot，则可以利用配置属性mybatis.config-location来定义MyBatis.xml配置文件。其它mybatis的属性可自行参考。接下来我们看看spring boot集成mybatis的内部原理吧。

## spring boot集成mybatis的内部原理









我们接下来说：springboot是如何和mybatis进行整合的

1.首先，springboot中使用mybatis需要用到mybatis-spring-boot-start，可以理解为mybatis开发的整合springboot的jar包

有一个关键点先说明：前面也提到过，不管是mybatis和spring整合，还是和springboot整合，都需要做两个操作：

  1.把当前接口和对应的mapperProxyFactory存入到knownMappers中，

  2.把sql包装成mappedStatement，存入到mappedStatements这个map中

 

1.springboot项目中，@SpringbootApplication注解上，有一个@EnableAutoConfiguration注解，而@EnableAutoConfiguration注解又利用了@Import注解，注入了一个ImportSelector的实现类 AutoConfigurationImportSelector.class

2.AutoConfigurationImportSelector在selectImports()方法中有一行重要的代码：

  List<String> configurations = SpringFactoriesLoader.loadFactoryNames(this.getSpringFactoriesLoaderFactoryClass(), this.getBeanClassLoader());

 

  这行代码内部，会从所有jar包中，META-INF/spring.factories文件中，加载EnableAutoConfiguration对应的实现类，那mybatis-spring-boot-autoconfigure.jar包中，配置了两个实现类，

 

  

```
1 org.springframework.boot.autoconfigure.EnableAutoConfiguration=\
2 org.mybatis.spring.boot.autoconfigure.MybatisLanguageDriverAutoConfiguration,\
3 org.mybatis.spring.boot.autoconfigure.MybatisAutoConfiguration
```

 

3.我们来说 MybatisAutoConfiguration.class

 

[![复制代码](https://common.cnblogs.com/images/copycode.gif)](javascript:void(0);)

```
1 @org.springframework.context.annotation.Configuration
2   @ConditionalOnClass({ SqlSessionFactory.class, SqlSessionFactoryBean.class })
3   @ConditionalOnSingleCandidate(DataSource.class)
4   @EnableConfigurationProperties(MybatisProperties.class)
5   @AutoConfigureAfter({ DataSourceAutoConfiguration.class, MybatisLanguageDriverAutoConfiguration.class })
6   public class MybatisAutoConfiguration implements InitializingBean {
7  
8  
9   }
```

[![复制代码](https://common.cnblogs.com/images/copycode.gif)](javascript:void(0);)

 

 

 这里有一个点，就是 @EnableConfigurationProperties(MybatisProperties.class)；点开MybatisProperties.class文件会发现，这里面声明的就是，在application.properties配置文件中，mybatis提供的配置信息：比如mybatis.mapper-locations=classpath:mapping/*Mapper.xml；这个点，不细说了，后面可能会写一篇自定义starter的学习笔记，到时候 再详细写

 

4.在MybatisAutoConfiguration中有一个静态内部类 AutoConfiguredMapperScannerRegistrar 实现了ImportBeanDefinitionRegistrar;

  所以，spring在refresh的时候，会执行这个类的 registerBeanDefinitions()方法，将 MapperScannerConfigurer存到了beanDefinitionMap中

[![复制代码](https://common.cnblogs.com/images/copycode.gif)](javascript:void(0);)

```
 1 @Override
 2 public void registerBeanDefinitions(AnnotationMetadata importingClassMetadata, BeanDefinitionRegistry registry) {
 3  
 4  
 5   if (!AutoConfigurationPackages.has(this.beanFactory)) {
 6     logger.debug("Could not determine auto-configuration package, automatic mapper scanning disabled.");
 7     return;
 8   }
 9   //中间删除了部分代码
10   registry.registerBeanDefinition(MapperScannerConfigurer.class.getName(), builder.getBeanDefinition());
11 }
```

[![复制代码](https://common.cnblogs.com/images/copycode.gif)](javascript:void(0);)

 

5.MapperScannerConfigurer是BeanDefinitionRegistryPostProcessor的实现类，在refresh() --> invokeBeanFactoryPostProcessors(beanFactory)中，会遍历所有beanFactoryPostProcessor和BeanDefinitionRegistrtPostProcessor的实现类，依次执行postProcessorBeanDefinitionRegistrar()方法

 

  MapperScannerConfigurer的postProcessBeanDefinitionRegistry()方法，会执行扫描方法，这里的扫描方法，和mapperScannerRegistrar的registerBeanDefinitions中的doScan是一样的，这里扫描的包是在，初始化MapperScannerConfigurer的时候，在执行完属性注入之后，调用了截图中的方法，把当前pvs中的basePackage传到MapperScannerConfigurer中，这里是如何传过去的，待研究

 

 

6.在将mapper扫描完之后，需要进行sql的解析，在和springboot整合之后，需要在配置文件中配置当前要扫描的mapper.xml文件，

  mybatis.mapper-locations=classpath:mapping/*Mapper.xml

 

  这里的mapperLocation,是在sqlSesionFactorybean中进行解析的，在第3步中的自动配置类中，通过@Bean,注入了SqlSessionFactory,

  在sqlSessionFactory()方法最后，会调用factoryBean.getObject()方法，这里其实调用的就是SqlSessionFactory的getObject()方法，

 

  

[![复制代码](https://common.cnblogs.com/images/copycode.gif)](javascript:void(0);)

```
1 @Override
2   public SqlSessionFactory getObject() throws Exception {
3     if (this.sqlSessionFactory == null) {
4       afterPropertiesSet();
5     }
6  
7  
8     return this.sqlSessionFactory;
9   }
```

[![复制代码](https://common.cnblogs.com/images/copycode.gif)](javascript:void(0);)

 

 

 

从afterPropertiesSet()方法，一直往下追，会追到同类中的buildSqlSessionFactory()方法，在这个方法中，判断如果当前mapperlocation不为null，就进行解析

 

 

[![复制代码](https://common.cnblogs.com/images/copycode.gif)](javascript:void(0);)

```
 1 if (this.mapperLocations != null) {
 2       if (this.mapperLocations.length == 0) {
 3         LOGGER.warn(() -> "Property 'mapperLocations' was specified but matching resources are not found.");
 4       } else {
 5         for (Resource mapperLocation : this.mapperLocations) {
 6           if (mapperLocation == null) {
 7             continue;
 8           }
 9           try {
10             XMLMapperBuilder xmlMapperBuilder = new XMLMapperBuilder(mapperLocation.getInputStream(),
11                 targetConfiguration, mapperLocation.toString(), targetConfiguration.getSqlFragments());
12             xmlMapperBuilder.parse();
13           } catch (Exception e) {
14             throw new NestedIOException("Failed to parse mapping resource: '" + mapperLocation + "'", e);
15           } finally {
16             ErrorContext.instance().reset();
17           }
18           LOGGER.debug(() -> "Parsed mapper file: '" + mapperLocation + "'");
19         }
20       }
21     }
```

[![复制代码](https://common.cnblogs.com/images/copycode.gif)](javascript:void(0);)

 

 

在xmlMapperBuilder.parse()就是原生mybatis在解析xml文件时，需要调用的方法

 

 

7.在service中注入mapper接口，在初始化service，注入依赖的mapper接口时，还是调用的mapperFactorybean.getObject()方法来获取代理对象

 

springboot整合mybatis  和 spring+mybatis整合时，解析xml文件有一个区别：

 spring-mybatis是利用mapperFactorybean的checkDao()方法来解析xml，put数据到mappedStatement和knowmappers

 

 springboot是利用SqlSessionFactoryBean的getObject()来解析xml,put数据到mappedStatement和knowMappers 