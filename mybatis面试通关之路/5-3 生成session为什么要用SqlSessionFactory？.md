# 生成session为什么要用SqlSessionFactory？

## 背景

> 小白：设计模式是一套被反复使用、多数人知晓的、经过分类编目的、代码设计经验的总结。使用设计模式是为了可重用代码、让代码更容易被他人理解、保证代码可靠性。那Mybatis都用了哪些设计模式？
>
> 扫地僧：Mybatis源码中使用了大量的设计模式如最常见的工厂模式：使用SqlSessionFactory生成SqlSession。
>
> 小白：那生成Session用什么要用SqlSessionFactory呢？直接new一个不就行了吗？
>
> 扫地僧：先不着急揭晓答案，先进入SqlSessionFactory看看Session是如何生成的吧？

## 工厂模式SqlSessionFactory及实现类

先看看代码是如何实现创建session的：

```java
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();	
```

- 第一步：读取Mybatis的配置文件到Reader；和Java中的IO流内容相似

- 第二步：SqlSessionFactoryBuilder根据Reader构建SqlSessionFactory；这里涉及到另外设计模式：Builder模式。这个模式我们放到后面谈，暂时忽略。

- 第三步：SqlSessionFactory生成SqlSession；工厂方式，生成Session。工厂模式（Factory Pattern）是 Java 中最常用的设计模式之一。这种类型的设计模式属于创建型模式，它提供了一种创建对象的最佳方式。在工厂模式中，我们在创建对象时不会对客户端暴露创建逻辑，并且是通过使用一个共同的接口来指向新创建的对象。

### SqlSessionFactory定义

抽象的说法不太好理解，我们直接来代码中看看。

```java
package org.apache.ibatis.session;

import java.sql.Connection;

/**
 * Creates an {@link SqlSession} out of a connection or a DataSource
 *
 * @author Clinton Begin
 */
public interface SqlSessionFactory {

  SqlSession openSession();

  SqlSession openSession(boolean autoCommit);

  SqlSession openSession(Connection connection);

  SqlSession openSession(TransactionIsolationLevel level);

  SqlSession openSession(ExecutorType execType);

  SqlSession openSession(ExecutorType execType, boolean autoCommit);

  SqlSession openSession(ExecutorType execType, TransactionIsolationLevel level);

  SqlSession openSession(ExecutorType execType, Connection connection);

  Configuration getConfiguration();

}
```

这里可以看到创建Session有多种方式，因参数的不同，创建方式也不同。使用工厂方法的原因：1.Session作为和应用本身交互的窗口，本身承接了很多功能，如果将构建方法也放入Session，就显得Session太重；2.在session中使用构建方法会暴漏session的创建过程，不利用session的安全防护；3.使用工厂方法，可以减少代码耦合，易于代码的理解。

### SqlSessionFactory实现类

**DefaultSqlSessionFactory线程不安全类**

DefaultSqlSessionFactory创建Session主要有两种方式，一种使用DataSource的信息创建DefaultSqlSession，但内部还是使用Connection，另一种使用Connection创建DefaultSqlSession。DefaultSqlSession封装了执行sql需要的执行器Exector及执行需要的配置信息类Configuration。

**SqlSessionManager线程安全类**

使用ThreadLocal封装了线程安全的localSqlSession，还使用动态代理模式或者Builder模式封装了DefaultSqlSessionFactory。

```java
  private SqlSessionManager(SqlSessionFactory sqlSessionFactory) {
    this.sqlSessionFactory = sqlSessionFactory;
    this.sqlSessionProxy = (SqlSession) Proxy.newProxyInstance(
        SqlSessionFactory.class.getClassLoader(),
        new Class[]{SqlSession.class},
        new SqlSessionInterceptor());
  }
```

**Spring事务管理的SqlSessionTemplate 线程安全实现类**

SqlSessionTemplate是专为Spring提供的线程安全实现类，它和SqlSessionManager相似，但又不完全相同。

