package com.davidwang456.mybatis.typealias;

import java.io.IOException;
import java.io.Reader;
import java.util.List;
import java.util.Map;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class TypeAliasTest {

	public static void main(String[] args) throws IOException {
		getTypeAlias4All();
	   }
	
	private static void getTypeAlias4All() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      Map<String, Class<?>> alias=session.getConfiguration().getTypeAliasRegistry().getTypeAliases();
	      for(String name:alias.keySet()) {
	    	  System.out.println("alias name:"+name+",alias type:"+alias.get(name).getCanonicalName());
	      }

	      session.commit(true);
	      session.close();
	}
	
	private static void testTypeAlias4Integer() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO stu=studentMapper.getStudentInfoById(3);
	      System.out.println(stu.toString());

	      session.commit(true);
	      session.close();
	}
	
	private static void testTypeAlias() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("wang");
	      param.setLastName("david");
	      param.setOrderBy("DESC");
	      param.setSort("age");
	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition(param);
	      for(StudentDTO stu: stus) {
	    	  System.out.println(stu.toString());
	      }
	      session.commit(true);
	      session.close();
	}
}
