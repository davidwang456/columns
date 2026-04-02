package com.davidwang456.mybatis.rediscache;

import java.io.IOException;
import java.io.Reader;
import java.text.ParseException;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;

public class RedisCacheTest {

	public static void main(String[] args) throws IOException, ParseException {
		testStoreProcedure();
		System.out.println("***************************************************************");
		testStoreProcedure();
	   }
	
	public static void testStoreProcedure() throws IOException, ParseException {
		  Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO dto=studentMapper.getStudentInfoById(1);
	      System.out.println(dto.toString());
	      session.commit(true);
	      session.close();	
	}
	
	public static void testCommonQuery() throws IOException, ParseException {
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
}
