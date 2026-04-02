# ORM框架之争：mybatis，hibernate，jpa

## 背景

> 小白：师傅，我曾经面试一家公司，面试官问我懂不懂Hibernate，懂不懂JPA，我说不懂，我只用过Mybatis，面试官就让我回去等消息了！
>
> 扫地僧：以懂不懂Hibernate，JPA来判断面试的标准，肯定是面试官的问题，框架是为了降低我们开发的难度，提升我们的开发效率，不是阻扰我们的障碍，Hibernate和Mybatis都是对JDBC的封装，Spring提供了对两者的支持。
>
> ![mybatis](img\mybatis.png)
>
> 那么我们就用几分钟来演示一个简单使用hibernate的示例，来看看它和Mybatis有什么不同吧！

## Hibernate示例

### 数据库表准备

准备数据如上面的章节

创建数据库www和表

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
INSERT INTO `student` VALUES (1, 'wang1', 'david1', 25);
INSERT INTO `student` VALUES (2, 'wang2', 'david2', 25);
INSERT INTO `student` VALUES (3, 'wang3', 'david3', 25);
INSERT INTO `student` VALUES (4, 'wang4', 'david4', 25);
INSERT INTO `student` VALUES (5, 'wang5', 'david5', 25);
INSERT INTO `student` VALUES (6, 'wang6', 'david6', 25);
INSERT INTO `student` VALUES (7, 'wang7', 'david7', 25);
INSERT INTO `student` VALUES (8, 'wang8', 'david8', 25);
```

### 创建maven项目

完整的项目结构如下：

![hibernateTest](img\hibernateTest.png)

步骤如下：

####  添加依赖

pom.xml添加lombok，hibernate，jdbc依赖

```xml
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>HibernateTest</artifactId>
  <version>1.3.0-SNAPSHOT</version>
  
  <dependencies>
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
	    <groupId>org.hibernate</groupId>
	    <artifactId>hibernate-core</artifactId>
	    <version>5.4.10.Final</version>
	</dependency>	
<!-- 	<dependency>
	    <groupId>org.hibernate</groupId>
	    <artifactId>hibernate-entitymanager</artifactId>
	    <version>5.4.10.Final</version>
	</dependency>	 -->	  
  </dependencies>
</project>
```

#### 添加hibernate配置文件

hibernate.cfg.xml

```xml
<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE hibernate-configuration PUBLIC
"-//Hibernate/Hibernate Configuration DTD//EN"
"http://hibernate.sourceforge.net/hibernate-configuration-5.0.dtd">

<hibernate-configuration>
<session-factory>
<property name="hibernate.connection.driver_class">com.mysql.cj.jdbc.Driver</property>
<property name="hibernate.connection.url">jdbc:mysql://localhost:3306/davidwang456?characterEncoding=UTF-8&amp;useSSL=false&amp;useLegacyDatetimeCode=false&amp;serverTimezone=UTC</property>
<property name="hibernate.connection.username">root</property>
<property name="hibernate.connection.password">wangwei456</property>
<property name="hibernate.connection.pool_size">10</property>
<property name="show_sql">true</property>
<property name="dialect">org.hibernate.dialect.MySQLDialect</property>
<property name="hibernate.current_session_context_class">thread</property>

<mapping class="com.davidwang456.mybatis.hibernate.entity.Student" />

</session-factory>
</hibernate-configuration>
```

配置文件做了两件事情：

- 配置了sessionFactory
- 将Student定义为entity

#### 实体类Student

```java
package com.davidwang456.mybatis.hibernate.entity;

import javax.persistence.Column;
import javax.persistence.Entity;
import javax.persistence.GeneratedValue;
import javax.persistence.GenerationType;
import javax.persistence.Id;
import javax.persistence.Table;

import lombok.Data;

@Entity
@Table(name="student")
@Data
public class Student {
	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	private int id;
	@Column(name="first_name")
	private String firstName;
	@Column(name="last_name")
	private String lastName;
	@Column(name="age")
	private int age;

	@Override
	public String toString() {
		return "Id= " + id + " First Name= " + 
				firstName + " Last Name= " + lastName + 
	           " age= "+ age;
	}
}
```

#### 测试类

```java
package com.davidwang456.mybatis.hibernate;

import java.util.List;

import javax.persistence.criteria.CriteriaQuery;

import org.hibernate.HibernateException;
import org.hibernate.Session;
import org.hibernate.SessionFactory;
import org.hibernate.Transaction;

