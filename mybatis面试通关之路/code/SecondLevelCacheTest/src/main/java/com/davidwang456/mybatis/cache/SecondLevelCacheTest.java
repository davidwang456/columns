package com.davidwang456.mybatis.cache;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class SecondLevelCacheTest {

	public static void main(String[] args) throws IOException {
		testCache();
	   }
	
	private static void testCache() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();	
	      SqlSession session2 = sqlSessionFactory.openSession();
	      System.out.println("session:"+session+",session2:"+session2);
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentMapper studentMapper2 =session2.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("wang");
	      param.setLastName("david");
	      param.setOrderBy("DESC");
	      param.setSort("age");

	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition(param);	      
	      printResult(stus,"Session1 first query");
	      //session.commit(true);
	      
	      //模拟人的操作
	      //updateWithoutJdbc();
	      //mybatis本身的update语句
	      studentMapper2.upStudentInfoById(8, 30);
	      session2.commit(true);
	      List<StudentDTO> stusCacge=studentMapper.getStudentInfoByCondition(param);
	      printResult(stusCacge,"Session1 cache query");
	      session.commit(true);
	      
	      
		  List<StudentDTO> stuSession2=studentMapper2.getStudentInfoByCondition(param);
		  printResult(stuSession2,"Session2 query"); 
		  session2.commit(true);
		 
	      
	      session.close();
	      session2.close();
	}
	
	private static void printResult(List<StudentDTO> stus,String name) {
		System.out.println("------------------"+name+"------------start-----------");
		for(StudentDTO dto:stus) {
			System.out.println(dto.toString());
		}		
		System.out.println("------------------"+name+"------------end----------");
	}
}
