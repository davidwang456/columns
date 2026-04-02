package com.davidwang456.mybatis.enumtest;

import java.io.IOException;
import java.io.Reader;
import java.util.List;
import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;

public class EnumTest {

	public static void main(String[] args) throws IOException {
		insert();
		insert1();
		insert2();
		insert3();
		//query();
		//query1();
		//query2();
		//query3();
	   }
	
	public static void insert() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO param=new StudentDTO();
	      param.setFirstName("david");
	      param.setLastName("wang");
	      param.setAge(20);
	      param.setStatus(Status.ACTIVE);
	      Integer effected=studentMapper.insert(param);
	      if(effected>0) {
	      System.out.println("插入记录成功！ ");
	      }
	      session.commit(true);
	      session.close();		
	}
	
	public static void insert1() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO param=new StudentDTO();
	      param.setFirstName("david1");
	      param.setLastName("wang1");
	      param.setAge(20);
	      param.setStatus(Status.ACTIVE);
	      Integer effected=studentMapper.insert1(param);
	      if(effected>0) {
	      System.out.println("插入记录成功！ ");
	      }
	      session.commit(true);
	      session.close();		
	}
	
	public static void insert2() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO param=new StudentDTO();
	      param.setFirstName("david2");
	      param.setLastName("wang2");
	      param.setAge(20);
	      param.setStatus(Status.ACTIVE);
	      Integer effected=studentMapper.insert2(param);
	      if(effected>0) {
	      System.out.println("插入记录成功！ ");
	      }
	      session.commit(true);
	      session.close();		
	}
	
	public static void insert3() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO param=new StudentDTO();
	      param.setFirstName("david3");
	      param.setLastName("wang3");
	      param.setAge(20);
	      param.setStatus(Status.ACTIVE);
	      Integer effected=studentMapper.insert3(param);
	      if(effected>0) {
	      System.out.println("插入记录成功！ ");
	      }
	      session.commit(true);
	      session.close();		
	}
	
	public static void query() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setKeyword("david");
	      param.setStatus(Status.ACTIVE);
	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition(param);
	      System.out.println("------------------query------------------");
	      for(StudentDTO stu:stus) {
	      System.out.println(stu.toString());
	      }
	      System.out.println("------------------query------------------");
	      session.commit(true);
	      session.close();		
	}
	
	public static void query1() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setKeyword("david");
	      param.setStatus(Status.ACTIVE);
	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition1(param);
	      System.out.println("------------------query1------------------");
	      for(StudentDTO stu:stus) {
	      System.out.println(stu.toString());
	      }
	      System.out.println("------------------query1------------------");
	      session.commit(true);
	      session.close();		
	}
	
	public static void query2() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setKeyword("david");
	      param.setStatus(Status.ACTIVE);
	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition2(param);
	      System.out.println("------------------query2------------------");
	      for(StudentDTO stu:stus) {
	      System.out.println(stu.toString());
	      }
	      System.out.println("------------------query2------------------");
	      session.commit(true);
	      session.close();		
	}
	
	public static void query3() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setKeyword("david");
	      param.setStatus(Status.ACTIVE);
	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition3(param);
	      System.out.println("------------------query3------------------");
	      for(StudentDTO stu:stus) {
	      System.out.println(stu.toString());
	      }
	      System.out.println("------------------query3------------------");
	      session.commit(true);
	      session.close();		
	}
}
