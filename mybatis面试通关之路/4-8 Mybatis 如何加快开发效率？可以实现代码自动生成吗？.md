# Mybatis 如何加快开发效率？可以实现代码自动生成吗？

## 背景

> 小白：师傅，JPA使用起来更便利，提供了很多代码自动生成工具，内部也封装了很多公用方法，可以大大提升我们的开发效率。Mybatis这方面有没有提升效率的工具呢？
>
> 扫地僧：Mybatis在这方面的发展也很不错，主要有：Mybatis官方提供了Mybatis Generator来提供代码的自动生成；国内流行的开源Mybatis-Plus，提供了便利的工具，减少开发代码。

## Mybatis Generator使用实例

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

![image-20210729084942908](img\chapter04-08.png)

#### 添加依赖

pom.xml

```xml
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.davidwang456.mybatis</groupId>
  <artifactId>MBGTest</artifactId>
  <version>4.8.0-SNAPSHOT</version>
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
<build>
    <plugins>
        <plugin>
            <groupId>org.mybatis.generator</groupId>
            <artifactId>mybatis-generator-maven-plugin</artifactId>
            <version>1.3.2</version>
            <configuration>
                <verbose>true</verbose>
                <overwrite>true</overwrite>
                <!--mybatis generator配置文件-->
                <configurationFile>src/main/resources/generatorConfig.xml</configurationFile>
            </configuration>
    
            <dependencies>
                <!-- 数据库驱动  -->
                <dependency>
                    <groupId>mysql</groupId>
                    <artifactId>mysql-connector-java</artifactId>
                    <version>8.0.16</version>
                </dependency>
            </dependencies>
        </plugin>
    </plugins>
</build>   
</project>
```

注意，mybatis-generator-maven-plugin为mybatis generator maven插件，负责生成entity，dao，xml，example。

插件的配置文件为generatorConfig.xml

```xml
<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE generatorConfiguration
    PUBLIC "-//mybatis.org//DTD MyBatis Generator Configuration 1.0//EN"
    "http://mybatis.org/dtd/mybatis-generator-config_1_0.dtd">

    <generatorConfiguration>
        <context id="mysqlTables" targetRuntime="MyBatis3">
            <commentGenerator>
                <property name="suppressDate" value="false"/>
                <property name="suppressAllComments" value="true"/>
            </commentGenerator>
            <!--目标数据库配置-->
            <jdbcConnection driverClass="com.mysql.cj.jdbc.Driver"
                    connectionURL="jdbc:mysql://localhost:3306/davidwang456?characterEncoding=UTF-8&amp;useSSL=false&amp;useLegacyDatetimeCode=false&amp;serverTimezone=UTC"
         userId="root" password="wangwei456"/>
            <!-- 指定生成的类型为java类型，避免数据库中number等类型字段 -->
            <javaTypeResolver>
                  <property name="forceBigDecimals" value="false"/>
            </javaTypeResolver>
            <!-- 生成实体类和example类-->
            <javaModelGenerator targetPackage="com.davidwang456.mybatis.mbg" targetProject="src/main/java">
                <property name="enableSubPackages" value="false"/>
                <property name="trimStrings" value="true"/>
                <property name="immutable" value="false"/>
            </javaModelGenerator>
            <!--对应的xml文件  -->
            <sqlMapGenerator targetPackage="mapper"  targetProject="src/main/resources">
                <property name="enableSubPackages" value="false"/>
            </sqlMapGenerator>
            <!-- 对应的dao接口 -->
            <javaClientGenerator type="XMLMAPPER" targetPackage="com.davidwang456.mybatis.mbg" targetProject="src/main/java">
                <property name="enableSubPackages" value="false"/>
            </javaClientGenerator>
              <!--定义需要操作的表及对应的DTO名称-->
            <table tableName="student" domainObjectName="StudentDTO"/>
        </context>
</generatorConfiguration>
```

运行

```shell
mvn mybatis-generator:generate 
```

此时，生成的文件有：

