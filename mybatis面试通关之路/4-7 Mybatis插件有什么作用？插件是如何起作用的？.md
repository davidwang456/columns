# Mybatis插件有什么作用？插件是如何起作用的？



# **背景**

> 小白：师傅，分页的时候我们一般情况下都是在sql语句中完成的，这对应用的移植非常不方便。我听说可以使用分页插件来帮助我们应对不同数据库之间的移植问题。
>
> 扫地僧：Mybatis插件提供了一种扩展机制，用户可以利用插件做一些自定义的功能如分页，公共字段统一赋值，性能监控，还可以打印sql日志，权限控制等。
>
> 小白：听起来很酷，是不是很难学？
>
> 扫地僧：Mybatis的插件设计很简洁，也很有意思，让我们一起来看看吧！



# **Mybatis插件示例**

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

![image-20210726134135293](img\chapter04-07.png)

#### 添加依赖

pom.xml

```
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>PluginsTest</artifactId>
  <version>4.7.0-SNAPSHOT</version>
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
	<dependency>
	    <groupId>cglib</groupId>
	    <artifactId>cglib</artifactId>
	    <version>3.3.0</version>
	</dependency>	
   </dependencies>
</project>
```

#### 实体

**数据库实体**

```java
package com.davidwang456.mybatis.plugin;

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
package com.davidwang456.mybatis.plugin;

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
   <plugins>
   	<plugin interceptor="com.davidwang456.mybatis.plugin.QueryConsumeTimePlugin"/>
   </plugins>
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

其中，plugin的定义如下：

```java
package com.davidwang456.mybatis.plugin;

import java.sql.Statement;

import org.apache.ibatis.executor.statement.StatementHandler;
import org.apache.ibatis.plugin.Interceptor;
import org.apache.ibatis.plugin.Intercepts;
import org.apache.ibatis.plugin.Invocation;
import org.apache.ibatis.plugin.Signature;
import org.apache.ibatis.session.ResultHandler;
@Intercepts({
    @Signature(type = StatementHandler.class, method = "query", args = {Statement.class,ResultHandler.class})
})
public class QueryConsumeTimePlugin implements Interceptor{

	@Override
	public Object intercept(Invocation invocation) throws Throwable {
		//Statement ms = (Statement) invocation.getArgs()[0];
		//ResultHandler resultHandler = (ResultHandler)invocation.getArgs()[1];
		String methodName = invocation.getMethod().getName();
		Long start=System.currentTimeMillis();
		Object result=invocation.proceed();
		System.out.println("execute method:"+methodName+",costTime:"+(System.currentTimeMillis()-start));
		return result;
	}

}
```

在src/main/resources目录下，定义StudentMapper.xml文件。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
        PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.plugin.StudentMapper">
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.plugin.StudentQueryDTO" 
	resultType="com.davidwang456.mybatis.plugin.StudentDTO">
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
package com.davidwang456.mybatis.plugin;

import java.util.List;

public interface StudentMapper {
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
}
```

#### 测试程序

```java
package com.davidwang456.mybatis.plugin;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class PluginTest {

	public static void main(String[] args) throws IOException {
		testPlugin();
	   }
	
	private static void testPlugin() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();	
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("wang");
	      param.setLastName("david");
	      param.setOrderBy("DESC");
	      param.setSort("age");

	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition(param);	      
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

打印结果：

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
execute method:query,costTime:22
------------------getStudentInfoByCondition query------------start-----------
student [id=8, firstName=wang8, lastName=david8, age=30]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=1, firstName=wang1, lastName=david1, age=21]
------------------getStudentInfoByCondition query------------end----------
```



# 深入Mybatis插件内部原理

**1.初始化**

通过断点深入进去，可以看到在XMLConfigBuilder解析interceptors节点

```java
  private void pluginElement(XNode parent) throws Exception {
    if (parent != null) {
      for (XNode child : parent.getChildren()) {
        String interceptor = child.getStringAttribute("interceptor");
        Properties properties = child.getChildrenAsProperties();
        Interceptor interceptorInstance = (Interceptor) resolveClass(interceptor).getDeclaredConstructor().newInstance();
        interceptorInstance.setProperties(properties);
        configuration.addInterceptor(interceptorInstance);
      }
    }
  }
  public void addInterceptor(Interceptor interceptor) {
    interceptorChain.addInterceptor(interceptor);
  }
```

添加到interceptorChain。

**2.拦截链使用之动态代理jdk与cglib简介**

想要拦截一个方法，并执行一段逻辑，我们一般会想到动态代理，不错！mybatis就是使用了动态代理。动态代理一般有两种方式：

**JDK原生动态代理方式**

动态代理只能对接口中声明的方法进行代理。包含下面两个组件：

Proxy：java.lang.reflect.Proxy是所有动态代理的父类。它通过静态方法newProxyInstance()来创建动态代理的class对象和实例。

InvocationHandler：每一个动态代理实例都有一个关联的InvocationHandler。通过代理实例调用方法，方法调用请求会被转发给InvocationHandler的invoke方法。

