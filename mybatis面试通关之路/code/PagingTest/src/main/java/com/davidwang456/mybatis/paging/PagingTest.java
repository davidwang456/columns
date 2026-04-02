package com.davidwang456.mybatis.paging;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.RowBounds;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class PagingTest {

	public static void main(String[] args) throws IOException {
		testPhysicalPaging();
	   }
	
	private static void testLogicPaging() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();	
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("wang");
	      param.setLastName("david");
	      param.setOrderBy("DESC");
	      param.setSort("age");	     
	      RowBounds rbs=new RowBounds(0,5);
	      List<StudentDTO> stus= session.selectList("getStudentInfoByCondition", param, rbs);     
	      printResult(stus,"getStudentInfoByCondition query");
	      session.commit(true);
	      session.close();
	}
	
	private static void testPhysicalPaging() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();	
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("wang");
	      param.setLastName("david");
	      param.setOrderBy("DESC");
	      param.setSort("age");	     
          param.setPage(1);
          param.setPageSize(5);
          StudentMapper sm=session.getMapper(StudentMapper.class);
          List<StudentDTO> stus=sm.getStudentInfoByCondition(param);
	      printResult(stus,"getStudentInfoByCondition query");
	      session.commit(true);
	      session.close();
	}
	
	private static void printResult(List<StudentDTO> stus,String name) {
		System.out.println("------------------"+name+"------------start-----------");
		for(StudentDTO dto:stus) {
			System.out.println(dto.toString());
		}		
		System.out.println("------------------"+name+"------------end----------");
	}
}
