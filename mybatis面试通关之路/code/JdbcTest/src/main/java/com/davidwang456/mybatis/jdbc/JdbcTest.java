package com.davidwang456.mybatis.jdbc;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;

public class JdbcTest {
    // MySQL 8.0 以下版本 - JDBC 驱动名及数据库 URL
    //static final String JDBC_DRIVER = "com.mysql.jdbc.Driver";  
   // static final String DB_URL = "jdbc:mysql://localhost:3306/davidwang456";
 
    // MySQL 8.0 以上版本 - JDBC 驱动名及数据库 URL
    static final String JDBC_DRIVER = "com.mysql.cj.jdbc.Driver";  
    static final String DB_URL = "jdbc:mysql://localhost:3306/davidwang456?characterEncoding=UTF-8&useSSL=false&useLegacyDatetimeCode=false&serverTimezone=UTC";
 
 
    // 数据库的用户名与密码，需要根据自己的设置
    static final String USER = "root";
    static final String PASS = "wangwei456";

    public static void main(String[] args) {
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
            
            //3 定义操作的SQL语句,实例化PreparedStatement对象            
            sql = "SELECT id, first_name, last_name,age FROM student where id = ?";
            System.out.println(" 实例化PreparedStatement对象...");
            preparedStatement = conn.prepareStatement(sql);
            preparedStatement.setInt(1, 5);

            
            //4 执行数据库操作
            rs = preparedStatement.executeQuery();
        
            //5 获取并操作结果集
            while(rs.next()){
                // 通过字段检索
                int id  = rs.getInt("id");
                String first_name = rs.getString("first_name");
                String last_name = rs.getString("last_name");
                int age=rs.getInt("age");
    
                //输出数据
                System.out.println("[ID: " + id+",first_name:"+first_name+",last_name:"+last_name+",age:"+age+"]");
            }
            //6 完成后关闭
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
