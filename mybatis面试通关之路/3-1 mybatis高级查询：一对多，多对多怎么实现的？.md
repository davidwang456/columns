# mybatis高级查询：一对多，多对多怎么实现的？

## 背景

> 小白：师傅，我能熟练的利用mybatis对单表进行增删改查操作了，也能用动态SQL书写一些比较复杂的sql语句。但是在实际开发中，我们做项目不可能只是单表操作，往往会涉及到多张表之间的关联操作。那么我们如何用 mybatis 处理多表之间的关联操作呢？
>
> 扫地僧：多张表的关联操作一般可以分为一对多查询和多对多查询，多说无益，那接下来我们就用代码演示一番！



## 一对多高级查询

### 准备工作

**数据库脚本**

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

DROP TABLE IF EXISTS `address`;
CREATE TABLE `address` (
  `id` INT(11) NOT NULL AUTO_INCREMENT,
  `student_id` INT(11) NOT NULL ,
  `address_type` INT(2) DEFAULT NULL,
  `detail` VARCHAR(100) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=INNODB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4;
```

其中，context使用text即Clob结构，image使用Blob结构

### 创建maven工程

**添加依赖pom.xml文件**

```java
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>One2ManyTest</artifactId>
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

**数据库配置**

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
      <mapper resource = "AddressMapper.xml"/>
   </mappers> 
</configuration>
```

**实体及之间的关系**

```java
package com.davidwang456.mybatis.one2many;

import java.io.Serializable;
import java.util.Date;
import java.util.List;

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
	private List<Address> addrs;
	@Override
	   public String toString() {
	    return "student [id=" + id + ", firstName=" + firstName
	    		 + ", lastName=" + lastName + ", age=" +age+ 
	    		 " 创建时间："+DateUtils.getDateString(createTime)+
	    		 " 更新时间："+DateUtils.getDateString(updateTime)+
	    		 " 地址：("+getAdds(addrs)+")"+
	    		 ']';
	   }
	
	private String getAdds(List<Address> address) {
		if(address==null||address.isEmpty()) {
			return "";
		}
		StringBuffer sbf=new StringBuffer();
		for(int i=0;i<address.size();i++) {
			
			if(i==address.size()-1) {
				sbf.append(address.get(i).getDetail());
			}else {
				sbf.append(address.get(i).getDetail()+", ");
			}
		}
		return sbf.toString();
	}
}

```

其中，Address实体

```java
package com.davidwang456.mybatis.one2many;

import lombok.Data;

@Data
public class Address {
	private Integer id;
	private Integer studentId;
	private Integer addressType;//1:home 2:company
	private String detail;
}
```

**对应的mapper文件**

```java
package com.davidwang456.mybatis.one2many;

public interface AddressMapper {
	public Integer insertAddressInfo(Address dto);
}
```

StudenetMapper.java文件

```java
package com.davidwang456.mybatis.one2many;

import java.util.List;

public interface StudentMapper {
	public Integer insertStudentInfo(StudentDTO dto);
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
}
```

**对应的xml配置文件**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
        PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.one2many.AddressMapper">
	<insert id="insertAddressInfo" parameterType="com.davidwang456.mybatis.one2many.Address">	
		INSERT INTO `address` 
		( `student_id`, `address_type`, `detail`) 
		VALUES
		(#{studentId},#{addressType},#{detail})	
	</insert>
</mapper>
```