import com.davidwang456.mybatis.hibernate.entity.Student;
import com.davidwang456.mybatis.hibernate.util.HibernateUtil;

public class HibernateTest {

	public static void main(String[] args) {
		getAllData();

	}
	
	   public static void deleteStudent(Integer studentID){
	      SessionFactory sessFact = HibernateUtil.getSessionFactory();
		  Session session = sessFact.getCurrentSession();
	      Transaction tx = null;
	      try{
	         tx = session.beginTransaction();
	         Student Student = 
	                   (Student)session.get(Student.class, studentID); 
	         session.delete(Student); 
	         tx.commit();
	      }catch (HibernateException e) {
	         if (tx!=null) tx.rollback();
	         e.printStackTrace(); 
	      }finally {
	         session.close(); 
	      }
	   }
		
		public static void updateStudent(Integer StudentID, Integer age ){
			SessionFactory sessFact = HibernateUtil.getSessionFactory();
			Session session = sessFact.getCurrentSession();
		      Transaction tx = null;
		      try{
		         tx = session.beginTransaction();
		         Student Student = 
		                    (Student)session.get(Student.class, StudentID); 
		         Student.setAge(age);
		         session.update(Student); 
		         tx.commit();
		      }catch (HibernateException e) {
		         if (tx!=null) tx.rollback();
		         e.printStackTrace(); 
		      }finally {
		         session.close(); 
		      }
		   }
		
		
		public static void getAllData() {
			SessionFactory sessFact = HibernateUtil.getSessionFactory();
			Session session = sessFact.getCurrentSession();
			Transaction tr = session.beginTransaction();
			
			CriteriaQuery<Student> cq = session.getCriteriaBuilder().createQuery(Student.class);
			cq.from(Student.class);
			List<Student> StudentList = session.createQuery(cq).getResultList();

			for (Student student : StudentList) {
				System.out.println(student.toString());
			}  
			

			tr.commit();
			System.out.println("Data printed");
			sessFact.close();
		}
		
		public static void saveStudent() {
			SessionFactory sessFact = HibernateUtil.getSessionFactory();
			Session session = sessFact.getCurrentSession();
			Transaction tr = session.beginTransaction();
			Student emp = new Student();
			emp.setFirstName("david");
			emp.setLastName("000000");
			emp.setAge(20);
			session.save(emp);
			tr.commit();
			System.out.println("Successfully inserted");
			sessFact.close();
		}
}
```

上面的代码中，Hibernate完成增删改查操作。

其中获取SessionFactory的工具类HibernateUtil的代码如下：

```java
package com.davidwang456.mybatis.hibernate.util;
import org.hibernate.SessionFactory;
import org.hibernate.boot.Metadata;
import org.hibernate.boot.MetadataSources;
import org.hibernate.boot.registry.StandardServiceRegistry;
import org.hibernate.boot.registry.StandardServiceRegistryBuilder;

public class HibernateUtil {
	private static final SessionFactory sessionFactory;

	static {
		try {
		StandardServiceRegistry standardRegistry = new StandardServiceRegistryBuilder()
														.configure("hibernate.cfg.xml")
														.build();
		Metadata metaData =new MetadataSources(standardRegistry)
										.getMetadataBuilder()
										.build();
			sessionFactory = metaData.getSessionFactoryBuilder().build();
			
		} catch (Throwable th) {

			System.err.println("Enitial SessionFactory creation failed" + th);
			throw new ExceptionInInitializerError(th);

		}
	}
	public static SessionFactory getSessionFactory() {
		return sessionFactory;
	}
}
```

####  测试结果

##### 查询

main方法调用getAllData(),打印日志：

```java
Hibernate: select student0_.id as id1_0_, student0_.age as age2_0_, student0_.first_name as first_na3_0_, student0_.last_name as last_nam4_0_ from student student0_
Id= 1 First Name= wang1 Last Name= david1 age= 25
Id= 2 First Name= wang2 Last Name= david2 age= 25
Id= 3 First Name= wang3 Last Name= david3 age= 25
Id= 4 First Name= wang4 Last Name= david4 age= 25
Id= 5 First Name= wang5 Last Name= david5 age= 25
Id= 6 First Name= wang6 Last Name= david6 age= 25
Id= 7 First Name= wang7 Last Name= david7 age= 25
Id= 8 First Name= wang8 Last Name= david8 age= 25
Data printed
```

Hibernate自动生成查询语句，打印查询结果。

##### 保存

main方法调用saveStudent()，打印日志：

```java
Hibernate: insert into student (age, first_name, last_name) values (?, ?, ?)
Successfully inserted
```

查询数据库，得到记录

```java
id  first_name  last_name age
9	david	     000000	20
```

##### 修改

main方法调用updateStudent(9,25)，打印日志：

```java
Hibernate: select student0_.id as id1_0_0_, student0_.age as age2_0_0_, student0_.first_name as first_na3_0_0_, student0_.last_name as last_nam4_0_0_ from student student0_ where student0_.id=?
Hibernate: update student set age=?, first_name=?, last_name=? where id=?
```

先查询，后修改。查询数据库，得到记录：

```java
id  first_name  last_name age
9	david	     000000	25
```

##### 删除

main方法调用deleteStudent(9)，打印日志：

```java
Hibernate: select student0_.id as id1_0_0_, student0_.age as age2_0_0_, student0_.first_name as first_na3_0_0_, student0_.last_name as last_nam4_0_0_ from student student0_ where student0_.id=?
Hibernate: delete from student where id=?
```

先查询，后删除。查询数据库，得到记录：

```java
id  first_name  last_name age

