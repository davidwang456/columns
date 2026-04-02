# spring如何集成mybatis？

# 背景

> 小白：目前不少的 Java老项目，都是用 Spring MVC + Spring + MyBatis 搭建平台的，它们都是怎么集成的呢？
>
> 扫地僧：总体上说，使用 Spring IoC 可以有效的管理各类的 Java 资源，达到即插即拔的功能；通过Spring Mvc集成各种视图引擎，完成高效展示；通过 Spring AOP 框架，数据库事务可以委托给 Spring 管理，消除很大一部分的事务代码，配合 MyBatis 的高灵活、可配置、可优化 SQL 等特性，完全可以构建高性能的大型网站。细节上讲，Spring MVC和Spring 本身是一体的，无需考虑集成问题。而Spring-Mybatis集成可以可用通过MyBatis-Spring来完成。使用 MyBatis-Spring 使得业务层和模型层得到了更好的分离，与此同时，在 Spring 环境中使用 MyBatis 也更加简单，节省了不少代码，甚至可以不用 SqlSessionFactory、 SqlSession 等对象，因为 MyBatis-Spring 为我们封装了它们。说了这么多，其实都不如代码有效，我们来看看一个实例吧！



# Mybatis-Spring集成实例

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

![image-20210630151526123](D:\document\wangwei\mybatis面试通关之路\img\chapter03-07.png)

#### 添加依赖

pom.xml

```xml
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>SpringIntegrateMybatis</artifactId>
  <version>3.7.0-SNAPSHOT</version>
  <name>SpringIntegrateMybatis</name>
  
  <properties>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    <maven.compiler.source>1.8</maven.compiler.source>
    <maven.compiler.target>1.8</maven.compiler.target>
    <org.springframework.version>5.3.8</org.springframework.version>
  </properties>
  <dependencies>
     <!-- Spring -->
    <dependency>
        <groupId>org.springframework</groupId>
        <artifactId>spring-context</artifactId>
        <version>${org.springframework.version}</version>
    </dependency>
    <dependency>
        <groupId>org.springframework</groupId>
        <artifactId>spring-beans</artifactId>
        <version>${org.springframework.version}</version>
    </dependency>
	<dependency>
	    <groupId>org.springframework</groupId>
	    <artifactId>spring-jdbc</artifactId>
        <version>${org.springframework.version}</version>
	</dependency>     
	<dependency>
	  <groupId>org.mybatis</groupId>
	  <artifactId>mybatis-spring</artifactId>
	  <version>2.0.6</version>
	</dependency>        
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
	    <groupId>com.alibaba</groupId>
	    <artifactId>druid</artifactId>
	    <version>1.2.6</version>
	</dependency>	
   </dependencies>
   
</project>
```

**1.实体类**

```java
package com.davidwang456.mybatis.spring;

import java.io.Serializable;

import lombok.Data;

@Data
public class StudentDTO implements Serializable{
	private static final long serialVersionUID = 1L;
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

**2.查询实体**

```java
package com.davidwang456.mybatis.spring;

import lombok.Data;

@Data
public class StudentQueryDTO {
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	private String sort;
	private String orderBy;	
}
```



**3.Mapper类**

```java
package com.davidwang456.mybatis.spring.mapper;

import java.util.List;

import com.davidwang456.mybatis.spring.StudentDTO;
import com.davidwang456.mybatis.spring.StudentQueryDTO;

public interface StudentMapper {
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
}

```



**4.配置类**

```java
package com.davidwang456.mybatis.spring;

import javax.sql.DataSource;

import org.apache.ibatis.session.SqlSessionFactory;
import org.mybatis.spring.SqlSessionFactoryBean;
import org.mybatis.spring.annotation.MapperScan;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.io.support.PathMatchingResourcePatternResolver;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;

import com.alibaba.druid.pool.DruidDataSource;

@Configuration
@MapperScan("com.davidwang456.mybatis.spring.mapper")
public class SpringConfig {
    @Bean
    public DataSource getDataSource() {
       DruidDataSource dataSource = new DruidDataSource();
       dataSource.setDriverClassName("com.mysql.cj.jdbc.Driver");
       dataSource.setUrl("jdbc:mysql://localhost:3306/davidwang456?characterEncoding=UTF-8&useSSL=false&useLegacyDatetimeCode=false&serverTimezone=UTC");
       dataSource.setUsername("root");
       dataSource.setPassword("wangwei456");
       return dataSource;
   }
    
   @Bean
   public DataSourceTransactionManager transactionManager() {
     return new DataSourceTransactionManager(getDataSource());
   }
   
