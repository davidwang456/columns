package com.davidwang456.mybatis.batch;

import java.io.IOException;
import java.io.Reader;
import java.util.ArrayList;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.ExecutorType;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;

public class BatchTest {

	public static void main(String[] args) throws IOException {
		testBatch4();
	   }
	
	public static void testBatch1() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      Long start=System.currentTimeMillis();
	      int seq=0;
	      for(int i=0;i<1000000;i++) {
		      StudentDTO dto=new StudentDTO();
		      seq=i+1;
		      dto.setFirstName("www"+seq);
		      dto.setLastName("david"+seq);
		      dto.setAge(20+seq%10);
		      studentMapper.insertStudentInfo(dto);
	      }
	      session.commit(true);
	      long end = System.currentTimeMillis();
	      System.out.println("------testBatch1-------" + (start - end) + "ms---------------");
	      session.close();	
	}
	
	public static void testBatch2() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession(ExecutorType.BATCH,false);      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      Long start=System.currentTimeMillis();
	      int seq=0;
	      for(int i=0;i<1000000;i++) {
		      StudentDTO dto=new StudentDTO();
		      seq=i+1;
		      dto.setFirstName("www"+seq);
		      dto.setLastName("david"+seq);
		      dto.setAge(20+seq%10);
		      studentMapper.insertStudentInfo(dto);
	      }
	      session.commit(true);
	      long end = System.currentTimeMillis();
	      System.out.println("------testBatch2------" + (start - end) + "ms---------------");
	      session.close();	
	}
	
	public static void testBatch3() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession(ExecutorType.BATCH,false);      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      
	      Long start=System.currentTimeMillis();
	      List<StudentDTO> dtos=new ArrayList<StudentDTO>();
	      int seq=0;
	      for(int i=0;i<1000000;i++) {
		      StudentDTO dto=new StudentDTO();
		      seq=i+1;
		      dto.setFirstName("www"+seq);
		      dto.setLastName("david"+seq);
		      dto.setAge(20+seq%10);
	      }
		  studentMapper.insertBatchStudentInfo(dtos);
	      session.commit(true);
	      long end = System.currentTimeMillis();
	      System.out.println("------testBatch3-------" + (start - end) + "ms---------------");
	      session.close();	
	}
	
	public static void testBatch4() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession(ExecutorType.BATCH,false);      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      
	      Long start=System.currentTimeMillis();
	      List<StudentDTO> dtos=null;
	      int seq=0;
	      for(int i=0;i<1000;i++) {
	    	  dtos=new ArrayList<StudentDTO>();
	    	  for(int j=0;j<1000;j++) {
	    		  seq=i*1000+j+1;
			      StudentDTO dto=new StudentDTO();
			      dto.setFirstName("www"+seq);
			      dto.setLastName("david"+seq);
			      dto.setAge(20+seq%10);
			      dtos.add(dto);  
	    	  }
	    	  studentMapper.insertBatchStudentInfo(dtos);
	    	  dtos=null;
	      }
		  
	      session.commit(true);
	      long end = System.currentTimeMillis();
	      System.out.println("------testBatch4-------" + (start - end) + "ms---------------");
	      session.close();	
	}
}
