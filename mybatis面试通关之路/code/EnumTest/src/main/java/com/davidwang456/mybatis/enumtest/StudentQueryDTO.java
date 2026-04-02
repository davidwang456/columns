package com.davidwang456.mybatis.enumtest;

import lombok.Data;

@Data
public class StudentQueryDTO {
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	
	private Status status;
	//关键词查询,依据firstName和lastName
	private String keyword;
}
