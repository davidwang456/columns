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
