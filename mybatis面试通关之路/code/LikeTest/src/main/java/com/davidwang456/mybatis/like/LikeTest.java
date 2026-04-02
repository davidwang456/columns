package com.davidwang456.mybatis.like;

import java.io.IOException;
import java.io.Reader;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.ExecutorType;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class LikeTest {

	public static void main(String[] args) throws IOException {
		testRight();
	   }
	
	private static void testBoth() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("%rearch:www:100%");
	      param.setLastName("%rearch:david:100%");
	      param.setOrderBy("DESC");
	      param.setSort("age");
	      Long start=System.currentTimeMillis();
	      List<StudentDTO> stus=studentMapper.getStudentInfoByConditionBoth(param);
	      System.out.println("testBoth cost:"+(System.currentTimeMillis()-start)+" ms,fetch size:"+stus.size());
	      session.commit(true);
	      session.close();
	}
	
	private static void testRight() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("rearch:www:100");
	      param.setLastName("rearch:david:100");
	      param.setOrderBy("DESC");
	      param.setSort("age");
	      Long start=System.currentTimeMillis();
	      List<StudentDTO> stus=studentMapper.getStudentInfoByConditionRight(param);
	      System.out.println("testRight cost:"+(System.currentTimeMillis()-start)+"ms,fetch size："+stus.size());
	      session.commit(true);
	      session.close();
	}
	
	private static void testLeft() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("rearch:www:100");
	      param.setLastName("rearch:david:100");
	      param.setOrderBy("DESC");
	      param.setSort("age");
	      Long start=System.currentTimeMillis();
	      List<StudentDTO> stus=studentMapper.getStudentInfoByConditionLeft(param);
	      System.out.println("testLeft cost:"+(System.currentTimeMillis()-start)+"ms,fetch size："+stus.size());
	      session.commit(true);
	      session.close();
	}
	
	public static void initData() throws IOException {
		  Random random = new Random();
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession(ExecutorType.BATCH,false);      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      
	      Long start=System.currentTimeMillis();
	      List<StudentDTO> dtos=null;
	      int seq=0;
	      List<String> departmentList=new ArrayList<>();
	      departmentList.add("rearch");
	      departmentList.add("develop");
	      departmentList.add("product");
	      departmentList.add("mark");
	      departmentList.add("other");
	      String prex="";
	      for(int i=0;i<1000;i++) {
	    	  prex=departmentList.get(random.nextInt(departmentList.size()));
	    	  dtos=new ArrayList<StudentDTO>();
	    	  for(int j=0;j<1000;j++) {
	    		  seq=i*1000+j+1;
			      StudentDTO dto=new StudentDTO();
			      dto.setFirstName(prex+":www:"+seq);
			      dto.setLastName(prex+":david:"+seq);
			      dto.setAge(20+seq%10);
			      dtos.add(dto);  
	    	  }
	    	  studentMapper.insertBatchStudentInfo(dtos);
	    	  dtos=null;
	      }
		  
	      session.commit(true);
	      long end = System.currentTimeMillis();
	      System.out.println("------initData-------" + (start - end) + "ms---------------");
	      session.close();	
	}
}
