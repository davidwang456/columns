package com.davidwang456.mybatis.jdbc;

import java.io.IOException;
import java.io.Reader;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;

import com.davidwang456.mybatis.jdbc.dto.StudentDTO;
import com.davidwang456.mybatis.jdbc.mapper.StudentMapper;

public class MapUnderscoreToCamelCaseTest {

	public static void main(String[] args) throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      
	      StudentDTO stu=studentMapper.getStudentInfoById(5);
	      System.out.println(stu.toString());
	      session.commit(true);
	      session.close();
				
	   }
}
