# mybatis如何对事务进行管理的？

## 背景

> 小白：师傅，在学习数据库知识的时候，我们学到了事务及事务隔离级别，这些在Mybatis里是如何实现的呢？
>
> 扫地僧：事务是指的是一个业务上的最小不可再分单元，通常一个事务对应了一个完整的业务，这个业务通常由一批SQL语句组成，这批SQL要么同时成功，要么同时失败。像银行业务中，A转账给B 500元，银行会将A账户减少500元，B账户增加500元这两个操作组成一个事务，要么同时成功，要么同时失败。Mybatis支持事务的实现及事务的隔离级别。
>
> 小白：看文档介绍，在我们的工程里，事务的开启跟关闭是由Spring负责的，我就搞不清楚到底是Mybatis还是Spring在负责事务了？
>
> 扫地僧：Mybatis支持两种事务的方式：一种JDBC方式，由基础的JDBC来控制事务；第三方(容器)方式，事务交由第三方(容器)来控制。为了让你更明白其中的差异，我们来看两个实例吧！

## 基础JDBC管理事务实例(无Mybatis)

以mysql为例，mysql 事务管理的语法如下：

```mysql
START TRANSACTION
    [transaction_characteristic [, transaction_characteristic] ...]

transaction_characteristic: {
    WITH CONSISTENT SNAPSHOT
  | READ WRITE
  | READ ONLY
}

BEGIN [WORK]
COMMIT [WORK] [AND [NO] CHAIN] [[NO] RELEASE]
ROLLBACK [WORK] [AND [NO] CHAIN] [[NO] RELEASE]
SET autocommit = {0 | 1}
```

实现mysql事务管理代码如下：

```java
             con.setAutoCommit(false);
             //此处命令通知数据库,从此刻开始从当前Connection通道推送而来的
             //SQL语句属于同一个业务中这些SQL语句在数据库中应该保存到同一个
             //Transaction中.这个Transaction的行为(commit,rollback)由当前Connection管理.
              try{
                  //推送sql语句命令……..;
                  con.commit();//通知Transaction提交.
              }catch(SQLException ex){
                  con.rollback();//通知Transaction回滚.
              }

```

### 数据脚本准备

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

### 基础JDBC实例

测试程序(以年龄转移为例)

```java
package com.davidwang456.mybatis.transaction;

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
        PreparedStatement preparedStatement2 = null;
        String sql="";
        String sql2="";
        try{
            //1 注册 JDBC 驱动
            Class.forName(JDBC_DRIVER);
        
            //2 打开链接
            System.out.println("连接数据库...");
            conn = DriverManager.getConnection(DB_URL,USER,PASS);
            
            
            //3 定义操作的SQL语句,实例化PreparedStatement对象    
            //开始事务
            conn.setAutoCommit(false);
            try{
                sql = "update student set age=age+1 where id = ?";
                
                System.out.println(" 实例化PreparedStatement对象...");
                preparedStatement = conn.prepareStatement(sql);
                preparedStatement.setInt(1, 1);
                //4 执行数据库操作
                preparedStatement.executeUpdate();

                sql2 = "update student set age=age-1 where id = ?";
                preparedStatement2 = conn.prepareStatement(sql2);
                preparedStatement2.setInt(1, 2);
                //4 执行数据库操作
                preparedStatement2.executeUpdate();
            
                
                conn.commit();//通知Transaction提交.
            }catch(SQLException ex){
                conn.rollback();//通知Transaction回滚.
            }
            //6 完成后关闭
            // 关闭资源
            shutdownResource(conn,preparedStatement,null);
        }catch(SQLException se){
            // 处理 JDBC 错误
            se.printStackTrace();
        }catch(Exception e){
            // 处理 Class.forName 错误
            e.printStackTrace();
        }finally{
        	shutdownResource(conn,preparedStatement,null);
        	shutdownResource(conn,preparedStatement2,null);
        }
    }
    
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

}
```

运行程序后，数据库数据：

```tex
    id  first_name  last_name     age  
------  ----------  ---------  --------
     1  wang1       david1           22
     2  wang2       david2           21
     3  wang3       david3           23
     4  wang4       david4           24
     5  wang5       david5           25
     6  wang6       david6           26
     7  wang7       david7           27
     8  wang8       david8           28
```

id为1，2的年龄值在一个事务内分别做了＋1和-1操作。

## Mybatis提供的JDBC事务管理

重新初始化脚本。

### 创建maven项目

**pom依赖**