```java
  public SqlSessionTemplate(SqlSessionFactory sqlSessionFactory, ExecutorType executorType,
      PersistenceExceptionTranslator exceptionTranslator) {

    notNull(sqlSessionFactory, "Property 'sqlSessionFactory' is required");
    notNull(executorType, "Property 'executorType' is required");

    this.sqlSessionFactory = sqlSessionFactory;
    this.executorType = executorType;
    this.exceptionTranslator = exceptionTranslator;
    this.sqlSessionProxy = (SqlSession) newProxyInstance(
        SqlSessionFactory.class.getClassLoader(),
        new Class[] { SqlSession.class },
        new SqlSessionInterceptor());
  }
```

不同之处：

- SqlSessionManager由使用者决定session是否共享事务，而SqlSessionTemplate会检查当前Session是不是Spring managed的session,不是同一个Session就不能共享事务，强制提交；是的话，可以共享事务。

  ```java
      /**
     * Proxy needed to route MyBatis method calls to the proper SqlSession got
     * from Spring's Transaction Manager
     * It also unwraps exceptions thrown by {@code Method#invoke(Object, Object...)} to
     * pass a {@code PersistenceException} to the {@code PersistenceExceptionTranslator}.
     */
    private class SqlSessionInterceptor implements InvocationHandler {
      @Override
      public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
        SqlSession sqlSession = getSqlSession(
            SqlSessionTemplate.this.sqlSessionFactory,
            SqlSessionTemplate.this.executorType,
            SqlSessionTemplate.this.exceptionTranslator);
        try {
          Object result = method.invoke(sqlSession, args);
          if (!isSqlSessionTransactional(sqlSession, SqlSessionTemplate.this.sqlSessionFactory)) {
            // force commit even on non-dirty sessions because some databases require
            // a commit/rollback before calling close()
            sqlSession.commit(true);
          }
          return result;
        } catch (Throwable t) {
          Throwable unwrapped = unwrapThrowable(t);
          if (SqlSessionTemplate.this.exceptionTranslator != null && unwrapped instanceof PersistenceException) {
            // release the connection to avoid a deadlock if the translator is no loaded. See issue #22
            closeSqlSession(sqlSession, SqlSessionTemplate.this.sqlSessionFactory);
            sqlSession = null;
            Throwable translated = SqlSessionTemplate.this.exceptionTranslator.translateExceptionIfPossible((PersistenceException) unwrapped);
            if (translated != null) {
              unwrapped = translated;
            }
          }
          throw unwrapped;
        } finally {
          if (sqlSession != null) {
            closeSqlSession(sqlSession, SqlSessionTemplate.this.sqlSessionFactory);
          }
        }
      }
    }
  ```

  - SqlSessionTemplate加入了异常转码，统一转码为Spring的错误编码。

## Builder模式

​	当一个对象必须经过多个步骤来创建，并且要求不同的参数可以产生不同的表现的时候，可以考虑使用Builder模式，它通常采用链式的方式，简单优美。Mybatis也大量使用了这种模式，以SqlSessionFactoryBuilder为例。

```java
/**
 * Builds {@link SqlSession} instances.
 *
 * @author Clinton Begin
 */
public class SqlSessionFactoryBuilder {

  public SqlSessionFactory build(Reader reader) {
    return build(reader, null, null);
  }

  public SqlSessionFactory build(Reader reader, String environment) {
    return build(reader, environment, null);
  }

  public SqlSessionFactory build(Reader reader, Properties properties) {
    return build(reader, null, properties);
  }

  public SqlSessionFactory build(Reader reader, String environment, Properties properties) {
    try {
      XMLConfigBuilder parser = new XMLConfigBuilder(reader, environment, properties);
      return build(parser.parse());
    } catch (Exception e) {
      throw ExceptionFactory.wrapException("Error building SqlSession.", e);
    } finally {
      ErrorContext.instance().reset();
      try {
        reader.close();
      } catch (IOException e) {
        // Intentionally ignore. Prefer previous error.
      }
    }
  }

  public SqlSessionFactory build(InputStream inputStream) {
    return build(inputStream, null, null);
  }

  public SqlSessionFactory build(InputStream inputStream, String environment) {
    return build(inputStream, environment, null);
  }

  public SqlSessionFactory build(InputStream inputStream, Properties properties) {
    return build(inputStream, null, properties);
  }

  public SqlSessionFactory build(InputStream inputStream, String environment, Properties properties) {
    try {
      XMLConfigBuilder parser = new XMLConfigBuilder(inputStream, environment, properties);
      return build(parser.parse());
    } catch (Exception e) {
      throw ExceptionFactory.wrapException("Error building SqlSession.", e);
    } finally {
      ErrorContext.instance().reset();
      try {
        inputStream.close();
      } catch (IOException e) {
        // Intentionally ignore. Prefer previous error.
      }
    }
  }

  public SqlSessionFactory build(Configuration config) {
    return new DefaultSqlSessionFactory(config);
  }

}
```

