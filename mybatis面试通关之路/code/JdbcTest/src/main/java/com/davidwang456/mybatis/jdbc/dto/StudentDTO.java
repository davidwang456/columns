package com.davidwang456.mybatis.jdbc.dto;

import java.io.Serializable;

import lombok.Data;

@Data
public class StudentDTO implements Serializable{
	private static final long serialVersionUID = 1L;
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	@Override
	   public String toString() {
	    return "student [id=" + id + ", firstName=" + firstName
	    		 + ", lastName=" + lastName + ", age=" +age+ ']';
	   }
	public static StudentDTO create(String firstName,String lastName,Integer age) {
		StudentDTO dto=new StudentDTO();
		dto.setAge(age);
		dto.setFirstName(firstName);
		dto.setLastName(lastName);
		return dto;
	}
}
