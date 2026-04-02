package com.davidwang456.mybatis.annotation;

import java.io.IOException;
import java.io.Reader;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.Configuration;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class AnotationTest {

	public static void main(String[] args) throws IOException {
		getStudentInfoById();
	   }
	
	
	private static void getStudentInfoById() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		 
	      Configuration conf=session.getConfiguration();
	      conf.addMapper(StudentMapper.class);
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO stu=studentMapper.getStudentByIdCondition(3, "wang3", "david3", 23);
	      System.out.println(stu.toString());

	      session.commit(true);
	      session.close();
	}
}