```

## Mybatis vs Hibernate

> 扫地僧：通过上面的实例，谈谈Mybatis 和Hibernate最大的不同是什么吧？
>
> 小白：1. Hibernate和Mybatis都是封装了Jdbc，流程基本相似。但Hibernate完全不用写sql，都是通过对象来操作，这个是Hibernate的优势。
>
> 2.Hibernate使用起来，可能会影响性能，如查询的时候会查询所有的列，而不是需要的列，更新和删除的时候都是先查询，后更新或者删除。
>
> 3.Hibernate 因为使用了实体对象，故应该是支持不同数据库类型之间的无缝切换，Mybatis是自己写sql的，不支持不同数据库类型之间的无缝切换。
>
> 扫地僧：Hibernate属于全自动ORM映射工具，使用Hibernate查询关联对象或者关联集合对象时，可以根据对象关系模型直接获取，所以它是全自动的。而Mybatis在查询关联对象或关联集合对象时，需要手动编写sql来完成，所以，称之为半自动ORM映射工具。
>
> 小白：那么，什么是JPA呢？它和Hibernate有什么关系呢？
>
> 扫地僧：JPA全称为Java Persistence API（Java持久层API），它是Sun公司在JavaEE 5中提出的Java持久化规范（JSR-338）。它为Java开发人员提供了一种对象/关联映射工具，来管理Java应用中的关系数据，JPA吸取了目前Java持久化技术的优点，旨在规范、简化Java对象的持久化工作。很多ORM框架都是实现了JPA的规范，如：Hibernate、EclipseLink。
>
> ![jpa](img\jpa.png)
>
> 注意：JPA和Hibernate的关系：JPA是一个规范，而不是框架，Hibernate其实是JPA的一种实现，而Spring Data JPA是一个JPA数据访问抽象。也就是说Spring Data JPA不是一个实现或JPA提供的程序，它只是一个抽象层，主要用于减少为各种持久层存储实现数据访问层所需的样板代码量。但是它还是需要JPA提供实现程序，其实Spring Data JPA底层就是使用的 Hibernate实现。那我们就通过一个实例来看看Spring JPA怎么使用Hibernate的示例。

## Spring JPA示例

### 概述

​		Spring Data JPA是在实现了JPA规范的基础上封装的一套 JPA 应用框架，虽然ORM框架都实现了JPA规范（官方地址为：https://spring.io/projects/spring-data-jpa），但是在不同的ORM框架之间切换仍然需要编写不同的代码，而使用Spring Data JPA能够方便大家在不同的ORM框架之间进行切换而不需要更改代码。Spring Data JPA旨在通过将统一ORM框架的访问持久层的操作，来提高开发人的效率。

Spring Data JPA给我们提供的主要的类和接口

Repository 接口：

- Repository
- CrudRepository
- JpaRepository

Repository 实现类：

- SimpleJpaRepository
- QueryDslJpaRepository

以上这些类和接口就是我们以后在使用Spring Data JPA的时候需要掌握的。

Spring Data JPA示例步骤如下：

### 数据库准备

准备数据如上面的章节,如有的话，请忽略！

创建数据库www和表

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
INSERT INTO `student` VALUES (1, 'wang1', 'david1', 25);
INSERT INTO `student` VALUES (2, 'wang2', 'david2', 25);
INSERT INTO `student` VALUES (3, 'wang3', 'david3', 25);
INSERT INTO `student` VALUES (4, 'wang4', 'david4', 25);
INSERT INTO `student` VALUES (5, 'wang5', 'david5', 25);
INSERT INTO `student` VALUES (6, 'wang6', 'david6', 25);
INSERT INTO `student` VALUES (7, 'wang7', 'david7', 25);
INSERT INTO `student` VALUES (8, 'wang8', 'david8, 25);
```