示例：

代理类：

```java
package com.davidwang456.mybatis.plugin;

public interface IHelloWorld {
	void sayHello(String msg);
}
```

代理类实现

```java
package com.davidwang456.mybatis.plugin;

public class HelloWorldImp implements IHelloWorld{

	@Override
	public void sayHello(String msg) {
		System.out.println("hello "+ msg+ " !");
	}

}

```

动态代理测试类

```java
package com.davidwang456.mybatis.plugin;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;

public class TestJdkDynamicProxy {

	public static void main(String[] args) {
		IHelloWorld iHelloWorld=new HelloWorldImp();
		InsertDataHandler insertDataHandler=new InsertDataHandler();
		IHelloWorld proxy=(IHelloWorld) insertDataHandler.getProxy(iHelloWorld);
		proxy.sayHello("world");
	}
}	
	class InsertDataHandler implements InvocationHandler{
		Object obj;
		
		public Object getProxy(Object obj) {
			this.obj=obj;
			return Proxy.newProxyInstance(obj.getClass().getClassLoader(), 
					obj.getClass().getInterfaces(), this);
		}
		
		@Override
		public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
			doBefore(method.getName());
			Object result=method.invoke(obj, args);
			doAfter(method.getName());
			return result;
		}
		
		private void doBefore(String methodName){
			System.out.println("method:"+methodName +" start");
		}
		
		private void doAfter(String methodName) {
			System.out.println("method:"+methodName +" end");
		}
		
	}
```



运行结果

> method:sayHello start
>hello world !
> method:sayHello end

**CGlib动态代理**

CGLib(Code Generation Library)是一个基于ASM的字节码生成库。它允许我们在运行时对字节码进行修改或动态生成。CGLib通过继承被代理类的方式实现代理。

Enhancer：Enhancer指定要代理的目标对象。通过create方法得到代理对象。通过代理实例调用非final方法，方法调用请求会首先转发给MethodInterceptor的intercept

MethodInterceptor：通过代理实例调用方法，调用请求都会转发给intercept方法进行增强。

动态代理测试类

```java
package com.davidwang456.mybatis.plugin;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;

import net.sf.cglib.proxy.Enhancer;
import net.sf.cglib.proxy.MethodInterceptor;
import net.sf.cglib.proxy.MethodProxy;

public class TestCglibDynamicProxy {

	public static void main(String[] args) {
		IHelloWorld iHelloWorld=new HelloWorldImp();
		InsertDataHandler insertDataHandler=new InsertDataHandler();
		IHelloWorld proxy=(IHelloWorld) insertDataHandler.getProxy(iHelloWorld);
		proxy.sayHello("world");
	}
}	
	class InsertDataInterceptor implements MethodInterceptor{
		Object target;
		
		public Object getProxy(Object target) {
			this.target=target;
			Enhancer enhancer=new Enhancer();
			enhancer.setSuperclass(this.target.getClass());
			enhancer.setCallback(this);
			return enhancer.create();
		}
		
		
		private void doBefore(String methodName){
			System.out.println("method:"+methodName +" start");
		}
		
		private void doAfter(String methodName) {
			System.out.println("method:"+methodName +" end");
		}

		@Override
		public Object intercept(Object obj, Method method, Object[] args, MethodProxy proxy) throws Throwable {
			// TODO Auto-generated method stub
			return null;
		}
		
	}
```

运行结果

> method:sayHello start
>hello world !
> method:sayHello end

**3.mybatis拦截链及动态代理**

拦截链：可以定义多个Interceptor，但注意：不要定义过多的插件，代理嵌套过多，执行方法的时候，比较耗性能；

```java
  public ParameterHandler newParameterHandler(MappedStatement mappedStatement, Object parameterObject, BoundSql boundSql) {
    ParameterHandler parameterHandler = mappedStatement.getLang().createParameterHandler(mappedStatement, parameterObject, boundSql);
    parameterHandler = (ParameterHandler) interceptorChain.pluginAll(parameterHandler);
    return parameterHandler;
  }

  public ResultSetHandler newResultSetHandler(Executor executor, MappedStatement mappedStatement, RowBounds rowBounds, ParameterHandler parameterHandler,
      ResultHandler resultHandler, BoundSql boundSql) {
    ResultSetHandler resultSetHandler = new DefaultResultSetHandler(executor, mappedStatement, parameterHandler, resultHandler, boundSql, rowBounds);
    resultSetHandler = (ResultSetHandler) interceptorChain.pluginAll(resultSetHandler);
    return resultSetHandler;
  }

  public StatementHandler newStatementHandler(Executor executor, MappedStatement mappedStatement, Object parameterObject, RowBounds rowBounds, ResultHandler resultHandler, BoundSql boundSql) {
    StatementHandler statementHandler = new RoutingStatementHandler(executor, mappedStatement, parameterObject, rowBounds, resultHandler, boundSql);
    statementHandler = (StatementHandler) interceptorChain.pluginAll(statementHandler);
    return statementHandler;
  }

  public Executor newExecutor(Transaction transaction) {
    return newExecutor(transaction, defaultExecutorType);
  }

  public Executor newExecutor(Transaction transaction, ExecutorType executorType) {
    executorType = executorType == null ? defaultExecutorType : executorType;
    executorType = executorType == null ? ExecutorType.SIMPLE : executorType;
    Executor executor;
    if (ExecutorType.BATCH == executorType) {
      executor = new BatchExecutor(this, transaction);
    } else if (ExecutorType.REUSE == executorType) {
      executor = new ReuseExecutor(this, transaction);
    } else {
      executor = new SimpleExecutor(this, transaction);
    }
    if (cacheEnabled) {
      executor = new CachingExecutor(executor);
    }
    executor = (Executor) interceptorChain.pluginAll(executor);
    return executor;
  }
```