   @Bean
   public SqlSessionFactory sqlSessionFactory() throws Exception {
	  PathMatchingResourcePatternResolver resolver=new PathMatchingResourcePatternResolver();
      SqlSessionFactoryBean sessionFactory = new SqlSessionFactoryBean();
      sessionFactory.setDataSource(getDataSource());
      sessionFactory.setConfigLocation(resolver.getResource("SqlMapConfig.xml"));
      sessionFactory.setMapperLocations(resolver.getResource("StudentMapper.xml"));
      return sessionFactory.getObject();
   }
}
```

其中，SqlMapConfig.xml配置全局属性

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
</configuration>
```

注意：不要使用sessionFactory.setConfigurationProperties(Properties sqlSessionFactoryProperties);来设置全局属性。SqlSessionFactoryBean构建SqlSessionFactory方法时可以看到configurationProperties对应Configuration中的variables：

```java
  protected SqlSessionFactory buildSqlSessionFactory() throws Exception {

    final Configuration targetConfiguration;

    XMLConfigBuilder xmlConfigBuilder = null;
    if (this.configuration != null) {
      targetConfiguration = this.configuration;
      if (targetConfiguration.getVariables() == null) {
        targetConfiguration.setVariables(this.configurationProperties);
      } else if (this.configurationProperties != null) {
        targetConfiguration.getVariables().putAll(this.configurationProperties);
      }
    } else if (this.configLocation != null) {
      xmlConfigBuilder = new XMLConfigBuilder(this.configLocation.getInputStream(), null, this.configurationProperties);
      targetConfiguration = xmlConfigBuilder.getConfiguration();
    } else {
      LOGGER.debug(
          () -> "Property 'configuration' or 'configLocation' not specified, using default MyBatis Configuration");
      targetConfiguration = new Configuration();
      Optional.ofNullable(this.configurationProperties).ifPresent(targetConfiguration::setVariables);
    }
      ......
  }
```

StudentMapper.xml配置相应mapper的sql：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
        PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.spring.mapper.StudentMapper">
	<!--  <spring/> -->
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.spring.StudentQueryDTO" 
	resultType="com.davidwang456.mybatis.spring.StudentDTO">
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



**5.测试类**

```java
package com.davidwang456.mybatis.spring;

import java.io.IOException;
import java.util.List;

import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;

import com.davidwang456.mybatis.spring.mapper.StudentMapper;


public class SpringIntegrateMybatisTest {

	@SuppressWarnings("resource")
	public static void main(String[] args) throws IOException {
		 AnnotationConfigApplicationContext ctx = new AnnotationConfigApplicationContext(SpringConfig.class);
		 SqlSessionFactory ssf=ctx.getBean(SqlSessionFactory.class);
		 SqlSession session=ssf.openSession();
		 //session.getConfiguration().addMapper(StudentMapper.class);
		 
		 StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("wang");
	      param.setLastName("david");
	      param.setOrderBy("DESC");
	      param.setSort("age");

	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition(param);	      
	      printResult(stus,"query");
		 
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

运行结果符合预期：

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
------------------query------------start-----------
student [id=8, firstName=wang8, lastName=david8, age=28]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=1, firstName=wang1, lastName=david1, age=21]
------------------query------------end----------
```

## 深入Spring-Mybatis集成原理

spring提供了扩展接口FactoryBean与第三方类库的集成，通过FactoryBean将第三方类库的对象交给spring容器管理，为了更清楚这个概念，我们打印Spring 容器内的bean：

```java
		 for(String name:ctx.getBeanDefinitionNames()) {
			 System.out.println("bean name:"+name+",bean type:"+ctx.getBean(name).toString());
		 }
```

结果如下：

```tex
bean name:org.springframework.context.annotation.internalConfigurationAnnotationProcessor,bean type:org.springframework.context.annotation.ConfigurationClassPostProcessor@4c4748bf
bean name:org.springframework.context.annotation.internalAutowiredAnnotationProcessor,bean type:org.springframework.beans.factory.annotation.AutowiredAnnotationBeanPostProcessor@7ce97ee5
bean name:org.springframework.context.annotation.internalCommonAnnotationProcessor,bean type:org.springframework.context.annotation.CommonAnnotationBeanPostProcessor@32c8e539
bean name:org.springframework.context.event.internalEventListenerProcessor,bean type:org.springframework.context.event.EventListenerMethodProcessor@73dce0e6
bean name:org.springframework.context.event.internalEventListenerFactory,bean type:org.springframework.context.event.DefaultEventListenerFactory@5a85c92
bean name:springConfig,bean type:com.davidwang456.mybatis.spring.SpringConfig$$EnhancerBySpringCGLIB$$1d29a46c@32811494
bean name:getDataSource,bean type:{
	CreateTime:".....",
	ActiveCount:0,
	PoolingCount:0,
	CreateCount:0,
	DestroyCount:0,
	CloseCount:0,
	ConnectCount:0,
	Connections:[
	]
}
bean name:transactionManager,bean type:org.springframework.jdbc.datasource.DataSourceTransactionManager@78fbff54
bean name:sqlSessionFactory,bean type:org.apache.ibatis.session.defaults.DefaultSqlSessionFactory@3e10dc6
bean name:com.davidwang456.mybatis.spring.SpringConfig#MapperScannerRegistrar#0,bean type:org.mybatis.spring.mapper.MapperScannerConfigurer@7e22550a
bean name:studentMapper,bean type:org.apache.ibatis.binding.MapperProxy@7516e4e5
```