### 创建maven工程

完整工程目录如下：

![1604026725698](img\springdatajpatest.png)

#### 添加依赖

pom.xml添加lombok，spring-data-jpa,hibernate ，jdbc支持

```java
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>SpringDataJpaTest</artifactId>
  <version>1.3.1-SNAPSHOT</version>
  
    <dependencies>
	<dependency>
	    <groupId>org.springframework.data</groupId>
	    <artifactId>spring-data-jpa</artifactId>
	    <version>2.2.9.RELEASE</version>
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
	    <groupId>org.hibernate</groupId>
	    <artifactId>hibernate-core</artifactId>
	    <version>5.4.10.Final</version>
	</dependency>	
	<dependency>
	    <groupId>org.hibernate</groupId>
	    <artifactId>hibernate-entitymanager</artifactId>
	    <version>5.4.10.Final</version>
	</dependency>	
</dependencies>
</project>
```

#### jpa配置及数据库信息

```java
package com.davidwang456.mybatis.jpa.config;
import java.util.Properties;

import javax.sql.DataSource;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.PropertySource;
import org.springframework.core.env.Environment;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.orm.jpa.JpaTransactionManager;
import org.springframework.orm.jpa.LocalContainerEntityManagerFactoryBean;
import org.springframework.orm.jpa.vendor.HibernateJpaVendorAdapter;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.EnableTransactionManagement;

@Configuration
@EnableJpaRepositories("com.davidwang456.mybatis.jpa.respository")
@EnableTransactionManagement
@PropertySource("classpath:db.properties")
public class JPAConfig {
	@Autowired
	private Environment env;
	@Bean
	public LocalContainerEntityManagerFactoryBean entityManagerFactory() {
		HibernateJpaVendorAdapter vendorAdapter = new HibernateJpaVendorAdapter();
		LocalContainerEntityManagerFactoryBean factory = new LocalContainerEntityManagerFactoryBean();
		factory.setJpaVendorAdapter(vendorAdapter);
		factory.setPackagesToScan("com.davidwang456.mybatis.jpa.entity");
		factory.setDataSource(dataSource());
		factory.setJpaProperties(hibernateProperties());
		return factory;
	}
	
	@Bean
	public DataSource dataSource() {
		DriverManagerDataSource ds = new DriverManagerDataSource();
		ds.setDriverClassName(env.getProperty("db.driverClassName"));
		ds.setUrl(env.getProperty("db.url"));
		ds.setUsername(env.getProperty("db.username"));
		ds.setPassword(env.getProperty("db.password"));
		return ds;
	}
	
	Properties hibernateProperties() {
		Properties properties = new Properties();
		properties.setProperty("hibernate.dialect", env.getProperty("hibernate.sqldialect"));
		properties.setProperty("hibernate.show_sql", env.getProperty("hibernate.showsql"));
		return properties;
	}
	
	@Bean
	public PlatformTransactionManager transactionManager() {
		JpaTransactionManager txManager = new JpaTransactionManager();
		txManager.setEntityManagerFactory(entityManagerFactory().getObject());
		return txManager;
	}
}
```

**db配置，放到resources目录下:**

```java
db.driverClassName=com.mysql.cj.jdbc.Driver
db.url=jdbc:mysql://localhost:3306/davidwang456?characterEncoding=UTF-8&useSSL=false&useLegacyDatetimeCode=false&serverTimezone=UTC
db.username=root
db.password=wangwei456
hibernate.sqldialect=org.hibernate.dialect.MySQLDialect
hibernate.showsql=true
```

#### 创建实体类

```JAVA
package com.davidwang456.mybatis.jpa.entity;

import javax.persistence.Column;
import javax.persistence.Entity;
import javax.persistence.GeneratedValue;
import javax.persistence.GenerationType;
import javax.persistence.Id;
import javax.persistence.Table;

import lombok.Data;

@Entity
@Table(name="student")
@Data
public class Student {
	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	private int id;
	@Column(name="first_name")
	private String firstName;
	@Column(name="last_name")
	private String lastName;
	@Column(name="age")
	private int age;

	@Override
	public String toString() {
		return "Id= " + id + " First Name= " + 
				firstName + " Last Name= " + lastName + 
	           " age= "+ age;
	}
}
```