SqlSessionFactoryBuilder接收多种参数，不同组合的参数来构建SqlSessionFactory。



## 动态代理模式

​	在直接访问对象时带来的问题，比如说：要访问的对象在远程的机器上。在面向对象系统中，有些对象由于某些原因（比如对象创建开销很大，或者某些操作需要安全控制，或者需要进程外的访问），直接访问会给使用者或者系统结构带来很多麻烦，我们可以在访问此对象时加上一个对此对象的访问层。在代理模式（Proxy Pattern）中，一个类代表另一个类的功能。Mybatis主要使用了Java的动态代理模式。

### java中的动态代理实现

以Mybatis中的SqlSessionManager为例，说一下动态代理的实现过程。

- 动态代理是由Proxy Java反射类提供实例创建的调派，由Proxy.newProxyInstance()方法帮我们创建对应的实例对象。

```JAVA
  private SqlSessionManager(SqlSessionFactory sqlSessionFactory) {
    this.sqlSessionFactory = sqlSessionFactory;
    this.sqlSessionProxy = (SqlSession) Proxy.newProxyInstance(
        SqlSessionFactory.class.getClassLoader(),
        new Class[]{SqlSession.class},
        new SqlSessionInterceptor());
  }
```

- 通过InvocationHandler接口中的invoke方法进行调用、增强、转发实现业务。

```java
  private class SqlSessionInterceptor implements InvocationHandler {
    public SqlSessionInterceptor() {
        // Prevent Synthetic Access
    }

    @Override
    public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
      final SqlSession sqlSession = SqlSessionManager.this.localSqlSession.get();
      if (sqlSession != null) {
        try {
          return method.invoke(sqlSession, args);
        } catch (Throwable t) {
          throw ExceptionUtil.unwrapThrowable(t);
        }
      } else {
        try (SqlSession autoSqlSession = openSession()) {
          try {
            final Object result = method.invoke(autoSqlSession, args);
            autoSqlSession.commit();
            return result;
          } catch (Throwable t) {
            autoSqlSession.rollback();
            throw ExceptionUtil.unwrapThrowable(t);
          }
        }
      }
    }
  }
```

## 总结

​	在软件工程领域，设计模式是一套通用的可复用的解决方案，用来解决在软件设计过程中产生的通用问题。它不是一个可以直接转换成源代码的设计，只是一套在软件系统设计过程中程序员应该遵循的最佳实践准则。Mybatis作为优秀的源码框架，在内部也大量使用了设计模式。本文通过Session创建过程，我们就看到使用了工厂模式，Builder模式，动态代理模式等。其它诸如模板方法模式 : 例如BaseExecutor和SimpleExecutor，还有BaseTypeHandler和所有的子类例如IntegerTypeHandler；配器模式 : 例如Log的Mybatis接口和它对jdbc、log4j等各种日志框架的适配实现，就不一一列举了，供读者去独立探索。

​	注意：Session作为交互的中心，Session的创建过程至关重要。故MyBatis框架围绕着SqlSessionFactory这个类进行了大量的工作，这个的创建过程如下：

1. 定义一个Configuration对象，其中包含数据源、事务、mapper文件资源以及影响数据库行为属性设置settings
2. 通过配置对象，则可以创建一个SqlSessionFactoryBuilder对象
3. 通过 SqlSessionFactoryBuilder 获得SqlSessionFactory 的实例。
4. SqlSessionFactory 的实例可以获得操作数据的SqlSession实例，通过这个实例对数据库进行操作



![mybatis_component](img\mybatis_component.jpg)



 