可以发现名称为sqlSessionFactory的bean的类型时Mybatis的DefaultSqlSessionFactory，即Spring容器可以管理Mybatis的DefaultSqlSessionFactory对象。

名称为studentMapper的bean的类型为MapperProxy，通过名称我们就可以知道它是一个代理类，Spring容器也可以管理studentMapper对象了，如果有更多的mapper类也将会产生更多的MapperProxy对象来交给spring管理。

那么SqlSessionFactory是如何构建Mybatis的DefaultSqlSessionFactory呢？不妨来看看代码：

**1.SqlSessionFactoryBean的初始化**

调用SqlSessionFactoryBean的getObject()返回SqlSessionFactory。而在getObject()内部通过buildSqlSessionFactory()方法来完成SqlSessionFactory的构建。支持xml和注解方式。

```java
  /**
   * Build a {@code SqlSessionFactory} instance.
   *
   * The default implementation uses the standard MyBatis {@code XMLConfigBuilder} API to build a
   * {@code SqlSessionFactory} instance based on a Reader. Since 1.3.0, it can be specified a {@link Configuration}
   * instance directly(without config file).
   *
   * @return SqlSessionFactory
   * @throws Exception
   *           if configuration is failed
   */
  protected SqlSessionFactory buildSqlSessionFactory() throws Exception {

    final Configuration targetConfiguration;

    XMLConfigBuilder xmlConfigBuilder = null;
    if (this.configuration != null) {
      targetConfiguration = this.configuration;
      if (targetConfiguration.getVariables() == null) {
        targetConfiguration.setVariables(this.configurationProperties);
      } else if (this.configurationProperties != null) {
        targetConfiguration.getVariables().putAll(this.configurationProperties);
      }
    } else if (this.configLocation != null) {
      xmlConfigBuilder = new XMLConfigBuilder(this.configLocation.getInputStream(), null, this.configurationProperties);
      targetConfiguration = xmlConfigBuilder.getConfiguration();
    } else {
      LOGGER.debug(
          () -> "Property 'configuration' or 'configLocation' not specified, using default MyBatis Configuration");
      targetConfiguration = new Configuration();
      Optional.ofNullable(this.configurationProperties).ifPresent(targetConfiguration::setVariables);
    }

    Optional.ofNullable(this.objectFactory).ifPresent(targetConfiguration::setObjectFactory);
    Optional.ofNullable(this.objectWrapperFactory).ifPresent(targetConfiguration::setObjectWrapperFactory);
    Optional.ofNullable(this.vfs).ifPresent(targetConfiguration::setVfsImpl);

    if (hasLength(this.typeAliasesPackage)) {
      scanClasses(this.typeAliasesPackage, this.typeAliasesSuperType).stream()
          .filter(clazz -> !clazz.isAnonymousClass()).filter(clazz -> !clazz.isInterface())
          .filter(clazz -> !clazz.isMemberClass()).forEach(targetConfiguration.getTypeAliasRegistry()::registerAlias);
    }

    if (!isEmpty(this.typeAliases)) {
      Stream.of(this.typeAliases).forEach(typeAlias -> {
        targetConfiguration.getTypeAliasRegistry().registerAlias(typeAlias);
        LOGGER.debug(() -> "Registered type alias: '" + typeAlias + "'");
      });
    }

    if (!isEmpty(this.plugins)) {
      Stream.of(this.plugins).forEach(plugin -> {
        targetConfiguration.addInterceptor(plugin);
        LOGGER.debug(() -> "Registered plugin: '" + plugin + "'");
      });
    }

    if (hasLength(this.typeHandlersPackage)) {
      scanClasses(this.typeHandlersPackage, TypeHandler.class).stream().filter(clazz -> !clazz.isAnonymousClass())
          .filter(clazz -> !clazz.isInterface()).filter(clazz -> !Modifier.isAbstract(clazz.getModifiers()))
          .forEach(targetConfiguration.getTypeHandlerRegistry()::register);
    }

    if (!isEmpty(this.typeHandlers)) {
      Stream.of(this.typeHandlers).forEach(typeHandler -> {
        targetConfiguration.getTypeHandlerRegistry().register(typeHandler);
        LOGGER.debug(() -> "Registered type handler: '" + typeHandler + "'");
      });
    }

    targetConfiguration.setDefaultEnumTypeHandler(defaultEnumTypeHandler);

    if (!isEmpty(this.scriptingLanguageDrivers)) {
      Stream.of(this.scriptingLanguageDrivers).forEach(languageDriver -> {
        targetConfiguration.getLanguageRegistry().register(languageDriver);
        LOGGER.debug(() -> "Registered scripting language driver: '" + languageDriver + "'");
      });
    }
    Optional.ofNullable(this.defaultScriptingLanguageDriver)
        .ifPresent(targetConfiguration::setDefaultScriptingLanguage);

    if (this.databaseIdProvider != null) {// fix #64 set databaseId before parse mapper xmls
      try {
        targetConfiguration.setDatabaseId(this.databaseIdProvider.getDatabaseId(this.dataSource));
      } catch (SQLException e) {
        throw new NestedIOException("Failed getting a databaseId", e);
      }
    }

    Optional.ofNullable(this.cache).ifPresent(targetConfiguration::addCache);

    if (xmlConfigBuilder != null) {
      try {
        xmlConfigBuilder.parse();
        LOGGER.debug(() -> "Parsed configuration file: '" + this.configLocation + "'");
      } catch (Exception ex) {
        throw new NestedIOException("Failed to parse config resource: " + this.configLocation, ex);
      } finally {
        ErrorContext.instance().reset();
      }
    }

    targetConfiguration.setEnvironment(new Environment(this.environment,
        this.transactionFactory == null ? new SpringManagedTransactionFactory() : this.transactionFactory,
        this.dataSource));

    if (this.mapperLocations != null) {
      if (this.mapperLocations.length == 0) {
        LOGGER.warn(() -> "Property 'mapperLocations' was specified but matching resources are not found.");
      } else {
        for (Resource mapperLocation : this.mapperLocations) {
          if (mapperLocation == null) {
            continue;
          }
          try {
            XMLMapperBuilder xmlMapperBuilder = new XMLMapperBuilder(mapperLocation.getInputStream(),
                targetConfiguration, mapperLocation.toString(), targetConfiguration.getSqlFragments());
            xmlMapperBuilder.parse();
          } catch (Exception e) {
            throw new NestedIOException("Failed to parse mapping resource: '" + mapperLocation + "'", e);
          } finally {
            ErrorContext.instance().reset();
          }
          LOGGER.debug(() -> "Parsed mapper file: '" + mapperLocation + "'");
        }
      }
    } else {
      LOGGER.debug(() -> "Property 'mapperLocations' was not specified.");
    }

    return this.sqlSessionFactoryBuilder.build(targetConfiguration);
  }
```

