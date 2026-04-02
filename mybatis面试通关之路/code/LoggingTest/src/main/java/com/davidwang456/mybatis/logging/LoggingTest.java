package com.davidwang456.mybatis.logging;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.logging.Log;
import org.apache.ibatis.logging.LogFactory;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class LoggingTest {

	public static void main(String[] args) throws IOException {
		testLogging();
	   }
	
	private static void testLogging() throws IOException {
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
	      printResult(stus,"getStudentInfoByCondition query");
	      session.commit(true);
	      session.close();
	}
	
	private static void printResult(List<StudentDTO> stus,String name) {
		LogFactory.useStdOutLogging();
		Log log= LogFactory.getLog(org.apache.ibatis.logging.stdout.StdOutImpl.class);
		log.debug("------------------"+name+"------------start-----------");
		for(StudentDTO dto:stus) {
			log.debug(dto.toString());
		}		
		log.debug("------------------"+name+"------------end----------");
	}
}
