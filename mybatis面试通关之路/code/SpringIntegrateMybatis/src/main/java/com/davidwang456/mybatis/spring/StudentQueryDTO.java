package com.davidwang456.mybatis.spring;

import lombok.Data;

@Data
public class StudentQueryDTO {
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	private String sort;
	private String orderBy;	
}
