package com.davidwang456.mybatis.paging;

import lombok.Data;

@Data
public class StudentQueryDTO {
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	
	private Integer pageSize=5;
	private Integer page=1;
	
	private Integer start;
	
	private Integer offset;
	//排序列
	private String sort;
	//排序 DESC|ASC
	private String orderBy;
	
	public Integer getStart() {
		return (page-1)*pageSize;
	}
	
	public Integer getOffset() {
		return page*pageSize;
	}
}