调用链InterceptorChain封装对象和Interceptor：

```java
  public Object pluginAll(Object target) {
    for (Interceptor interceptor : interceptors) {
      target = interceptor.plugin(target);
    }
    return target;
  }
-->
-->
  default Object plugin(Object target) {
    return Plugin.wrap(target, this);
  }

  public static Object wrap(Object target, Interceptor interceptor) {
    Map<Class<?>, Set<Method>> signatureMap = getSignatureMap(interceptor);
    Class<?> type = target.getClass();
    Class<?>[] interfaces = getAllInterfaces(type, signatureMap);
    if (interfaces.length > 0) {
      return Proxy.newProxyInstance(
          type.getClassLoader(),
          interfaces,
          new Plugin(target, interceptor, signatureMap));
    }
    return target;
  }
```

获取plugin注解@Intercepts

```java
  private static Map<Class<?>, Set<Method>> getSignatureMap(Interceptor interceptor) {
    Intercepts interceptsAnnotation = interceptor.getClass().getAnnotation(Intercepts.class);
    // issue #251
    if (interceptsAnnotation == null) {
      throw new PluginException("No @Intercepts annotation was found in interceptor " + interceptor.getClass().getName());
    }
    Signature[] sigs = interceptsAnnotation.value();
    Map<Class<?>, Set<Method>> signatureMap = new HashMap<>();
    for (Signature sig : sigs) {
      Set<Method> methods = signatureMap.computeIfAbsent(sig.type(), k -> new HashSet<>());
      try {
        Method method = sig.type().getMethod(sig.method(), sig.args());
        methods.add(method);
      } catch (NoSuchMethodException e) {
        throw new PluginException("Could not find method on " + sig.type() + " named " + sig.method() + ". Cause: " + e, e);
      }
    }
    return signatureMap;
  }
```

注解@Intercepts格式为:

```java
@Documented
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.TYPE)
public @interface Intercepts {
  /**
   * Returns method signatures to intercept.
   *
   * @return method signatures
   */
  Signature[] value();
}
@Documented
@Retention(RetentionPolicy.RUNTIME)
@Target({})
public @interface Signature {
  /**
   * Returns the java type.
   *
   * @return the java type
   */
  Class<?> type();

  /**
   * Returns the method name.
   *
   * @return the method name
   */
  String method();

  /**
   * Returns java types for method argument.
   * @return java types for method argument
   */
  Class<?>[] args();
}
```

实例:

```java
@Intercepts({@Signature(
	type=Executor.class,
	method="update",
	args={MappedStatement.class,Object.class})})
publicclassExamplePluginimplementsInterceptor{
@Override
publicObjectintercept(Invocationinvocation)throwsThrowable{
//implementpre-processingifneeded
ObjectreturnObject=invocation.proceed();
//implementpost-processingifneeded
returnreturnObject;
}
}
```

Executor.update方法触发Plugin.java#invoke()方法

```java
  @Override
  public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
    try {
      Set<Method> methods = signatureMap.get(method.getDeclaringClass());
      if (methods != null && methods.contains(method)) {
        return interceptor.intercept(new Invocation(target, method, args));
      }
      return method.invoke(target, args);
    } catch (Exception e) {
      throw ExceptionUtil.unwrapThrowable(e);
    }
  }
```

然后执行Interceptor.java#intercept方法.

注意：拦截器实现类的intercept方法里最后不要忘了执行invocation.proceed()方法，否则多个拦截器情况下，执行链条会断掉

# **小结**

- 简单的说，mybatis插件就是对ParameterHandler、ResultSetHandler、StatementHandler、Executor这四个接口上的方法进行拦截，利用JDK动态代理机制，为这些接口的实现类创建代理对象，在执行方法时，先去执行代理对象的方法，从而执行自己编写的拦截逻辑。要用好mybatis插件，主要还是要熟悉这四个接口的方法以及这些方法上的参数的含义；

- 插件使用了责任链和动态代理模式。插件的设计比较精妙，可以应用到其它场景。
- 动态代理的两种方式：jdk动态代理，cglib动态代理