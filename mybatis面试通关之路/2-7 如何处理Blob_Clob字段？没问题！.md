## 背景

> 小白：师傅，用户信息中既包含有用户的基本信息，也包含用户的头像等图片信息，我把用户上传的图片信息保存到数据库中，用户登陆时从数据库中读取用户信息返回给用户，用户抱怨网页太慢，有什么办法优化一下吗？
>
> 扫地僧：你是如何把用户的头像信息存储到数据库的？
>
> 小白：那我演示一下我的做法，您给我指导一下吧！

## Mybatis 存储图像示例

不同数据库中对应clob,blob的类型不同，在mysql中：

- Clob对应类型为Text,用于存储大量的**文本**数据；

- Blob对应类型为Blob,用于存储二进制数据，常常为**图片或音频**；

他们的存储大小如下：

| [`TINYBLOB`](https://dev.mysql.com/doc/refman/8.0/en/blob.html), [`TINYTEXT`](https://dev.mysql.com/doc/refman/8.0/en/blob.html) | *`L`* + 1 bytes, where *`L`* < 2^8  |
| ------------------------------------------------------------ | ----------------------------------- |
| [`BLOB`](https://dev.mysql.com/doc/refman/8.0/en/blob.html), [`TEXT`](https://dev.mysql.com/doc/refman/8.0/en/blob.html) | *`L`* + 2 bytes, where *`L`* < 2^16 |
| [`MEDIUMBLOB`](https://dev.mysql.com/doc/refman/8.0/en/blob.html), [`MEDIUMTEXT`](https://dev.mysql.com/doc/refman/8.0/en/blob.html) | *`L`* + 3 bytes, where *`L`* < 2^24 |
| [`LONGBLOB`](https://dev.mysql.com/doc/refman/8.0/en/blob.html), [`LONGTEXT`](https://dev.mysql.com/doc/refman/8.0/en/blob.html) | *`L`* + 4 bytes, where *`L`* < 2^32 |

即TinyBlob，Tinytext最多存储256个字节；blob和text存储不超过64Kb+2字节，mediumblob和mediumtext存储不超过16MB+3字节；LongBlob和longtext存储不超过4GB+4字节.

### 准备工作

数据库脚本

```mysql
CREATE database davidwang456;
use davidwang456;

DROP TABLE IF EXISTS  student;
CREATE TABLE `student` (
  `id` INT(11) NOT NULL AUTO_INCREMENT,
  `first_name` VARCHAR(100) DEFAULT NULL,
  `last_name` VARCHAR(100) DEFAULT NULL,
  `age` INT(11) DEFAULT NULL,
  `content` TEXT DEFAULT NULL,
  `image` BLOB DEFAULT NULL,
  `create_time` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=INNODB DEFAULT CHARSET=utf8mb4;
```

其中，context使用text即Clob结构，image使用Blob结构

### 创建maven项目

**依赖pom.xml**

```
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>ClobBlobTest</artifactId>
  <version>3.6.0-SNAPSHOT</version>
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
**数据库实体**

```java
package com.davidwang456.mybatis.clobblob;

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
	private String content;
	private byte[] image;
	private Date createTime;
	private Date updateTime;
	@Override
	   public String toString() {
	    return "student [id=" + id + ", firstName=" + firstName
	    		 + ", lastName=" + lastName + ", age=" +age+ 
	    		 "创建时间："+DateUtils.getDateString(createTime)+
	    		 "更新时间："+DateUtils.getDateString(updateTime)+
	    		 ']';
	   }
}
```

**查询实体**

```java
package com.davidwang456.mybatis.clobblob;

import java.util.Date;

import lombok.Data;

@Data
public class StudentQueryDTO {
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	private Date startDate;
	private Date endDate;
	//关键词查:依据firstName和lastName
	private String keyword;
	//排序项目
	private String sort;
	//排序 DESC|ASC
	private String orderBy;
}
```

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
<mapper namespace="com.davidwang456.mybatis.clobblob.StudentMapper">
	<insert id="insertStudentInfo" parameterType="com.davidwang456.mybatis.clobblob.StudentDTO">	
		INSERT INTO `student` 
		( `first_name`, `last_name`, `age`,
		`content`, `image`) 
		VALUES
		(#{firstName},#{lastName},#{age},#{content},#{image})	
	</insert>
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.clobblob.StudentQueryDTO" resultType="com.davidwang456.mybatis.clobblob.StudentDTO">
		<bind name="condition" value="'%'+keyword+'%'"/>
		select id,
			   first_name ,
			   last_name ,
			   age,
			   content,
			   image,
			   create_time,
			   update_time
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
			   <if test="startDate!=null">
			   AND create_time > #{startDate}
			   AND update_time > #{startDate}
			   </if>
			   <if test="endDate!=null">
			   AND  create_time <![CDATA[< #{endDate}]]>
			   AND  update_time <![CDATA[< #{endDate}]]>
			   </if>
			   ORDER BY ${sort} ${orderBy}			   		   		  				  
	</select>	
</mapper>
```

**Mapper文件**

```java
package com.davidwang456.mybatis.clobblob;

import java.util.List;

public interface StudentMapper {
	public Integer insertStudentInfo(StudentDTO dto);
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
}
```

**测试程序**

1.插入测试

```java
	public static void insert() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO dto=new StudentDTO();
	      dto.setFirstName("david");
	      dto.setLastName("www");
	      dto.setAge(25);	      
	      dto.setContent(new String(readToByte("C:\\documet\\mybatis\\wangwei\\README.md"), "UTF-8"));
          dto.setImage(readToByte("C:\\documet\\mybatis\\wangwei\\别人家孩子.jpg"));
          studentMapper.insertStudentInfo(dto);
	      session.commit(true);
	      session.close();	
	}
	public static byte[] readToByte(String fileName) {
        byte[] image = null; 
        try {
            File file = new File(fileName); 
            InputStream is = new FileInputStream(file); 
            image = new byte[is.available()]; 
            is.read(image); 
            is.close(); 
        } catch (Exception e){ 
            e.printStackTrace(); 
        } 
        return image;
	}
```

操作后数据结果
> ==>  Preparing: INSERT INTO `student` ( `first_name`, `last_name`, `age`, `content`, `image`) VALUES (?,?,?,?,?)
> ==> Parameters: david(String), www(String), 25(Integer), # wangwei
> 王伟的专栏
> (String), [B@dd3b207(byte[])
> <==    Updates: 1

查询数据库

```java
SELECT * FROM student;
```

可以看到插入的记录。

2.用户信息查询接口测试

```java
	public static void query() throws IOException, ParseException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO query=new StudentQueryDTO();
	      query.setKeyword("david");
	      query.setOrderBy("DESC");
	      query.setSort("create_time");
	      List<StudentDTO> dtos=studentMapper.getStudentInfoByCondition(query);
	      for(StudentDTO dto:dtos) {
	    	  System.out.println(dto.toString());
	      }
	      session.commit(true);
	      session.close();	
	}
```

打印出结果如下：

```tex
==>  Preparing: select id, first_name , last_name , age, content, image, create_time, update_time from student where 1=1 and (first_name LIKE ? OR last_name LIKE ? ) ORDER BY create_time DESC
==> Parameters: %david%(String), %david%(String)
<==    Columns: id, first_name, last_name, age, content, image, create_time, update_time
<==        Row: 1, david, www, 25, <<BLOB>>, <<BLOB>>, 2021-03-05 15:07:12, 2021-03-05 15:07:12
<==      Total: 1
student [id=1, firstName=david, lastName=www, age=25创建时间：2021-03-05 15:07:12更新时间：2021-03-05 15:07:12]
```



## 优化用户信息查询速度

> 扫地僧：一般来说，图片、大文本的文本等静态的内容不会存储到数据库中，而是将它们存放到文件服务器、cdn或者Nginx上，数据库中只保存这些文件的存放位置。
>
> 小白：那我这种情况，还可以抢救一下吗？
>
> 扫地僧：方法有不少哟，先从最简单的开始。
>
> 第一个方法是分拆法。
>
> ​     第一步：用户信息查询表分拆，三张表，一张表存储基本信息(不包含图像和大文本)，一张表存储大文本，一张表专门存放图像，它们可以通过用户唯一标识关联。
>
> ​     第二步改造接口：增加查询类型，type=1 代表查询基本信息接口；type=2 代表查询大文本+基本信息接口；type=3 代表查询图像+基本信息接口；type=4代表查询图像+大文本+基本信息接口。
>
> 第二个方法是压缩法。
>
>  **将大文本压缩存储**
>
> 常见的压缩算法java实现有：
>
> - JDK中的java.util.zip.GZIPInputStream/GZIPOutputStream
>
> - JDK中的java.util.zip.DeflaterOutputStream / InflaterInputStream
>
> - Snappy—Google开发的一个非常流行的压缩算法。
>
> **将图片压缩成缩略图存储**
>
> 常见的实现方式是使用Thumbnailator
>
> 第三个方法是转移法：将图片、大文本的文本等静态的内容存放到专门的文件服务器、cdn或者Nginx上，数据库中只保存这些文件的存放位置。
>
> 扫地僧：三种方法，第一种最简单，但留下了隐患；第二种治标不治本，容易出bug；第三种标本兼治，但需要额外的资源，成本比较高。你会选择哪种呢？

## 总结

1. mysql中

   - Clob对应类型为Text,有三种类型Tinytext、mediumtext、longtext,用于存储大量的**文本**数据，对应java中的String；

   - Blob对应类型为Blob,有三种类型TinyBlob、mediumblob、LongBlob，用于存储二进制数据，常常为**图片或音频**，对应java中byte[];
   - TinyBlob，Tinytext最多存储256个字节；blob和text存储不超过64Kb+2字节，mediumblob和mediumtext存储不超过16MB+3字节；LongBlob和longtext存储不超过4GB+4字节。

2. 对请求频繁的数据，一般来说，图片、大文本的文本等静态的内容不会存储到数据库中，而是将它们存放到文件服务器、cdn或者Nginx上，数据库中只保存这些文件的存放位置。前端访问时通过链接地址访问这些资源，不会对后端产生压力。

   对于请求不频繁且文本或者图片不大的情况下，也可以考虑存储到数据库，但最好分表放置。访问时最好加缓冲层。