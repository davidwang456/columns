# mybatis的工作原理是什么？

> 面试官：请你说一下mybatis的工作原理
>
> 小白：MyBatis的基本工作原理就是：先封装SQL，接着调用JDBC操作数据库，最后把数据库返回的表结果封装成Java类。
>
> 面试官：能深入说说Mybatis内部的实现原理吗？
>
> 小白：当然可以，既然Mybatis是JDBC的封装，可以通过JDBC方式和Mybatis的对比来看Mybatis的实现原理。

## MyBatis的前世JDBC数据库编程 vs MyBatis的今生

我们知道，JDBC有四个核心对象：

- DriverManager：用于注册数据库连接
- Connection：数据库连接对象
- Statement/PrepareStatement/CallableStatement：操作数据库SQL语句的对象
- ResultSet：结果集

而操作JDBC有分6步走(我自己归纳划分的，以动态sql为例)：

- Class注册JDBC驱动
- DriverManager打开链接Connection
- 定义操作的SQL语句,Connection实例化PreparedStatement对象 
- PreparedStatement执行数据库操作
- 获取并操作结果集ResultSet
- 依此关闭ResultSet，PreparedStatement，Connection资源

完整的jdbc操作步骤示例，可以参考如下示例：

```java
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;

public class JdbcTest {
    // MySQL 8.0 以下版本 - JDBC 驱动名及数据库 URL
    //static final String JDBC_DRIVER = "com.mysql.jdbc.Driver";  
   // static final String DB_URL = "jdbc:mysql://localhost:3306/davidwang456";
 
    // MySQL 8.0 以上版本 - JDBC 驱动名及数据库 URL
    static final String JDBC_DRIVER = "com.mysql.cj.jdbc.Driver";  
    static final String DB_URL = "jdbc:mysql://localhost:3306/davidwang456?characterEncoding=UTF-8&useSSL=false&useLegacyDatetimeCode=false&serverTimezone=UTC";
 
 
    // 数据库的用户名与密码，需要根据自己的设置
    static final String USER = "root";
    static final String PASS = "wangwei456";

    public static void main(String[] args) {
        Connection conn = null;
        PreparedStatement preparedStatement = null;
        ResultSet rs=null;
        String sql="";
        try{
            //1.Class注册JDBC驱动
            Class.forName(JDBC_DRIVER);
        
            //2 DriverManager打开链接Connection
            System.out.println("连接数据库...");
            conn = DriverManager.getConnection(DB_URL,USER,PASS);
            
            //3 定义操作的SQL语句,Connection实例化PreparedStatement对象           
            sql = "SELECT id, first_name, last_name,age FROM student where id = ?";
            System.out.println(" 实例化PreparedStatement对象...");
            preparedStatement = conn.prepareStatement(sql);
            preparedStatement.setInt(1, 5);

            
            //4 PreparedStatement执行数据库操作
            rs = preparedStatement.executeQuery();
        
            //5 获取并操作结果集ResultSet
            while(rs.next()){
                // 通过字段检索
                int id  = rs.getInt("id");
                String first_name = rs.getString("first_name");
                String last_name = rs.getString("last_name");
                int age=rs.getInt("age");
    
                //输出数据
                System.out.println("[ID: " + id+",first_name:"+first_name+",last_name:"+last_name+",age:"+age+"]");
            }
            //6 依此关闭ResultSet，PreparedStatement，Connection资源
            shutdownResource(conn,preparedStatement,rs);
        }catch(SQLException se){
            // 处理 JDBC 错误
            se.printStackTrace();
        }catch(Exception e){
            // 处理 Class.forName 错误
            e.printStackTrace();
        }finally{
        	shutdownResource(conn,preparedStatement,rs);
        }
    }
    
    public static void shutdownResource(Connection conn,Statement stmt,ResultSet rs) {
        // 依此关闭ResultSet，PreparedStatement，Connection资源
    	try {
    		if(rs!=null) {
    			rs.close();
    		}
    	}catch(SQLException se1){
    		//TODO
    	}
    	
        try{
            if(stmt!=null) stmt.close();
        }catch(SQLException se2){
        	//TODO
        }
        
        try{
            if(conn!=null) conn.close();
        }catch(SQLException se){
            //TODO
        }
    }

}

```

