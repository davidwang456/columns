package com.davidwang456.mybatis.SubSymbol;

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
	//排序列
	private String sort;
	//排序 DESC|ASC
	private String orderBy;
}
