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