我们可以根据上面的步骤，将jdbc和mybatis做一个对比：

## 1. Class注册JDBC驱动

### jdbc做法

            //1 注册 JDBC 驱动
            Class.forName(JDBC_DRIVER);
### Mybatis实现

1.静态方法（全局）

UnpooledDataSource.java

```java
  static {
    Enumeration<Driver> drivers = DriverManager.getDrivers();
    while (drivers.hasMoreElements()) {
      Driver driver = drivers.nextElement();
      registeredDrivers.put(driver.getClass().getName(), driver);
    }
  }
```

2.初始化方法

UnpooledDataSource.java

```java
  private synchronized void initializeDriver() throws SQLException {	  
    if (!registeredDrivers.containsKey(driver)) {
      Class<?> driverType;
      try {
        if (driverClassLoader != null) {
          driverType = Class.forName(driver, true, driverClassLoader);
        } else {
          driverType = Resources.classForName(driver);
        }
        // DriverManager requires the driver to be loaded via the system ClassLoader.
        // http://www.kfu.com/~nsayer/Java/dyn-jdbc.html
        Driver driverInstance = (Driver)driverType.getDeclaredConstructor().newInstance();
        DriverManager.registerDriver(new DriverProxy(driverInstance));
        registeredDrivers.put(driver, driverInstance);
      } catch (Exception e) {
        throw new SQLException("Error setting driver on UnpooledDataSource. Cause: " + e);
      }
    }
  }
```

## 2. DriverManager打开链接Connection

### jdbc做法：

```java
conn = DriverManager.getConnection(DB_URL,USER,PASS);
```

### Mybatis做法：

UnpooledDataSource.java

```java
  @Override
  public Connection getConnection() throws SQLException {
    return doGetConnection(username, password);
  }
  private Connection doGetConnection(String username, String password) throws SQLException {
    Properties props = new Properties();
    if (driverProperties != null) {
      props.putAll(driverProperties);
    }
    if (username != null) {
      props.setProperty("user", username);
    }
    if (password != null) {
      props.setProperty("password", password);
    }
    return doGetConnection(props);
  }

  private Connection doGetConnection(Properties properties) throws SQLException {
    initializeDriver();
    Connection connection = DriverManager.getConnection(url, properties);
    configureConnection(connection);
    return connection;
  }
  private void configureConnection(Connection conn) throws SQLException {
    if (defaultNetworkTimeout != null) {
      conn.setNetworkTimeout(Executors.newSingleThreadExecutor(), defaultNetworkTimeout);
    }
    if (autoCommit != null && autoCommit != conn.getAutoCommit()) {
      conn.setAutoCommit(autoCommit);
    }
    if (defaultTransactionIsolationLevel != null) {
      conn.setTransactionIsolation(defaultTransactionIsolationLevel);
    }
  }
```

## 3.定义操作的SQL语句,Connection实例化PreparedStatement对象 

### jdbc做法

```
            //3 定义操作的SQL语句       
            sql = "SELECT id, first_name, last_name,age FROM student where id = ?";
            System.out.println(" 实例化PreparedStatement对象...");
            preparedStatement = conn.prepareStatement(sql);
            preparedStatement.setInt(1, 5);
```

### mybatis做法

MappedStatement

