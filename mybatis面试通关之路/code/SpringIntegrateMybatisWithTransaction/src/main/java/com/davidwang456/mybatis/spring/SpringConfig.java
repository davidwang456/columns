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