```xml
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>TransactionTest</artifactId>
  <version>1.6.0-SNAPSHOT</version>
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

**实体类**

```java
package com.davidwang456.mybatis.transaction;

import java.io.Serializable;
import java.util.Date;
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
	    		 + ", lastName=" + lastName + ", age=" +age+ 
	    		 ']';
	   }
}
```

**配置**

在Mybatis的配置文件中可以配置事务管理方式如下：

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
            <property name = "url" value = "jdbc:mysql://localhost:3306/davidwang456?characterEncoding=UTF-8&amp;useSSL=false&amp;useLegacyDatetimeCode=false&amp;serverTimezone=GMT%2B8"/>
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

注意：<transactionManager type="JDBC" /> 实现

映射文件

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
        PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.transaction.StudentMapper">	
	<insert id="insert" parameterType="com.davidwang456.mybatis.transaction.StudentDTO" >
		INSERT INTO `student`(`id`, `first_name`, `last_name`, `age`) values(#{id},#{firstName},#{lastName},#{age})
	</insert>
</mapper>
```

映射类

```java
package com.davidwang456.mybatis.transaction;

public interface StudentMapper {
	public void insert(StudentDTO studentDTO);
}

```

测试程序

```java
package com.davidwang456.mybatis.transaction;

import java.io.IOException;
import java.io.Reader;
import java.text.ParseException;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;

public class MybatisJdbcTest {

	public static void main(String[] args) throws IOException, ParseException {
		testTransaction();
	   }
	
	
	public static void testTransaction() throws IOException, ParseException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession(false);      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
    	  StudentDTO dto=new StudentDTO();
    	  dto.setAge(29);
    	  dto.setFirstName("wangwei9");
    	  dto.setLastName("david9");
    	  dto.setId(9);
	      studentMapper.insert(dto);
	      
    	  StudentDTO dto2=new StudentDTO();
    	  dto2.setAge(30);
    	  dto2.setFirstName("wangwei10");
    	  dto2.setLastName("david10");
    	  dto2.setId(10);
	      studentMapper.insert(dto2);
	      session.commit(true);
	      session.close();	
	}
}
```

运行程序后，数据库数据如下所示：

```tex
    id  first_name  last_name     age  
------  ----------  ---------  --------
     1  wang1       david1           21
     2  wang2       david2           22
     3  wang3       david3           23
     4  wang4       david4           24
     5  wang5       david5           25
     6  wang6       david6           26
     7  wang7       david7           27
     8  wang8       david8           28
     9  wangwei9    david9           29
    10  wangwei10   david10          30
```

## Spring集成Mybatis管理事务

配置类SpringConfig

```java
package com.davidwang456.mybatis.spring;

import javax.sql.DataSource;

import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.transaction.managed.ManagedTransactionFactory;
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
      //事务由Spring来管理
      sessionFactory.setTransactionFactory(new ManagedTransactionFactory());
      return sessionFactory.getObject();
   }
}
```

测试程序如下：

```java
package com.davidwang456.mybatis.spring;

import java.io.IOException;
import java.util.List;

import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.TransactionIsolationLevel;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;

import com.davidwang456.mybatis.spring.mapper.StudentMapper;


public class SpringIntegrateMybatisTest {

	@SuppressWarnings("resource")
	public static void main(String[] args) throws IOException {
		 AnnotationConfigApplicationContext ctx = new AnnotationConfigApplicationContext(SpringConfig.class);
		 SqlSessionFactory ssf=ctx.getBean(SqlSessionFactory.class);
		 ssf.openSession(TransactionIsolationLevel.REPEATABLE_READ);
		 SqlSession session=ssf.openSession();
		 //session.getConfiguration().addMapper(StudentMapper.class);		 
		 StudentMapper studentMapper=session.getMapper(StudentMapper.class);
		 testTransaction(studentMapper);
			/*
			 * DataSourceTransactionManager dtm=
			 * ctx.getBean(DataSourceTransactionManager.class); TransactionTemplate
			 * transactionTemplate = new TransactionTemplate(dtm);
			 * transactionTemplate.execute(txStatus -> { StudentDTO dto=new StudentDTO();
			 * dto.setAge(30); dto.setFirstName("wangwei11"); dto.setLastName("david111");
			 * dto.setId(11); studentMapper.insert(dto); return null; });
			 */
 
	   }
	@Transactional
	private static void testTransaction(StudentMapper studentMapper) {
  	  StudentDTO dto=new StudentDTO();
  	  dto.setAge(29);
  	  dto.setFirstName("wangwei9");
  	  dto.setLastName("david9");
  	  dto.setId(9);
	  studentMapper.insert(dto);
	      
  	  StudentDTO dto2=new StudentDTO();
  	  dto2.setAge(30);
  	  dto2.setFirstName("wangwei10");
  	  dto2.setLastName("david10");
  	  dto2.setId(10);
	  studentMapper.insert(dto2);
	}
}
```