```java
  public BoundSql getBoundSql(Object parameterObject) {
    BoundSql boundSql = sqlSource.getBoundSql(parameterObject);
    List<ParameterMapping> parameterMappings = boundSql.getParameterMappings();
    if (parameterMappings == null || parameterMappings.isEmpty()) {
      boundSql = new BoundSql(configuration, boundSql.getSql(), parameterMap.getParameterMappings(), parameterObject);
    }

    // check for nested result maps in parameter mappings (issue #30)
    for (ParameterMapping pm : boundSql.getParameterMappings()) {
      String rmId = pm.getResultMapId();
      if (rmId != null) {
        ResultMap rm = configuration.getResultMap(rmId);
        if (rm != null) {
          hasNestedResultMaps |= rm.hasNestedResultMaps();
        }
      }
    }

    return boundSql;
  }
```

DefaultParameterHandler

```java
  @Override
  public void setParameters(PreparedStatement ps) {
    ErrorContext.instance().activity("setting parameters").object(mappedStatement.getParameterMap().getId());
    List<ParameterMapping> parameterMappings = boundSql.getParameterMappings();
    if (parameterMappings != null) {
      for (int i = 0; i < parameterMappings.size(); i++) {
        ParameterMapping parameterMapping = parameterMappings.get(i);
        if (parameterMapping.getMode() != ParameterMode.OUT) {
          Object value;
          String propertyName = parameterMapping.getProperty();
          if (boundSql.hasAdditionalParameter(propertyName)) { // issue #448 ask first for additional params
            value = boundSql.getAdditionalParameter(propertyName);
          } else if (parameterObject == null) {
            value = null;
          } else if (typeHandlerRegistry.hasTypeHandler(parameterObject.getClass())) {
            value = parameterObject;
          } else {
            MetaObject metaObject = configuration.newMetaObject(parameterObject);
            value = metaObject.getValue(propertyName);
          }
          TypeHandler typeHandler = parameterMapping.getTypeHandler();
          JdbcType jdbcType = parameterMapping.getJdbcType();
          if (value == null && jdbcType == null) {
            jdbcType = configuration.getJdbcTypeForNull();
          }
          try {
            typeHandler.setParameter(ps, i + 1, value, jdbcType);
          } catch (TypeException | SQLException e) {
            throw new TypeException("Could not set parameters for mapping: " + parameterMapping + ". Cause: " + e, e);
          }
        }
      }
    }
  }
```

BaseStatementHandler

```java
  @Override
  public Statement prepare(Connection connection, Integer transactionTimeout) throws SQLException {
    ErrorContext.instance().sql(boundSql.getSql());
    Statement statement = null;
    try {
      statement = instantiateStatement(connection);
      setStatementTimeout(statement, transactionTimeout);
      setFetchSize(statement);
      return statement;
    } catch (SQLException e) {
      closeStatement(statement);
      throw e;
    } catch (Exception e) {
      closeStatement(statement);
      throw new ExecutorException("Error preparing statement.  Cause: " + e, e);
    }
  }
```



## 4. PreparedStatement执行数据库操作

### jdbc做法

            //5 执行数据库操作
            rs = stmt.executeQuery(sql);
### mybatis做法

PreparedStatementHandler

```java
  @Override
  public <E> List<E> query(Statement statement, ResultHandler resultHandler) throws SQLException {
    PreparedStatement ps = (PreparedStatement) statement;
    ps.execute();
    return resultSetHandler.handleResultSets(ps);
  }
```

## 5. 获取并操作结果集ResultSet

### jdbc做法

```java
            //6 获取并操作结果集
            while(rs.next()){
                // 通过字段检索
                int id  = rs.getInt("id");
                String first_name = rs.getString("first_name");
                String last_name = rs.getString("last_name");
                int age=rs.getInt("age");
    
                //输出数据
                System.out.println("[ID: " + id+",first_name:"+first_name+",last_name:"+last_name+",age:"+age);
            }
```

### mybatis做法

DefaultResultSetHandler

