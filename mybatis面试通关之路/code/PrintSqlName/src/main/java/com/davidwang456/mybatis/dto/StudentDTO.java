package com.davidwang456.mybatis.dto;

import java.io.Serializable;

import lombok.Data;

@Data
public class StudentDTO implements Serializable{
	private static final long serialVersionUID = 1L;
	//字段
	private Integer id;
	private String first_name;
	private String last_name;
	private Integer age;
	@Override
	   public String toString() {
	    return "student [id=" + id + ", first_name=" + first_name
	    		 + ", last_name=" + last_name + ", age=" +age+ ']';
	   }
}