初始化数据库，运行程序，结果如JDBC事务管理方式一致。

## 深入Mybatis事务内部原理

Mybatis管理事务是分为两种方式: Configuration.java

```java
    typeAliasRegistry.registerAlias("JDBC", JdbcTransactionFactory.class);
    typeAliasRegistry.registerAlias("MANAGED", ManagedTransactionFactory.class);
```

(1)使用JDBC的事务管理机制,就是利用java.sql.Connection对象完成对事务的提交

(2)使用MANAGED的事务管理机制，这种机制mybatis自身不会去实现事务管理，而是让程序的容器来实现对事务的管理

Mybatis提供了一个事务接口Transaction，以及两个实现类jdbcTransaction和ManagedTransaction，当spring与Mybatis一起使用时，spring提供了一个实现类SpringManagedTransaction
Transaction接口：提供的抽象方法有获取数据库连接getConnection，提交事务commit，回滚事务rollback和关闭连接close，源码如下：

```java
//事务接口  
ublic interface Transaction {  
 /** 
  * Retrieve inner database connection 
  * @return DataBase connection 
  * @throws SQLException 
  */  
  //获得数据库连接  
 Connection getConnection() throws SQLException;  
 /** 
  * 提交 
  * Commit inner database connection. 
  * @throws SQLException 
  */  
 void commit() throws SQLException;  
 /** 
  * 回滚 
  * Rollback inner database connection. 
  * @throws SQLException 
  */  
 void rollback() throws SQLException;  
 /** 
  * 关闭连接 
  * Close inner database connection. 
  * @throws SQLException 
  */  
 void close() throws SQLException; 
```

JdbcTransaction实现类：Transaction的实现类，通过使用jdbc提供的方式来管理事务，通过Connection提供的事务管理方法来进行事务管理，源码如下：

```java
/**
 *    Copyright 2009-2020 the original author or authors.
 *
 *    Licensed under the Apache License, Version 2.0 (the "License");
 *    you may not use this file except in compliance with the License.
 *    You may obtain a copy of the License at
 *
 *       http://www.apache.org/licenses/LICENSE-2.0
 *
 *    Unless required by applicable law or agreed to in writing, software
 *    distributed under the License is distributed on an "AS IS" BASIS,
 *    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *    See the License for the specific language governing permissions and
 *    limitations under the License.
 */
package org.apache.ibatis.transaction.jdbc;

import java.sql.Connection;
import java.sql.SQLException;

import javax.sql.DataSource;

import org.apache.ibatis.logging.Log;
import org.apache.ibatis.logging.LogFactory;
import org.apache.ibatis.session.TransactionIsolationLevel;
import org.apache.ibatis.transaction.Transaction;
import org.apache.ibatis.transaction.TransactionException;

/**
 * {@link Transaction} that makes use of the JDBC commit and rollback facilities directly.
 * It relies on the connection retrieved from the dataSource to manage the scope of the transaction.
 * Delays connection retrieval until getConnection() is called.
 * Ignores commit or rollback requests when autocommit is on.
 *
 * @author Clinton Begin
 *
 * @see JdbcTransactionFactory
 */
public class JdbcTransaction implements Transaction {

  private static final Log log = LogFactory.getLog(JdbcTransaction.class);

  protected Connection connection;
  protected DataSource dataSource;
  protected TransactionIsolationLevel level;
  protected boolean autoCommit;

  public JdbcTransaction(DataSource ds, TransactionIsolationLevel desiredLevel, boolean desiredAutoCommit) {
    dataSource = ds;
    level = desiredLevel;
    autoCommit = desiredAutoCommit;
  }

  public JdbcTransaction(Connection connection) {
    this.connection = connection;
  }

  @Override
  public Connection getConnection() throws SQLException {
    if (connection == null) {
      openConnection();
    }
    return connection;
  }

  @Override
  public void commit() throws SQLException {
    if (connection != null && !connection.getAutoCommit()) {
      if (log.isDebugEnabled()) {
        log.debug("Committing JDBC Connection [" + connection + "]");
      }
      connection.commit();
    }
  }

  @Override
  public void rollback() throws SQLException {
    if (connection != null && !connection.getAutoCommit()) {
      if (log.isDebugEnabled()) {
        log.debug("Rolling back JDBC Connection [" + connection + "]");
      }
      connection.rollback();
    }
  }

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

  protected void setDesiredAutoCommit(boolean desiredAutoCommit) {
    try {
      if (connection.getAutoCommit() != desiredAutoCommit) {
        if (log.isDebugEnabled()) {
          log.debug("Setting autocommit to " + desiredAutoCommit + " on JDBC Connection [" + connection + "]");
        }
        connection.setAutoCommit(desiredAutoCommit);
      }
    } catch (SQLException e) {
      // Only a very poorly implemented driver would fail here,
      // and there's not much we can do about that.
      throw new TransactionException("Error configuring AutoCommit.  "
          + "Your driver may not support getAutoCommit() or setAutoCommit(). "
          + "Requested setting: " + desiredAutoCommit + ".  Cause: " + e, e);
    }
  }

  protected void resetAutoCommit() {
    try {
      if (!connection.getAutoCommit()) {
        // MyBatis does not call commit/rollback on a connection if just selects were performed.
        // Some databases start transactions with select statements
        // and they mandate a commit/rollback before closing the connection.
        // A workaround is setting the autocommit to true before closing the connection.
        // Sybase throws an exception here.
        if (log.isDebugEnabled()) {
          log.debug("Resetting autocommit to true on JDBC Connection [" + connection + "]");
        }
        connection.setAutoCommit(true);
      }
    } catch (SQLException e) {
      if (log.isDebugEnabled()) {
        log.debug("Error resetting autocommit to true "
            + "before closing the connection.  Cause: " + e);
      }
    }
  }

  protected void openConnection() throws SQLException {
    if (log.isDebugEnabled()) {
      log.debug("Opening JDBC Connection");
    }
    connection = dataSource.getConnection();
    if (level != null) {
      connection.setTransactionIsolation(level.getLevel());
    }
    setDesiredAutoCommit(autoCommit);
  }

  @Override
  public Integer getTimeout() throws SQLException {
    return null;
  }

}
```