```java
  //
  // HANDLE RESULT SETS
  //
  @Override
  public List<Object> handleResultSets(Statement stmt) throws SQLException {
    ErrorContext.instance().activity("handling results").object(mappedStatement.getId());
    final List<Object> multipleResults = new ArrayList<>();

    int resultSetCount = 0;
    ResultSetWrapper rsw = getFirstResultSet(stmt);

    List<ResultMap> resultMaps = mappedStatement.getResultMaps();
    int resultMapCount = resultMaps.size();
    validateResultMapsCount(rsw, resultMapCount);
    while (rsw != null && resultMapCount > resultSetCount) {
      ResultMap resultMap = resultMaps.get(resultSetCount);
      handleResultSet(rsw, resultMap, multipleResults, null);
      rsw = getNextResultSet(stmt);
      cleanUpAfterHandlingResultSet();
      resultSetCount++;
    }

    String[] resultSets = mappedStatement.getResultSets();
    if (resultSets != null) {
      while (rsw != null && resultSetCount < resultSets.length) {
        ResultMapping parentMapping = nextResultMaps.get(resultSets[resultSetCount]);
        if (parentMapping != null) {
          String nestedResultMapId = parentMapping.getNestedResultMapId();
          ResultMap resultMap = configuration.getResultMap(nestedResultMapId);
          handleResultSet(rsw, resultMap, null, parentMapping);
        }
        rsw = getNextResultSet(stmt);
        cleanUpAfterHandlingResultSet();
        resultSetCount++;
      }
    }
    // throw new NullPointerException();
    return collapseSingleResultList(multipleResults);
  }
```

## 6. 依此关闭ResultSet，PreparedStatement，Connection资源

### jdbc做法

```java
    public static void shutdownResource(Connection conn,Statement stmt,ResultSet rs) {
        // 关闭资源
    	try {
    		if(rs!=null) {
    			rs.close();
    		}
    	}catch(SQLException se1){
    		//TODO
    	}
    	
        try{
            if(stmt!=null) stmt.close();
        }catch(SQLException se2){
        	//TODO
        }
        
        try{
            if(conn!=null) conn.close();
        }catch(SQLException se){
            //TODO
        }
    }
```

### mybatis做法

BaseStatementHandler.java

```java
  protected void closeStatement(Statement statement) {
    try {
      if (statement != null) {
        statement.close();
      }
    } catch (SQLException e) {
      //ignore
    }
  }
```

DefaultResultSetHandler.java

```java
  private void closeResultSet(ResultSet rs) {
    try {
      if (rs != null) {
        rs.close();
      }
    } catch (SQLException e) {
      // ignore
    }
  }
```

JdbcTransaction.java

```java
  @Override
  public void close() throws SQLException {
    if (connection != null) {
      resetAutoCommit();
      if (log.isDebugEnabled()) {
        log.debug("Closing JDBC Connection [" + connection + "]");
      }
      connection.close();
    }
  }
```

### 小结

通过对程序进行调试，我们可以看到JDBC的所有流程，在Mybatis中都能找到对应的实现：

![Jdbc vs Mybatis](img\Jdbc vs Mybatis.png)



## 深入挖掘Mybatis原理

为了更深入的理解内部执行的原理，通过内部埋点的方式，来看看它的执行序列是什么样子的？

在UnpooledDataSource.doGetConnection()埋点：

```java
  private Connection doGetConnection(Properties properties) throws SQLException {
	StackUtils.getStack();
    initializeDriver();
    Connection connection = DriverManager.getConnection(url, properties);
    configureConnection(connection);
    return connection;
  }
```

其中，埋点程序StackUtils如下：

```java
package com.davidwang456;
public class StackUtils {	
	public static void getStack() {
		   java.util.Map<Thread, StackTraceElement[]> ts = Thread.getAllStackTraces();
		    StackTraceElement[] ste = ts.get(Thread.currentThread());
		    int cnt=1;
		    for (int i=ste.length-1;i>0;i--) { 
		    	StackTraceElement s=ste[i];
		    	System.out.println("调用序号："+cnt+"  调用类和方法 "+s.getClassName()+"$"+s.getMethodName());
		    	cnt++;		   
		    }
	}
}
```

