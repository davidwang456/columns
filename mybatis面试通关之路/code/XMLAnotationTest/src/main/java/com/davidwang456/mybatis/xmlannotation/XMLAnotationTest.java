package com.davidwang456.mybatis.xmlannotation;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.Configuration;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class XMLAnotationTest {

	public static void main(String[] args) throws IOException {
		getStudentInfoByCondition();
	   }
	
	private static void getStudentInfoByCondition() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();	
	      
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("wang");
	      param.setLastName("david");
	      param.setOrderBy("DESC");
	      param.setSort("age");	     
          StudentMapper sm=session.getMapper(StudentMapper.class);
          List<StudentDTO> stus=sm.getStudentInfoByCondition(param);
	      printResult(stus,"getStudentInfoByCondition xml query");
	      
	      
	      Configuration conf=session.getConfiguration();
	      conf.addMapper(StudentAnnotationMapper.class);
	      StudentAnnotationMapper studentMapper=session.getMapper(StudentAnnotationMapper.class);
	      List<StudentDTO> stus2=studentMapper.getStudentByIdCondition("wang", "david");
	      printResult(stus2,"getStudentByIdCondition anotation query");	
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
