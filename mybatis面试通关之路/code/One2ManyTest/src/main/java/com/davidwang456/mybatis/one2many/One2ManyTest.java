package com.davidwang456.mybatis.one2many;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InputStream;
import java.io.Reader;
import java.io.UnsupportedEncodingException;
import java.text.ParseException;
import java.util.ArrayList;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;

public class One2ManyTest {

	public static void main(String[] args) throws IOException, ParseException {
		//insert();
		queryStudentInfo();
		//queryStudentInfoManyStep();
	   }
	
	public static void queryStudentInfo() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentQueryDTO query=new StudentQueryDTO();
	      query.setId(1);
	      query.setKeyword("david");
	      query.setSort("create_time");
	      query.setOrderBy("ASC");
	      List<StudentDTO> dtos=studentMapper.getStudentInfoByCondition(query);
	      for(StudentDTO dto:dtos) {
	    	  System.out.println(dto.toString());
	      }
	      session.commit(true);
	      session.close();	
	}
	
	public static void queryStudentInfoManyStep() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      StudentDTO dto=studentMapper.getStudentById(1);
	      System.out.println(dto.toString());
	      session.commit(true);
	      session.close();	
	}
	
	public static void insert() throws IOException {
	      Reader reader = Resources.getResourceAsReader("SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);		
	      SqlSession session = sqlSessionFactory.openSession();
	      StudentMapper studentMapper=session.getMapper(StudentMapper.class);
	      AddressMapper addressMapper=session.getMapper(AddressMapper.class);
	      StudentDTO dto=new StudentDTO();
	      dto.setFirstName("david");
	      dto.setLastName("www");
	      dto.setAge(25);	      
	      dto.setContent(new String(readToByte("D:\\document\\wangwei\\README.md"), "UTF-8"));
          dto.setImage(readToByte("D:\\document\\wangwei\\春暖花开.jpg"));
          studentMapper.insertStudentInfo(dto);         
          Integer studentId=dto.getId();
          
          List<Address> ads=new ArrayList<>();
          Address home=new Address();
          home.setAddressType(1);
          home.setStudentId(studentId);
          home.setDetail("china shanghai");
          
          Address company=new Address();
          company.setAddressType(1);
          company.setStudentId(studentId);
          company.setDetail("china beijing");
          
          ads.add(home);
          ads.add(company);
          
          for(Address a:ads) {
        	  addressMapper.insertAddressInfo(a);
          }
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
	public static String readToString(String fileName) {  
        String encoding = "UTF-8";  
        File file = new File(fileName);  
        Long filelength = file.length();  
        byte[] filecontent = new byte[filelength.intValue()];  
        try {  
            FileInputStream in = new FileInputStream(file);  
            in.read(filecontent);  
            in.close();  
        } catch (FileNotFoundException e) {  
            e.printStackTrace();  
        } catch (IOException e) {  
            e.printStackTrace();  
        }  
        try {  
            return new String(filecontent, encoding);  
        } catch (UnsupportedEncodingException e) {  
            System.err.println("The OS does not support " + encoding);  
            e.printStackTrace();  
            return null;  
        }  
    }  
}
