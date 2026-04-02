package com.davidwang456.mybatis.rangequery;

import java.io.IOException;
import java.io.Reader;
import java.text.ParseException;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;

public class RangeQueryTest {

	public static void main(String[] args) throws IOException, ParseException {
		testBatch1();
	   }
	
	public static void testBatch1() throws IOException, ParseException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO query=new StudentQueryDTO();
	      query.setKeyword("david");
	      query.setStartDate(DateUtils.getDateByString("2020-12-01 10:35:00"));
	      query.setEndDate(DateUtils.getDateByString("2020-12-01 10:45:00"));
	      query.setOrderBy("DESC");
	      query.setSort("create_time");
	      List<StudentDTO> dtos=studentMapper.getStudentInfoByCondition(query);
	      for(StudentDTO dto:dtos) {
	    	  System.out.println(dto.toString());
	      }
	      session.commit(true);
	      session.close();	
	}
}
