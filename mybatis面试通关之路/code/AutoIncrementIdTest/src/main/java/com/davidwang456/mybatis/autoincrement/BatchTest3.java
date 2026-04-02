package com.davidwang456.mybatis.autoincrement;

import java.io.IOException;
import java.io.Reader;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;

public class BatchTest3 {

	public static void main(String[] args) throws IOException {
		testBatch();
	   }
	
	public static void testBatch() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession(true);      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      Long start=System.currentTimeMillis();
	      int seq=0;
	      for(int i=0;i<10000;i++) {
		      StudentDTO dto=new StudentDTO();
		      seq=i+1;
		      dto.setFirstName("www"+seq);
		      dto.setLastName("david"+seq);
		      dto.setAge(20+seq%10);		      
		      if(i%100==0) {
		    	  studentMapper.deleteById(i-1);
		      }else {
		    	  studentMapper.insertStudentInfo(dto);
		      }
	      }
	      long end = System.currentTimeMillis();
	      System.out.println("------BatchTest3-------" + (start - end) + "ms---------------");
	      session.close();	
	}
}
