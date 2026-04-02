package com.davidwang456.mybatis.dynamicsort;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class DynamicSortTest {

	public static void main(String[] args) throws IOException {
		testID();
	   }
	
	private static void testID() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();		      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setKeyword("david");
	      param.setOrderBy("DESC");
	      param.setSort("id");
	      Long start=System.currentTimeMillis();
	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition(param);
	      for(StudentDTO dto:stus) {
	    	  System.out.println(dto.toString());
	      }
	      
	      System.out.println("testBoth cost:"+(System.currentTimeMillis()-start)+" ms,fetch size:"+stus.size());
	      session.commit(true);
	      session.close();
	}
}
