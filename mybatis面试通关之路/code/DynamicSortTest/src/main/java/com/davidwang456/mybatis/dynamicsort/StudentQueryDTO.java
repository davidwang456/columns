package com.davidwang456.mybatis.dynamicsort;

import lombok.Data;

@Data
public class StudentQueryDTO {
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	private String keyword;
	//排序列
	private String sort;
	//排序 DESC|ASC
	private String orderBy;	
}