可以发现这段就是Mybatis构建DefaultSqlSessionFactory的过程。

**2.使用MapperProxyFactory生成studentMapper对象**

getMapper方法通过MapperProxyFactory调用Proxy动态生成代理实例供spring容器管理。

```java
  @SuppressWarnings("unchecked")
  protected T newInstance(MapperProxy<T> mapperProxy) {
    return (T) Proxy.newProxyInstance(mapperInterface.getClassLoader(), new Class[] { mapperInterface }, mapperProxy);
  }

  public T newInstance(SqlSession sqlSession) {
    final MapperProxy<T> mapperProxy = new MapperProxy<>(sqlSession, mapperInterface, methodCache);
    return newInstance(mapperProxy);
  }
```

**3.使用MapperFactoryBean生成studentMapper对象**

```xml
     <bean id="baseMapper" class="org.mybatis.spring.mapper.MapperFactoryBean" abstract="true" lazy-init="true">
       <property name="sqlSessionFactory" ref="sqlSessionFactory" />
     </bean>
  
     <bean id="oneMapper" parent="baseMapper">
       <property name="mapperInterface" value="my.package.MyMapperInterface" />
     </bean>
  
     <bean id="anotherMapper" parent="baseMapper">
       <property name="mapperInterface" value="my.package.MyAnotherMapperInterface" />
     </bean>
```



# 总结

Spring 集成Mybatis时需要把SqlSessionFactory对象和各种Mapper类的实例交给spring容器来管理：

1.spring集成Mybatis用了一个FactoryBean工厂类，通过调用getObect()方法获取SqlSessionFactory实例，并交给Spring 容器管理。

2.Mapper实例生成方式有两种方式：

- 通过MapperProxyFactory调用Proxy动态生成代理实例供spring容器管理；
- 通过MapperFactoryBean的getObect()方法生成mapper实例(底层也是使用Proxy动态代理生成)供spring容器调用；