StudentMapper.xml文件

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
        PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.one2many.StudentMapper">
	<insert id="insertStudentInfo" parameterType="com.davidwang456.mybatis.one2many.StudentDTO" keyColumn="id"	useGeneratedKeys="true" keyProperty="id">	
		INSERT INTO `student` 
		( `first_name`, `last_name`, `age`,
		`content`, `image`) 
		VALUES
		(#{firstName},#{lastName},#{age},#{content},#{image})	
	</insert>
	<resultMap type="com.davidwang456.mybatis.one2many.StudentDTO" id="studentInfoWithAddress">
		<id column="s_student_id" property="id" />
		<result column="s_first_name" property="firstName" ></result>
		<result column="s_last_name" property="lastName" ></result>
		<result column="s_age" property="age" ></result>
		<result column="s_create_time" property="createTime" ></result>
		<result column="s_update_time" property="updateTime" ></result>
		<collection property="addrs" ofType="com.davidwang456.mybatis.one2many.Address">
            <id column="a_id" property="id"/>
            <result column="a_student_id" property="studentId"/>
            <result column="a_address_type" property="addressType"/>
            <result column="a_detail" property="detail"/>
        </collection>
		
	</resultMap>
	<select id="getStudentInfoByCondition" parameterType="com.davidwang456.mybatis.one2many.StudentQueryDTO" resultMap="studentInfoWithAddress">
		<bind name="condition" value="'%'+keyword+'%'"/>
		select s.id as s_student_id,
			   s.first_name as s_first_name ,
			   s.last_name as s_last_name ,
			   s.age as s_age,
			   s.create_time as s_create_time,
			   s.update_time as s_update_time, 
			   a.id as a_id,
			   a.student_id as a_student_id,
			   a.address_type as a_address_type,
			   a.detail as a_detail
			   from student s
			   left join address a on s.id= a.student_id 
			   where 1=1 
			   <if test="id!=null">
			   and s.id=#{id}
			   </if>
			   <if test="keyword!=null and keyword!=''">
			   and 
			   (s.first_name LIKE #{condition}
			   OR s.last_name LIKE #{condition}
			   )
			   </if>
			  <if test="age!=null and age!=0">
			   and s.age=#{age}
			   </if>
			   <if test="startDate!=null">
			   AND s.create_time > #{startDate}
			   AND s.update_time > #{startDate}
			   </if>
			   <if test="endDate!=null">
			   AND  s.create_time <![CDATA[< #{endDate}]]>
			   AND  s.update_time <![CDATA[< #{endDate}]]>
			   </if>
			   ORDER BY ${sort} ${orderBy}			   		   		  				  
	</select>	
</mapper>
```

注意mapper映射，使用了<collection>标签。

### 测试程序

**插入数据操作**

```java
	public static void insert() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      AddressMapper addressMapper=session.getMapper(AddressMapper.class);
	      StudentDTO dto=new StudentDTO();
	      dto.setFirstName("david");
	      dto.setLastName("www");
	      dto.setAge(25);	      
	      dto.setContent(new String(readToByte("E:\\ppt\\mybatis\\wangwei\\README.md"), "UTF-8"));
          dto.setImage(readToByte("E:\\ppt\\mybatis\\wangwei\\春暖花开.jpg"));
          studentMapper.insertStudentInfo(dto);         
          Integer studentId=dto.getId();
          
          List<Address> ads=new ArrayList<>();
          Address home=new Address();
          home.setAddressType(1);
          home.setStudentId(studentId);
          home.setDetail("china shanghai");
          
          Address company=new Address();
          company.setAddressType(1);
          company.setStudentId(studentId);
          company.setDetail("china beijing");
          
          ads.add(home);
          ads.add(company);
          
          for(Address a:ads) {
        	  addressMapper.insertAddressInfo(a);
          }
	      session.commit(true);
	      session.close();	
	}
	
	public static byte[] readToByte(String fileName) {
        byte[] image = null; 
        try {
      	  
            //读取用户头像图片
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
	public static String readToString(String fileName) {  
        String encoding = "UTF-8";  
        File file = new File(fileName);  
        Long filelength = file.length();  
        byte[] filecontent = new byte[filelength.intValue()];  
        try {  
            FileInputStream in = new FileInputStream(file);  
            in.read(filecontent);  
            in.close();  
        } catch (FileNotFoundException e) {  
            e.printStackTrace();  
        } catch (IOException e) {  
            e.printStackTrace();  
        }  
        try {  
            return new String(filecontent, encoding);  
        } catch (UnsupportedEncodingException e) {  
            System.err.println("The OS does not support " + encoding);  
            e.printStackTrace();  
            return null;  
        }  
    }  
```

执行插入数据

```java
	public static void main(String[] args) throws IOException, ParseException {
		insert();
		//queryStudentInfo();
	   }
```

执行sql打印如下：

```tex
==>  Preparing: INSERT INTO `student` ( `first_name`, `last_name`, `age`, `content`, `image`) VALUES (?,?,?,?,?)
==> Parameters: david(String), www(String), 25(Integer), # wangwei
王伟的专栏
(String), [B@345965f2(byte[])
<==    Updates: 1
==>  Preparing: INSERT INTO `address` ( `student_id`, `address_type`, `detail`) VALUES (?,?,?)
==> Parameters: 1(Integer), 1(Integer), china shanghai(String)
<==    Updates: 1
==>  Preparing: INSERT INTO `address` ( `student_id`, `address_type`, `detail`) VALUES (?,?,?)
==> Parameters: 1(Integer), 1(Integer), china beijing(String)
<==    Updates: 1
```



**测试1：单步查询操作**

使用表关联join的方式查询

```java
	public static void main(String[] args) throws IOException, ParseException {
		//insert();
		queryStudentInfo();
	   }
	
	public static void queryStudentInfo() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO query=new StudentQueryDTO();
	      query.setId(1);
	      query.setKeyword("david");
	      query.setSort("create_time");
	      query.setOrderBy("ASC");
	      List<StudentDTO> dtos=studentMapper.getStudentInfoByCondition(query);
	      for(StudentDTO dto:dtos) {
	    	  System.out.println(dto.toString());
	      }
	      session.commit(true);
	      session.close();	
	}
```

查询结果:

```tex
==>  Preparing: select s.id as s_student_id, s.first_name as s_first_name , s.last_name as s_last_name , s.age as s_age, s.create_time as s_create_time, s.update_time as s_update_time, a.id as a_id, a.student_id as a_student_id, a.address_type as a_address_type, a.detail as a_detail from student s left join address a on s.id= a.student_id where 1=1 and s.id=? and (s.first_name LIKE ? OR s.last_name LIKE ? ) ORDER BY create_time ASC
==> Parameters: 1(Integer), %david%(String), %david%(String)
<==    Columns: s_student_id, s_first_name, s_last_name, s_age, s_create_time, s_update_time, a_id, a_student_id, a_address_type, a_detail
<==        Row: 1, david, www, 25, 2021-05-31 09:09:22, 2021-05-31 09:09:22, 1, 1, 1, china shanghai
<==        Row: 1, david, www, 25, 2021-05-31 09:09:22, 2021-05-31 09:09:22, 2, 1, 1, china beijing
<==      Total: 2
student [id=1, firstName=david, lastName=www, age=25 创建时间：2021-05-31 09:09:22 更新时间：2021-05-31 09:09:22 地址：(china shanghai, china beijing)]
```

可以看出：针对一对多的高级查询，它的查询sql使用join的方式进行，返回结果使用resultMap来聚合返回结果（将地址聚合到student的地址下）。

**测试2：分步查询**

可以利用collection/association标签进行分步查询，两种标签的方式是相同的，故仅以collection为例：

```xml
	<select id="getStudentById" resultMap="studentInfoWithAddressManyStep" >
		select s.id,
			   s.first_name,
			   s.last_name,
			   s.age,
			   s.create_time,
			   s.update_time			   
			   from student s where s.id=#{id}
	</select>
	<resultMap type="com.davidwang456.mybatis.one2many.StudentDTO" id="studentInfoWithAddressManyStep">
		<id column="id" property="id" />
		<result column="first_name" property="firstName" ></result>
		<result column="last_name" property="lastName" ></result>
		<result column="age" property="age" ></result>
		<result column="create_time" property="createTime" ></result>
		<result column="update_time" property="updateTime" ></result>
		<collection property="addrs"
                     select="getAddressByStudentId"
                     column="student_id">
                     </collection>	
	</resultMap>
```

**测试3：注解方式查询**

```java
    // 根据id查询学生信息
    @Select("SELECT * FROM student  WHERE ID = #{id}")
    @Results(
    { @Result(id = true, column = "id", property = "id"), @Result(column = "fisrst_name", property = "firstName"),
            @Result(column = "last_name", property = "lastName"),@Result(column = "age", property = "age"),
            @Result(column = "id", property = "addrs", many = @Many(select = "com.davidwang456.mybatis.one2many.StudentMapper.selectByStudentId", fetchType = FetchType.LAZY)) })
    StudentDTO getAnnatationStudentById(Integer id);
	

    @Select("SELECT * FROM address WHERE student_id = #{id}")
    @Results(
    { @Result(id = true, column = "id", property = "id"), @Result(column = "student_id", property = "studentId"),
            @Result(column = "address_type", property = "addressType"), @Result(column = "detail", property = "detail") })

    List<Address> selectByStudentId(Integer student_id);
}
```

**小结**

，又有两种实现方式：；。



## 多对多高级查询

使用多对多关联，需要借助一个起中介作用的连接表完成。示例：学生student和老师teacher的关系，一个学生可以拥有多个老师，一个老师也可以拥有多个学生，他们是多对多的关系。设计数据库表时除了student和teacher表外还会定义一个student_teacher_relation表。这样一个多对多的关联通常可以分拆成两个一对多的关联。

## 总结

- 一对多关联查询，使用xml配置文件和注解都可以实现；
- 一对多关联查询，内部实现方式有两种单步使用表JOIN方式，多步分开查询；
- 从可读性和易用性来看，推荐表关联JOIN方式；
- 多对多高级查询可以拆分成两个一对多关系，简化逻辑复杂度；
- 多对多高级查询通过使用中间表来完成。