ManagedTransaction实现类：通过容器来进行事务管理，所有它对事务提交和回滚并不会做任何操作，源码如下：

```java
/**
 *    Copyright 2009-2020 the original author or authors.
 *
 *    Licensed under the Apache License, Version 2.0 (the "License");
 *    you may not use this file except in compliance with the License.
 *    You may obtain a copy of the License at
 *
 *       http://www.apache.org/licenses/LICENSE-2.0
 *
 *    Unless required by applicable law or agreed to in writing, software
 *    distributed under the License is distributed on an "AS IS" BASIS,
 *    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *    See the License for the specific language governing permissions and
 *    limitations under the License.
 */
package org.apache.ibatis.transaction.managed;

import java.sql.Connection;
import java.sql.SQLException;

import javax.sql.DataSource;

import org.apache.ibatis.logging.Log;
import org.apache.ibatis.logging.LogFactory;
import org.apache.ibatis.session.TransactionIsolationLevel;
import org.apache.ibatis.transaction.Transaction;

/**
 * {@link Transaction} that lets the container manage the full lifecycle of the transaction.
 * Delays connection retrieval until getConnection() is called.
 * Ignores all commit or rollback requests.
 * By default, it closes the connection but can be configured not to do it.
 *
 * @author Clinton Begin
 *
 * @see ManagedTransactionFactory
 */
public class ManagedTransaction implements Transaction {

  private static final Log log = LogFactory.getLog(ManagedTransaction.class);

  private DataSource dataSource;
  private TransactionIsolationLevel level;
  private Connection connection;
  private final boolean closeConnection;

  public ManagedTransaction(Connection connection, boolean closeConnection) {
    this.connection = connection;
    this.closeConnection = closeConnection;
  }

  public ManagedTransaction(DataSource ds, TransactionIsolationLevel level, boolean closeConnection) {
    this.dataSource = ds;
    this.level = level;
    this.closeConnection = closeConnection;
  }

  @Override
  public Connection getConnection() throws SQLException {
    if (this.connection == null) {
      openConnection();
    }
    return this.connection;
  }

  @Override
  public void commit() throws SQLException {
    // Does nothing
  }

  @Override
  public void rollback() throws SQLException {
    // Does nothing
  }

  @Override
  public void close() throws SQLException {
    if (this.closeConnection && this.connection != null) {
      if (log.isDebugEnabled()) {
        log.debug("Closing JDBC Connection [" + this.connection + "]");
      }
      this.connection.close();
    }
  }

  protected void openConnection() throws SQLException {
    if (log.isDebugEnabled()) {
      log.debug("Opening JDBC Connection");
    }
    this.connection = this.dataSource.getConnection();
    if (this.level != null) {
      this.connection.setTransactionIsolation(this.level.getLevel());
    }
  }

  @Override
  public Integer getTimeout() throws SQLException {
    return null;
  }

}
```

