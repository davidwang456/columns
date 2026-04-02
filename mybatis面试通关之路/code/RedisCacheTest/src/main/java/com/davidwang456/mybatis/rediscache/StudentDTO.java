package com.davidwang456.mybatis.rediscache;

import java.io.Serializable;
import java.util.Date;
import lombok.Data;

@Data
public class StudentDTO implements Serializable{
	private static final long serialVersionUID = 1L;
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	private Date createTime;
	private Date updateTime;
	@Override
	   public String toString() {
	    return "student [id=" + id + ", firstName=" + firstName
	    		 + ", lastName=" + lastName + ", age=" +age+ 
	    		 ",创建时间："+DateUtils.getDateString(createTime)+
	    		 ",更新时间："+DateUtils.getDateString(updateTime)+
	    		 ']';
	   }
}
