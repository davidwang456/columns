package com.davidwang456.mybatis;

import java.io.IOException;
import java.io.Reader;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.RowBounds;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;

import com.davidwang456.mybatis.dto.StudentDTO;
import com.davidwang456.mybatis.dto.StudentQueryDTO;
import com.davidwang456.mybatis.util.MappedStatementUtil;

public class PrintSqlName {
	   public static void main(String args[]) throws IOException{
		      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
		      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
		      SqlSession session = sqlSessionFactory.openSession();
		      //打印所有STATEMENT的名称
		      System.out.println("--------------print name and Object------------------");
		      MappedStatementUtil.printMappedStatementName(session);
		      
		      StudentQueryDTO param=new StudentQueryDTO();
		      param.setKeyword("david");		
		      RowBounds rbs=new RowBounds(0,5);
		      //1. 全名称的statement
		      //List<StudentDTO> stu=session.selectList("com.davidwang456.mybatis.StudentDao.getStudentInfoByCondition",param,rbs);
		      //2. 简名称的statement
		      //打印所有STATEMENT的名称及执行的sql，注意：此方法需要传入入参
		      System.out.println("---------------print name and sql---------------------");
		      MappedStatementUtil.printMappedSql(session,param);
		      List<StudentDTO> stu=session.selectList("getStudentInfoByCondition",param,rbs);
		      System.out.println("--------------record selected:"+stu.size()+"-----------");
		      for(StudentDTO dto:stu) {
		    	  System.out.println(dto.toString());
		      }
		      
		      session.commit(true);
		      session.close();
					
		   }

}
