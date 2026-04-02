package com.davidwang456.mybatis.dto;

import lombok.Data;

@Data
public class StudentQueryDTO {
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	//关键词查询,依据firstName和lastName
	private String keyword;

	private String orderByItem;
	private String orderBy;
}
