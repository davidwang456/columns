package com.davidwang456.mybatis.autoincrement;

import java.io.IOException;
import java.io.Reader;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;

public class AutoIncrementIdTest {

	public static void main(String[] args) throws IOException {
		testBatch1();
	   }
	
	public static void testBatch1() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO dto=new StudentDTO();
	      dto.setFirstName("www");
	      dto.setLastName("david");
	      dto.setAge(20);	      
	      studentMapper.insertStudentInfo(dto);
	      System.out.println("auto increment id:"+dto.getId());
	      session.commit(true);
	      session.close();	
	}
}