- StudentDTO.java 实体类

  ```java
  package com.davidwang456.mybatis.mbg;
  
  public class StudentDTO {
      private Integer id;
  
      private String firstName;
  
      private String lastName;
  
      private Integer age;
  
      public Integer getId() {
          return id;
      }
  
      public void setId(Integer id) {
          this.id = id;
      }
  
      public String getFirstName() {
          return firstName;
      }
  
      public void setFirstName(String firstName) {
          this.firstName = firstName == null ? null : firstName.trim();
      }
  
      public String getLastName() {
          return lastName;
      }
  
      public void setLastName(String lastName) {
          this.lastName = lastName == null ? null : lastName.trim();
      }
  
      public Integer getAge() {
          return age;
      }
  
      public void setAge(Integer age) {
          this.age = age;
      }
  }
  ```

- StudentDTOExample.java 查询类

  ```java
  package com.davidwang456.mybatis.mbg;
  
  import java.util.ArrayList;
  import java.util.List;
  
  public class StudentDTOExample {
      protected String orderByClause;
  
      protected boolean distinct;
  
      protected List<Criteria> oredCriteria;
  
      public StudentDTOExample() {
          oredCriteria = new ArrayList<Criteria>();
      }
  
      public void setOrderByClause(String orderByClause) {
          this.orderByClause = orderByClause;
      }
  
      public String getOrderByClause() {
          return orderByClause;
      }
  
      public void setDistinct(boolean distinct) {
          this.distinct = distinct;
      }
  
      public boolean isDistinct() {
          return distinct;
      }
  
      public List<Criteria> getOredCriteria() {
          return oredCriteria;
      }
  
      public void or(Criteria criteria) {
          oredCriteria.add(criteria);
      }
  
      public Criteria or() {
          Criteria criteria = createCriteriaInternal();
          oredCriteria.add(criteria);
          return criteria;
      }
  
      public Criteria createCriteria() {
          Criteria criteria = createCriteriaInternal();
          if (oredCriteria.size() == 0) {
              oredCriteria.add(criteria);
          }
          return criteria;
      }
  
      protected Criteria createCriteriaInternal() {
          Criteria criteria = new Criteria();
          return criteria;
      }
  
      public void clear() {
          oredCriteria.clear();
          orderByClause = null;
          distinct = false;
      }
  
      protected abstract static class GeneratedCriteria {
          protected List<Criterion> criteria;
  
          protected GeneratedCriteria() {
              super();
              criteria = new ArrayList<Criterion>();
          }
  
          public boolean isValid() {
              return criteria.size() > 0;
          }
  
          public List<Criterion> getAllCriteria() {
              return criteria;
          }
  
          public List<Criterion> getCriteria() {
              return criteria;
          }
  
          protected void addCriterion(String condition) {
              if (condition == null) {
                  throw new RuntimeException("Value for condition cannot be null");
              }
              criteria.add(new Criterion(condition));
          }
  
          protected void addCriterion(String condition, Object value, String property) {
              if (value == null) {
                  throw new RuntimeException("Value for " + property + " cannot be null");
              }
              criteria.add(new Criterion(condition, value));
          }
  
          protected void addCriterion(String condition, Object value1, Object value2, String property) {
              if (value1 == null || value2 == null) {
                  throw new RuntimeException("Between values for " + property + " cannot be null");
              }
              criteria.add(new Criterion(condition, value1, value2));
          }
  
          public Criteria andIdIsNull() {
              addCriterion("id is null");
              return (Criteria) this;
          }
  
          public Criteria andIdIsNotNull() {
              addCriterion("id is not null");
              return (Criteria) this;
          }
  
          public Criteria andIdEqualTo(Integer value) {
              addCriterion("id =", value, "id");
              return (Criteria) this;
          }
  
          public Criteria andIdNotEqualTo(Integer value) {
              addCriterion("id <>", value, "id");
              return (Criteria) this;
          }
  
          public Criteria andIdGreaterThan(Integer value) {
              addCriterion("id >", value, "id");
              return (Criteria) this;
          }
  
          public Criteria andIdGreaterThanOrEqualTo(Integer value) {
              addCriterion("id >=", value, "id");
              return (Criteria) this;
          }
  
          public Criteria andIdLessThan(Integer value) {
              addCriterion("id <", value, "id");
              return (Criteria) this;
          }
  
          public Criteria andIdLessThanOrEqualTo(Integer value) {
              addCriterion("id <=", value, "id");
              return (Criteria) this;
          }
  
          public Criteria andIdIn(List<Integer> values) {
              addCriterion("id in", values, "id");
              return (Criteria) this;
          }
  
          public Criteria andIdNotIn(List<Integer> values) {
              addCriterion("id not in", values, "id");
              return (Criteria) this;
          }
  
          public Criteria andIdBetween(Integer value1, Integer value2) {
              addCriterion("id between", value1, value2, "id");
              return (Criteria) this;
          }
  
          public Criteria andIdNotBetween(Integer value1, Integer value2) {
              addCriterion("id not between", value1, value2, "id");
              return (Criteria) this;
          }
  
          public Criteria andFirstNameIsNull() {
              addCriterion("first_name is null");
              return (Criteria) this;
          }
  
          public Criteria andFirstNameIsNotNull() {
              addCriterion("first_name is not null");
              return (Criteria) this;
          }
  
          public Criteria andFirstNameEqualTo(String value) {
              addCriterion("first_name =", value, "firstName");
              return (Criteria) this;
          }
  
          public Criteria andFirstNameNotEqualTo(String value) {
              addCriterion("first_name <>", value, "firstName");
              return (Criteria) this;
          }
  
          public Criteria andFirstNameGreaterThan(String value) {
              addCriterion("first_name >", value, "firstName");
              return (Criteria) this;
          }
  
          public Criteria andFirstNameGreaterThanOrEqualTo(String value) {
              addCriterion("first_name >=", value, "firstName");
              return (Criteria) this;
          }
  
          public Criteria andFirstNameLessThan(String value) {
              addCriterion("first_name <", value, "firstName");
              return (Criteria) this;
          }
  
          public Criteria andFirstNameLessThanOrEqualTo(String value) {
              addCriterion("first_name <=", value, "firstName");
              return (Criteria) this;
          }
  
          public Criteria andFirstNameLike(String value) {
              addCriterion("first_name like", value, "firstName");
              return (Criteria) this;
          }
  
          public Criteria andFirstNameNotLike(String value) {
              addCriterion("first_name not like", value, "firstName");
              return (Criteria) this;
          }
  
          public Criteria andFirstNameIn(List<String> values) {
              addCriterion("first_name in", values, "firstName");
              return (Criteria) this;
          }
  
          public Criteria andFirstNameNotIn(List<String> values) {
              addCriterion("first_name not in", values, "firstName");
              return (Criteria) this;
          }
  
          public Criteria andFirstNameBetween(String value1, String value2) {
              addCriterion("first_name between", value1, value2, "firstName");
              return (Criteria) this;
          }
  
          public Criteria andFirstNameNotBetween(String value1, String value2) {
              addCriterion("first_name not between", value1, value2, "firstName");
              return (Criteria) this;
          }
  
          public Criteria andLastNameIsNull() {
              addCriterion("last_name is null");
              return (Criteria) this;
          }
  
          public Criteria andLastNameIsNotNull() {
              addCriterion("last_name is not null");
              return (Criteria) this;
          }
  
          public Criteria andLastNameEqualTo(String value) {
              addCriterion("last_name =", value, "lastName");
              return (Criteria) this;
          }
  
          public Criteria andLastNameNotEqualTo(String value) {
              addCriterion("last_name <>", value, "lastName");
              return (Criteria) this;
          }
  
          public Criteria andLastNameGreaterThan(String value) {
              addCriterion("last_name >", value, "lastName");
              return (Criteria) this;
          }
  
          public Criteria andLastNameGreaterThanOrEqualTo(String value) {
              addCriterion("last_name >=", value, "lastName");
              return (Criteria) this;
          }
  
          public Criteria andLastNameLessThan(String value) {
              addCriterion("last_name <", value, "lastName");
              return (Criteria) this;
          }
  
          public Criteria andLastNameLessThanOrEqualTo(String value) {
              addCriterion("last_name <=", value, "lastName");
              return (Criteria) this;
          }
  
          public Criteria andLastNameLike(String value) {
              addCriterion("last_name like", value, "lastName");
              return (Criteria) this;
          }
  
          public Criteria andLastNameNotLike(String value) {
              addCriterion("last_name not like", value, "lastName");
              return (Criteria) this;
          }
  
          public Criteria andLastNameIn(List<String> values) {
              addCriterion("last_name in", values, "lastName");
              return (Criteria) this;
          }
  
          public Criteria andLastNameNotIn(List<String> values) {
              addCriterion("last_name not in", values, "lastName");
              return (Criteria) this;
          }
  
          public Criteria andLastNameBetween(String value1, String value2) {
              addCriterion("last_name between", value1, value2, "lastName");
              return (Criteria) this;
          }
  
          public Criteria andLastNameNotBetween(String value1, String value2) {
              addCriterion("last_name not between", value1, value2, "lastName");
              return (Criteria) this;
          }
  
          public Criteria andAgeIsNull() {
              addCriterion("age is null");
              return (Criteria) this;
          }
  
          public Criteria andAgeIsNotNull() {
              addCriterion("age is not null");
              return (Criteria) this;
          }
  
          public Criteria andAgeEqualTo(Integer value) {
              addCriterion("age =", value, "age");
              return (Criteria) this;
          }
  
          public Criteria andAgeNotEqualTo(Integer value) {
              addCriterion("age <>", value, "age");
              return (Criteria) this;
          }
  
          public Criteria andAgeGreaterThan(Integer value) {
              addCriterion("age >", value, "age");
              return (Criteria) this;
          }
  
          public Criteria andAgeGreaterThanOrEqualTo(Integer value) {
              addCriterion("age >=", value, "age");
              return (Criteria) this;
          }
  
          public Criteria andAgeLessThan(Integer value) {
              addCriterion("age <", value, "age");
              return (Criteria) this;
          }
  
          public Criteria andAgeLessThanOrEqualTo(Integer value) {
              addCriterion("age <=", value, "age");
              return (Criteria) this;
          }
  
          public Criteria andAgeIn(List<Integer> values) {
              addCriterion("age in", values, "age");
              return (Criteria) this;
          }
  
          public Criteria andAgeNotIn(List<Integer> values) {
              addCriterion("age not in", values, "age");
              return (Criteria) this;
          }
  
          public Criteria andAgeBetween(Integer value1, Integer value2) {
              addCriterion("age between", value1, value2, "age");
              return (Criteria) this;
          }
  
          public Criteria andAgeNotBetween(Integer value1, Integer value2) {
              addCriterion("age not between", value1, value2, "age");
              return (Criteria) this;
          }
      }
  
      public static class Criteria extends GeneratedCriteria {
  
          protected Criteria() {
              super();
          }
      }
  
      public static class Criterion {
          private String condition;
  
          private Object value;
  
          private Object secondValue;
  
          private boolean noValue;
  
          private boolean singleValue;
  
          private boolean betweenValue;
  
          private boolean listValue;
  
          private String typeHandler;
  
          public String getCondition() {
              return condition;
          }
  
          public Object getValue() {
              return value;
          }
  
          public Object getSecondValue() {
              return secondValue;
          }
  
          public boolean isNoValue() {
              return noValue;
          }
  
          public boolean isSingleValue() {
              return singleValue;
          }
  
          public boolean isBetweenValue() {
              return betweenValue;
          }
  
          public boolean isListValue() {
              return listValue;
          }
  
          public String getTypeHandler() {
              return typeHandler;
          }
  
          protected Criterion(String condition) {
              super();
              this.condition = condition;
              this.typeHandler = null;
              this.noValue = true;
          }
  
          protected Criterion(String condition, Object value, String typeHandler) {
              super();
              this.condition = condition;
              this.value = value;
              this.typeHandler = typeHandler;
              if (value instanceof List<?>) {
                  this.listValue = true;
              } else {
                  this.singleValue = true;
              }
          }
  
          protected Criterion(String condition, Object value) {
              this(condition, value, null);
          }
  
          protected Criterion(String condition, Object value, Object secondValue, String typeHandler) {
              super();
              this.condition = condition;
              this.value = value;
              this.secondValue = secondValue;
              this.typeHandler = typeHandler;
              this.betweenValue = true;
          }
  
          protected Criterion(String condition, Object value, Object secondValue) {
              this(condition, value, secondValue, null);
          }
      }
  }
  ```

