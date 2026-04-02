package com.davidwang456.mybatis.cache;

import java.io.IOException;
import java.io.Reader;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.List;

import org.apache.ibatis.io.Resources;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;


public class FirstLevelCacheTest {
    // MySQL 8.0 以下版本 - JDBC 驱动名及数据库 URL
    //static final String JDBC_DRIVER = "com.mysql.jdbc.Driver";  
   // static final String DB_URL = "jdbc:mysql://localhost:3306/davidwang456";
 
    // MySQL 8.0 以上版本 - JDBC 驱动名及数据库 URL
    static final String JDBC_DRIVER = "com.mysql.cj.jdbc.Driver";  
    static final String DB_URL = "jdbc:mysql://localhost:3306/davidwang456?characterEncoding=UTF-8&useSSL=false&useLegacyDatetimeCode=false&serverTimezone=UTC";
 
 
    // 数据库的用户名与密码，需要根据自己的设置
    static final String USER = "root";
    static final String PASS = "wangwei456";

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
	
	private static void updateWithoutJdbc() {
	     Connection conn = null;
	        PreparedStatement preparedStatement = null;
	        ResultSet rs=null;
	        String sql="";
	        try{
	            //1 注册 JDBC 驱动
	            Class.forName(JDBC_DRIVER);
	        
	            //2 打开链接
	            System.out.println("连接数据库...");
	            conn = DriverManager.getConnection(DB_URL,USER,PASS);
	            
	            //3 定义操作的SQL语句,实例化PreparedStatement对象,设置入参           
	            sql = "update student set age=? where id = ?";
	            System.out.println(" 实例化PreparedStatement对象...");
	            preparedStatement = conn.prepareStatement(sql);
	            preparedStatement.setInt(1, 30);
	            preparedStatement.setInt(2, 8);
	            //4 执行数据库操作
	            preparedStatement.executeUpdate();
	            //5 完成后关闭
	            // 关闭资源
	            shutdownResource(conn,preparedStatement,rs);
	        }catch(SQLException se){
	            // 处理 JDBC 错误
	            se.printStackTrace();
	        }catch(Exception e){
	            // 处理 Class.forName 错误
	            e.printStackTrace();
	        }finally{
	        	shutdownResource(conn,preparedStatement,rs);
	        }
	    }
	    
	    public static void shutdownResource(Connection conn,Statement stmt,ResultSet rs) {
	        // 关闭资源
	    	try {
	    		if(rs!=null) {
	    			rs.close();
	    		}
	    	}catch(SQLException se1){
	    		//TODO
	    	}
	    	
	        try{
	            if(stmt!=null) stmt.close();
	        }catch(SQLException se2){
	        	//TODO
	        }
	        
	        try{
	            if(conn!=null) conn.close();
	        }catch(SQLException se){
	            //TODO
	        }
	    }
}
