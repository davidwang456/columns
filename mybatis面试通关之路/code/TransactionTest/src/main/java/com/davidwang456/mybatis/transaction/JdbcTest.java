package com.davidwang456.mybatis.transaction;

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
        PreparedStatement preparedStatement2 = null;
        String sql="";
        String sql2="";
        try{
            //1 注册 JDBC 驱动
            Class.forName(JDBC_DRIVER);
        
            //2 打开链接
            System.out.println("连接数据库...");
            conn = DriverManager.getConnection(DB_URL,USER,PASS);
            
            
            //3 定义操作的SQL语句,实例化PreparedStatement对象    
            //开始事务
            conn.setAutoCommit(false);
            try{
                sql = "update student set age=age+1 where id = ?";
                
                System.out.println(" 实例化PreparedStatement对象...");
                preparedStatement = conn.prepareStatement(sql);
                preparedStatement.setInt(1, 1);
                //4 执行数据库操作
                preparedStatement.executeUpdate();

                sql2 = "update student set age=age-1 where id = ?";
                preparedStatement2 = conn.prepareStatement(sql2);
                preparedStatement2.setInt(1, 2);
                //4 执行数据库操作
                preparedStatement2.executeUpdate();
            
                
                conn.commit();//通知Transaction提交.
            }catch(SQLException ex){
                conn.rollback();//通知Transaction回滚.
            }
            //6 完成后关闭
            // 关闭资源
            shutdownResource(conn,preparedStatement,null);
        }catch(SQLException se){
            // 处理 JDBC 错误
            se.printStackTrace();
        }catch(Exception e){
            // 处理 Class.forName 错误
            e.printStackTrace();
        }finally{
        	shutdownResource(conn,preparedStatement,null);
        	shutdownResource(conn,preparedStatement2,null);
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