- StudentDTOMapper mapper类，提供了默认的增删改查

  ```java
  package com.davidwang456.mybatis.mbg;
  
  import java.util.List;
  
  import org.apache.ibatis.annotations.Param;
  
  public interface StudentDTOMapper {
      int countByExample(StudentDTOExample example);
  
      int deleteByExample(StudentDTOExample example);
  
      int insert(StudentDTO record);
  
      int insertSelective(StudentDTO record);
  
      List<StudentDTO> selectByExample(StudentDTOExample example);
  
      int updateByExampleSelective(@Param("record") StudentDTO record, @Param("example") StudentDTOExample example);
  
      int updateByExample(@Param("record") StudentDTO record, @Param("example") StudentDTOExample example);
  }
  ```

- StudentDTOMapper.xml配置文件

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN" "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.davidwang456.mybatis.mbg.StudentDTOMapper">
  <resultMap id="BaseResultMap" type="com.davidwang456.mybatis.mbg.StudentDTO">
    <result column="id" jdbcType="INTEGER" property="id" />
    <result column="first_name" jdbcType="VARCHAR" property="firstName" />
    <result column="last_name" jdbcType="VARCHAR" property="lastName" />
    <result column="age" jdbcType="INTEGER" property="age" />
  </resultMap>
  <sql id="Example_Where_Clause">
    <where>
      <foreach collection="oredCriteria" item="criteria" separator="or">
        <if test="criteria.valid">
          <trim prefix="(" prefixOverrides="and" suffix=")">
            <foreach collection="criteria.criteria" item="criterion">
              <choose>
                <when test="criterion.noValue">
                  and ${criterion.condition}
                </when>
                <when test="criterion.singleValue">
                  and ${criterion.condition} #{criterion.value}
                </when>
                <when test="criterion.betweenValue">
                  and ${criterion.condition} #{criterion.value} and #{criterion.secondValue}
                </when>
                <when test="criterion.listValue">
                  and ${criterion.condition}
                  <foreach close=")" collection="criterion.value" item="listItem" open="(" separator=",">
                    #{listItem}
                  </foreach>
                </when>
              </choose>
            </foreach>
          </trim>
        </if>
      </foreach>
    </where>
  </sql>
  <sql id="Update_By_Example_Where_Clause">
    <where>
      <foreach collection="example.oredCriteria" item="criteria" separator="or">
        <if test="criteria.valid">
          <trim prefix="(" prefixOverrides="and" suffix=")">
            <foreach collection="criteria.criteria" item="criterion">
              <choose>
                <when test="criterion.noValue">
                  and ${criterion.condition}
                </when>
                <when test="criterion.singleValue">
                  and ${criterion.condition} #{criterion.value}
                </when>
                <when test="criterion.betweenValue">
                  and ${criterion.condition} #{criterion.value} and #{criterion.secondValue}
                </when>
                <when test="criterion.listValue">
                  and ${criterion.condition}
                  <foreach close=")" collection="criterion.value" item="listItem" open="(" separator=",">
                    #{listItem}
                  </foreach>
                </when>
              </choose>
            </foreach>
          </trim>
        </if>
      </foreach>
    </where>
  </sql>
  <sql id="Base_Column_List">
    id, first_name, last_name, age
  </sql>
  <select id="selectByExample" parameterType="com.davidwang456.mybatis.mbg.StudentDTOExample" resultMap="BaseResultMap">
    select
    <if test="distinct">
      distinct
    </if>
    <include refid="Base_Column_List" />
    from student
    <if test="_parameter != null">
      <include refid="Example_Where_Clause" />
    </if>
    <if test="orderByClause != null">
      order by ${orderByClause}
    </if>
  </select>
  <delete id="deleteByExample" parameterType="com.davidwang456.mybatis.mbg.StudentDTOExample">
    delete from student
    <if test="_parameter != null">
      <include refid="Example_Where_Clause" />
    </if>
  </delete>
  <insert id="insert" parameterType="com.davidwang456.mybatis.mbg.StudentDTO">
    insert into student (id, first_name, last_name, 
      age)
    values (#{id,jdbcType=INTEGER}, #{firstName,jdbcType=VARCHAR}, #{lastName,jdbcType=VARCHAR}, 
      #{age,jdbcType=INTEGER})
  </insert>
  <insert id="insertSelective" parameterType="com.davidwang456.mybatis.mbg.StudentDTO">
    insert into student
    <trim prefix="(" suffix=")" suffixOverrides=",">
      <if test="id != null">
        id,
      </if>
      <if test="firstName != null">
        first_name,
      </if>
      <if test="lastName != null">
        last_name,
      </if>
      <if test="age != null">
        age,
      </if>
    </trim>
    <trim prefix="values (" suffix=")" suffixOverrides=",">
      <if test="id != null">
        #{id,jdbcType=INTEGER},
      </if>
      <if test="firstName != null">
        #{firstName,jdbcType=VARCHAR},
      </if>
      <if test="lastName != null">
        #{lastName,jdbcType=VARCHAR},
      </if>
      <if test="age != null">
        #{age,jdbcType=INTEGER},
      </if>
    </trim>
  </insert>
  <select id="countByExample" parameterType="com.davidwang456.mybatis.mbg.StudentDTOExample" resultType="java.lang.Integer">
    select count(*) from student
    <if test="_parameter != null">
      <include refid="Example_Where_Clause" />
    </if>
  </select>
  <update id="updateByExampleSelective" parameterType="map">
    update student
    <set>
      <if test="record.id != null">
        id = #{record.id,jdbcType=INTEGER},
      </if>
      <if test="record.firstName != null">
        first_name = #{record.firstName,jdbcType=VARCHAR},
      </if>
      <if test="record.lastName != null">
        last_name = #{record.lastName,jdbcType=VARCHAR},
      </if>
      <if test="record.age != null">
        age = #{record.age,jdbcType=INTEGER},
      </if>
    </set>
    <if test="_parameter != null">
      <include refid="Update_By_Example_Where_Clause" />
    </if>
  </update>
  <update id="updateByExample" parameterType="map">
    update student
    set id = #{record.id,jdbcType=INTEGER},
      first_name = #{record.firstName,jdbcType=VARCHAR},
      last_name = #{record.lastName,jdbcType=VARCHAR},
      age = #{record.age,jdbcType=INTEGER}
    <if test="_parameter != null">
      <include refid="Update_By_Example_Where_Clause" />
    </if>
  </update>
</mapper>
```

自己新增配置类和测试类

配置类：

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
      <mapper resource = "mapper/StudentDTOMapper.xml"/>
   </mappers> 
  
</configuration>
```

测试类：

```java
package com.davidwang456.mybatis.mbg;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;

public class MBGTest {

	public static void main(String[] args) throws IOException {
		testMBG();
	}
	
	private static void testMBG() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();	
	      StudentDTOMapper studentMapper=session.getMapper(StudentDTOMapper.class);
	      StudentDTOExample param=new StudentDTOExample();
	      param.createCriteria().andFirstNameLike("%wang%")
	      .andLastNameLike("%david%");

	      List<StudentDTO> stus=studentMapper.selectByExample(param);     
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

因打印信息需要，在实体类中重写toString方法

```java
	@Override
	   public String toString() {
	    return "student [id=" + id + ", firstName=" + firstName
	    		 + ", lastName=" + lastName + ", age=" +age+ ']';
	   }
```

运行测试程序，结果如下。

```tex
==>  Preparing: select id, first_name, last_name, age from student WHERE ( first_name like ? and last_name like ? )
==> Parameters: %wang%(String), %david%(String)
<==    Columns: id, first_name, last_name, age
<==        Row: 1, wang1, david1, 21
<==        Row: 2, wang2, david2, 22
<==        Row: 3, wang3, david3, 23
<==        Row: 4, wang4, david4, 24
<==        Row: 5, wang5, david5, 25
<==        Row: 6, wang6, david6, 26
<==        Row: 7, wang7, david7, 27
<==        Row: 8, wang8, david8, 30
<==      Total: 8
------------------getStudentInfoByCondition query------------start-----------
student [id=1, firstName=wang1, lastName=david1, age=21]
student [id=2, firstName=wang2, lastName=david2, age=22]
student [id=3, firstName=wang3, lastName=david3, age=23]
student [id=4, firstName=wang4, lastName=david4, age=24]
student [id=5, firstName=wang5, lastName=david5, age=25]
student [id=6, firstName=wang6, lastName=david6, age=26]
student [id=7, firstName=wang7, lastName=david7, age=27]
student [id=8, firstName=wang8, lastName=david8, age=30]
------------------getStudentInfoByCondition query------------end----------
```

## Mybatis-Plus实例：不用配置文件的方式

**创建一个空的 Spring Boot 工程**

### 添加依赖

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
	xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
	<modelVersion>4.0.0</modelVersion>
	<parent>
		<groupId>org.springframework.boot</groupId>
		<artifactId>spring-boot-starter-parent</artifactId>
		<version>2.5.3</version>
		<relativePath/> <!-- lookup parent from repository -->
	</parent>
	<groupId>com.example</groupId>
	<artifactId>MybatisPlusTest</artifactId>
	<version>4.8.0-SNAPSHOT</version>
	<name>MybatisPlusTest</name>
	<description>Demo project for Spring Boot</description>
	<properties>
		<java.version>1.8</java.version>
	</properties>
	<dependencies>
		<dependency>
			<groupId>org.mybatis.spring.boot</groupId>
			<artifactId>mybatis-spring-boot-starter</artifactId>
			<version>2.2.0</version>
		</dependency>
		<dependency>
		    <groupId>org.springframework.boot</groupId>
		    <artifactId>spring-boot-starter-web</artifactId>
		</dependency>
		<dependency>
			<groupId>mysql</groupId>
			<artifactId>mysql-connector-java</artifactId>
			<scope>runtime</scope>
		</dependency>
		<dependency>
			<groupId>org.springframework.boot</groupId>
			<artifactId>spring-boot-starter-test</artifactId>
			<scope>test</scope>
		</dependency>
		<dependency>
		    <groupId>org.projectlombok</groupId>
		    <artifactId>lombok</artifactId>
		    <scope>provided</scope>
		</dependency>		
	    <dependency>
	        <groupId>com.baomidou</groupId>
	        <artifactId>mybatis-plus-boot-starter</artifactId>
	        <version>3.4.2</version>
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

### 配置数据源

```yaml
spring:
  datasource:
    driver-class-name: com.mysql.cj.jdbc.Driver
    url: jdbc:mysql://localhost:3306/davidwang456?characterEncoding=UTF-8&useSSL=false&useLegacyDatetimeCode=false&serverTimezone=UTC
    username: root
    password: wangwei456
```

### 创建实体类

```java
package com.davidwang456.mybatis.plus;

import lombok.Data;

@Data
public class Student {
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

### 创建mapper

```java
package com.davidwang456.mybatis.plus.mapper;

import com.davidwang456.mybatis.plus.Student;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;

public interface StudentMapper extends BaseMapper<Student> {

}
```

### 创建测试controller

```java
package com.davidwang456.mybatis.plus;

import java.util.List;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.ResponseBody;
import org.springframework.web.bind.annotation.RestController;

import com.davidwang456.mybatis.plus.mapper.StudentMapper;

@RestController
public class MybatisPlusControllerTest {
	@Autowired
	private StudentMapper studentMapper;
	
	
	@GetMapping("/studentList")
	@ResponseBody
	public List<Student> getStudents() {
        List<Student> userList = studentMapper.selectList(null);
        userList.forEach(System.out::println);
        return userList;
	}
}
```

启动应用，进行测试http://localhost:8080/studentList

```json
[{"id":1,"firstName":"wang1","lastName":"david1","age":21},{"id":2,"firstName":"wang2","lastName":"david2","age":22},{"id":3,"firstName":"wang3","lastName":"david3","age":23},{"id":4,"firstName":"wang4","lastName":"david4","age":24},{"id":5,"firstName":"wang5","lastName":"david5","age":25},{"id":6,"firstName":"wang6","lastName":"david6","age":26},{"id":7,"firstName":"wang7","lastName":"david7","age":27},{"id":8,"firstName":"wang8","lastName":"david8","age":30}]
```

结果符合预期。

## 总结

- Mybatis-generator(简称MBG)主要完成的工作是依据数据库表创建对应的model、dao、mapping文件，可以通过Maven插件或者mybatis-generator的jar包生成。
- Mybatis-Plus（简称MP）是Mybatis的增强工具（MBG和通用Mapper可看成插件），在Mybatis的基础上增加了很多功能，简化开发，提高效率。