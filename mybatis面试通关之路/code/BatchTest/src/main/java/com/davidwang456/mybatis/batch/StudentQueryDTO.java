package com.davidwang456.mybatis.batch;

import lombok.Data;

@Data
public class StudentQueryDTO {
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	//关键词查�?,依据firstName和lastName
	private String keyword;
	//排序�?
	private String sort;
	//排序 DESC|ASC
	private String orderBy;
}
