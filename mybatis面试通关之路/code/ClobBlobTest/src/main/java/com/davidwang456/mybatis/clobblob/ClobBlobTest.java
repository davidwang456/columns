package com.davidwang456.mybatis.clobblob;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InputStream;
import java.io.Reader;
import java.io.UnsupportedEncodingException;
import java.text.ParseException;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;

public class ClobBlobTest {

	public static void main(String[] args) throws IOException, ParseException {
		query();
	   }
	
	public static void insert() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO dto=new StudentDTO();
	      dto.setFirstName("david");
	      dto.setLastName("www");
	      dto.setAge(25);	      
	      dto.setContent(new String(readToByte("C:\\documet\\mybatis\\wangwei\\README.md"), "UTF-8"));
          dto.setImage(readToByte("C:\\documet\\mybatis\\wangwei\\别人家孩子.jpg"));
          studentMapper.insertStudentInfo(dto);
	      session.commit(true);
	      session.close();	
	}
	
	public static byte[] readToByte(String fileName) {
        byte[] image = null; 
        try {
      	  
            //读取用户头像图片
            File file = new File(fileName); 
            InputStream is = new FileInputStream(file); 
            image = new byte[is.available()]; 
            is.read(image); 
            is.close(); 
        } catch (Exception e){ 
            e.printStackTrace(); 
        } 
        return image;
	}
	
	public static void query() throws IOException, ParseException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();      
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO query=new StudentQueryDTO();
	      query.setKeyword("david");
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
