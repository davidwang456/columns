package com.davidwang456.mybatis.resulttype;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class ResultTypeMapTest {

	public static void main(String[] args) throws IOException {
		testID();
	   }
	
	private static void testID() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      
	      List<Integer> cnt=studentMapper.getUserCount();
	      System.out.println("总数cnt="+cnt.get(0));
	      List<Integer> ids=studentMapper.getUserIdList();
	      for(Integer id:ids) {
	    	  System.out.println("id="+id);
	      }
	      session.commit(true);
	      session.close();
	}
}
