package com.davidwang456.mybatis.spring;

import java.io.IOException;
import java.util.List;

import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;

import com.davidwang456.mybatis.spring.mapper.StudentMapper;


public class SpringIntegrateMybatisTest {

	@SuppressWarnings("resource")
	public static void main(String[] args) throws IOException {
		 AnnotationConfigApplicationContext ctx = new AnnotationConfigApplicationContext(SpringConfig.class);
		 SqlSessionFactory ssf=ctx.getBean(SqlSessionFactory.class);
		 ;
		 for(String name:ctx.getBeanDefinitionNames()) {
			 System.out.println("bean name:"+name+",bean type:"+ctx.getBean(name).toString());
		 }
		 
		 SqlSession session=ssf.openSession();
		 //session.getConfiguration().addMapper(StudentMapper.class);
		 
		 StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO param=new StudentQueryDTO();
	      param.setFirstName("wang");
	      param.setLastName("david");
	      param.setOrderBy("DESC");
	      param.setSort("age");

	      List<StudentDTO> stus=studentMapper.getStudentInfoByCondition(param);	      
	      printResult(stus,"query");
		 
	   }
	
	private static void printResult(List<StudentDTO> stus,String name) {
		System.out.println("------------------"+name+"------------start-----------");
		for(StudentDTO dto:stus) {
			System.out.println(dto.toString());
		}		
		System.out.println("------------------"+name+"------------end----------");
	}
}