#### 创建resporitory(dao层)

```java
package com.davidwang456.mybatis.jpa.respository;

import org.springframework.data.repository.CrudRepository;

import com.davidwang456.mybatis.jpa.entity.Student;

public interface StudentRepository extends CrudRepository<Student,Integer>{
}
```

你没有看错，一个方法都没有！CrudRepository帮我们实现了增删改查。

#### 测试类

```java
package com.davidwang456.mybatis.jpa;

import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import com.davidwang456.mybatis.jpa.config.JPAConfig;
import com.davidwang456.mybatis.jpa.entity.Student;
import com.davidwang456.mybatis.jpa.respository.StudentRepository;

public class SpringDataJpaTest {
	@SuppressWarnings({ "resource" })
	public static void main(String[] args) {
		AnnotationConfigApplicationContext context=new AnnotationConfigApplicationContext(JPAConfig.class);		
		//for(String beanName:context.getBeanDefinitionNames())
			//System.out.println(beanName);		
		StudentRepository studentRepository= (StudentRepository)context.getBean("studentRepository");
		select(studentRepository,8);
	}
	
	public static void delete(StudentRepository studentRepository,Integer id) { 
		studentRepository.deleteById(id);
	}
	
	public static void update(StudentRepository studentRepository,Integer id) { 
		Student entity=new Student();
		entity.setFirstName("www1");
		entity.setLastName("baidu.com1");
		entity.setAge(13); 
		entity.setId(id);
		studentRepository.save(entity);
		System.out.println("data update success");
	}
	
	public static void select(StudentRepository studentRepository,Integer id) { 
		System.out.println(studentRepository.findById(id));
	}
	
	public static void save(StudentRepository studentRepository) {
		Student entity=new Student();
		entity.setFirstName("www");
		entity.setLastName("baidu.com");
		entity.setAge(23);  
		studentRepository.save(entity);
		System.out.println("data insert success");
	}	
}

```

测试情况

##### 查询

main方法调用select(studentRepository,8),打印日志：

```java
Hibernate: select student0_.id as id1_0_0_, student0_.age as age2_0_0_, student0_.first_name as first_na3_0_0_, student0_.last_name as last_nam4_0_0_ from student student0_ where student0_.id=?
Optional[Id= 8 First Name= wang8 Last Name= david8 age= 25]
```

Jpa自动生成查询语句，打印查询结果。

##### 保存

main方法调用save(studentRepository)，打印日志：

```java
Hibernate: insert into student (age, first_name, last_name) values (?, ?, ?)
data insert success
```

查询数据库，得到记录

```java
id  first_name  last_name age
10	www	baidu.com	23
```

##### 修改

main方法调用update(studentRepository,10)，打印日志：

```java
Hibernate: select student0_.id as id1_0_0_, student0_.age as age2_0_0_, student0_.first_name as first_na3_0_0_, student0_.last_name as last_nam4_0_0_ from student student0_ where student0_.id=?
Hibernate: update student set age=?, first_name=?, last_name=? where id=?
data update success
```

先查询，后修改。查询数据库，得到记录：

```java
id  first_name  last_name age
10	www1	baidu.com1	13
```

##### 删除

main方法调用delete(studentRepository,10)，打印日志：

```java
Hibernate: select student0_.id as id1_0_0_, student0_.age as age2_0_0_, student0_.first_name as first_na3_0_0_, student0_.last_name as last_nam4_0_0_ from student student0_ where student0_.id=?
Hibernate: delete from student where id=?
```

先查询，后删除。查询数据库，得到记录：

```java
id  first_name  last_name age

```

## 总结

> 扫地僧：通过上面的spring-data-jpa，你知道什么是jpa？jpa和hibernate的关系了吗？
>
> 小白：JPA是Java持久层API，它是Sun公司在JavaEE 5中提出的Java持久化规范，它的规范是jsr-338。
>
> Spring Data JPA是一个JPA数据访问抽象,Hibernate其实是JPA的一种实现,本质还是Hibernate。它提供了更高层的抽象，可以提高开发效率。既然Spring Data JPA那么方便，我们为什么使用Mybatis，而不是使用它？
>
> 扫地僧：抛开业务场景，只针对使用上来看的话，Hibernate 比Mybatis更加方便。但是写程序永远脱离不了业务，面对日益复杂话的业务场景，Mybatis的灵活性尤为重要。为了应对复杂的场景Mybatis首当其冲的成为首选的持久化框架。