运行程序打印出链路和结果如下：

```java
调用序号：1  调用类和方法 com.davidwang456.MapUnderscoreToCamelCaseTest$main
调用序号：2  调用类和方法 com.sun.proxy.$Proxy0$getStudentInfoById
调用序号：3  调用类和方法 org.apache.ibatis.binding.MapperProxy$invoke
调用序号：4  调用类和方法 org.apache.ibatis.binding.MapperProxy$PlainMethodInvoker$invoke
调用序号：5  调用类和方法 org.apache.ibatis.binding.MapperMethod$execute
调用序号：6  调用类和方法 org.apache.ibatis.session.defaults.DefaultSqlSession$selectOne
调用序号：7  调用类和方法 org.apache.ibatis.session.defaults.DefaultSqlSession$selectList
调用序号：8  调用类和方法 org.apache.ibatis.session.defaults.DefaultSqlSession$selectList
调用序号：9  调用类和方法 org.apache.ibatis.executor.CachingExecutor$query
调用序号：10  调用类和方法 org.apache.ibatis.executor.CachingExecutor$query
调用序号：11  调用类和方法 org.apache.ibatis.executor.BaseExecutor$query
调用序号：12  调用类和方法 org.apache.ibatis.executor.BaseExecutor$queryFromDatabase
调用序号：13  调用类和方法 org.apache.ibatis.executor.SimpleExecutor$doQuery
调用序号：14  调用类和方法 org.apache.ibatis.executor.SimpleExecutor$prepareStatement
调用序号：15  调用类和方法 org.apache.ibatis.executor.BaseExecutor$getConnection
调用序号：16  调用类和方法 org.apache.ibatis.transaction.jdbc.JdbcTransaction$getConnection
调用序号：17  调用类和方法 org.apache.ibatis.transaction.jdbc.JdbcTransaction$openConnection
调用序号：18  调用类和方法 org.apache.ibatis.datasource.pooled.PooledDataSource$getConnection
调用序号：19  调用类和方法 org.apache.ibatis.datasource.pooled.PooledDataSource$popConnection
调用序号：20  调用类和方法 org.apache.ibatis.datasource.unpooled.UnpooledDataSource$getConnection
调用序号：21  调用类和方法 org.apache.ibatis.datasource.unpooled.UnpooledDataSource$doGetConnection
调用序号：22  调用类和方法 org.apache.ibatis.datasource.unpooled.UnpooledDataSource$doGetConnection
调用序号：23  调用类和方法 com.davidwang456.StackUtils$getStack
调用序号：24  调用类和方法 java.lang.Thread$getAllStackTraces
student [id=5, first_name=wang5, last_name=david5, age=25]
```

上面的链路，也可以通过单步调试得到。

## 总结

通过上面的分析和调试，使我们对mybatis的执行链路有了较完整的印象：

![Mybatis时序图](img\Mybatis时序图.png)

它的核心类有：

- Configuration：包含数据源、事务、mapper文件资源以及影响数据库行为属性的各种设置settings；
- SqlSession：类似于JDBC中的Connection.负责应用程序与持久层之间执行交互操作；
- StatementHandler包含了SimpleStatementHandler，PreparedStatementHandler，CallableStatementHandler包装了操作数据库SQL语句的对象；
- DefaultParameterHandler对动态sql的参数进行设置，内部调用TypeHandler的各种类型实现；
- DefaultResultSetHandler对结果集进行处理后返回；
- MappedStatement：维护了一条<select|update|delete|insert>节点的封装。

这样，Mybatis的原理就很清楚了。

事前：加载配置到Configuration；

事中：StatementHandler处理对应的MappedStatement(从Configuration中获取)，将结果返回给客户端；

事后：依此关闭ResultSet，PreparedStatement，Connection等资源