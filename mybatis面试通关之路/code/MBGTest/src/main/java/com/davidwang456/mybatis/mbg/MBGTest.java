package com.davidwang456.mybatis.mbg;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;

public class MBGTest {

	public static void main(String[] args) throws IOException {
		testMBG();
	}
	
	private static void testMBG() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();	
	      StudentDTOMapper studentMapper=session.getMapper(StudentDTOMapper.class);
	      StudentDTOExample param=new StudentDTOExample();
	      param.createCriteria().andFirstNameLike("%wang%")
	      .andLastNameLike("%david%");

	      List<StudentDTO> stus=studentMapper.selectByExample(param);     
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