SpringManagedTransaction实现类：它其实也是通过使用JDBC来进行事务管理的，当spring的事务管理有效时，不需要操作commit/rollback/close，spring事务管理会自动帮我们完成，源码如下：

```java
/**
 * Copyright 2010-2019 the original author or authors.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *    http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package org.mybatis.spring.transaction;

import static org.springframework.util.Assert.notNull;

import java.sql.Connection;
import java.sql.SQLException;

import javax.sql.DataSource;

import org.apache.ibatis.transaction.Transaction;
import org.mybatis.logging.Logger;
import org.mybatis.logging.LoggerFactory;
import org.springframework.jdbc.datasource.ConnectionHolder;
import org.springframework.jdbc.datasource.DataSourceUtils;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/**
 * {@code SpringManagedTransaction} handles the lifecycle of a JDBC connection. It retrieves a connection from Spring's
 * transaction manager and returns it back to it when it is no longer needed.
 * <p>
 * If Spring's transaction handling is active it will no-op all commit/rollback/close calls assuming that the Spring
 * transaction manager will do the job.
 * <p>
 * If it is not it will behave like {@code JdbcTransaction}.
 *
 * @author Hunter Presnall
 * @author Eduardo Macarron
 */
public class SpringManagedTransaction implements Transaction {

  private static final Logger LOGGER = LoggerFactory.getLogger(SpringManagedTransaction.class);

  private final DataSource dataSource;

  private Connection connection;

  private boolean isConnectionTransactional;

  private boolean autoCommit;

  public SpringManagedTransaction(DataSource dataSource) {
    notNull(dataSource, "No DataSource specified");
    this.dataSource = dataSource;
  }

  /**
   * {@inheritDoc}
   */
  @Override
  public Connection getConnection() throws SQLException {
    if (this.connection == null) {
      openConnection();
    }
    return this.connection;
  }

  /**
   * Gets a connection from Spring transaction manager and discovers if this {@code Transaction} should manage
   * connection or let it to Spring.
   * <p>
   * It also reads autocommit setting because when using Spring Transaction MyBatis thinks that autocommit is always
   * false and will always call commit/rollback so we need to no-op that calls.
   */
  private void openConnection() throws SQLException {
    this.connection = DataSourceUtils.getConnection(this.dataSource);
    this.autoCommit = this.connection.getAutoCommit();
    this.isConnectionTransactional = DataSourceUtils.isConnectionTransactional(this.connection, this.dataSource);

    LOGGER.debug(() -> "JDBC Connection [" + this.connection + "] will"
        + (this.isConnectionTransactional ? " " : " not ") + "be managed by Spring");
  }

  /**
   * {@inheritDoc}
   */
  @Override
  public void commit() throws SQLException {
    if (this.connection != null && !this.isConnectionTransactional && !this.autoCommit) {
      LOGGER.debug(() -> "Committing JDBC Connection [" + this.connection + "]");
      this.connection.commit();
    }
  }

  /**
   * {@inheritDoc}
   */
  @Override
  public void rollback() throws SQLException {
    if (this.connection != null && !this.isConnectionTransactional && !this.autoCommit) {
      LOGGER.debug(() -> "Rolling back JDBC Connection [" + this.connection + "]");
      this.connection.rollback();
    }
  }

  /**
   * {@inheritDoc}
   */
  @Override
  public void close() throws SQLException {
    DataSourceUtils.releaseConnection(this.connection, this.dataSource);
  }

  /**
   * {@inheritDoc}
   */
  @Override
  public Integer getTimeout() throws SQLException {
    ConnectionHolder holder = (ConnectionHolder) TransactionSynchronizationManager.getResource(dataSource);
    if (holder != null && holder.hasTimeout()) {
      return holder.getTimeToLiveInSeconds();
    }
    return null;
  }

}
```

## 总结

Mybatis的事务管理机制还是比较简单的，其并没有做过多的操作，只是封装一下方便别人调用而已。



