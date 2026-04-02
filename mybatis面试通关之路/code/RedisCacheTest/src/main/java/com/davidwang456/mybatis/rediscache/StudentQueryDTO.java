package com.davidwang456.mybatis.rediscache;

import java.util.Date;

import lombok.Data;

@Data
public class StudentQueryDTO {
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	private Date startDate;
	private Date endDate;
	//关键词查:依据firstName和lastName
	private String keyword;
	//排序项目
	private String sort;
	//排序 DESC|ASC
	private String orderBy;
}
