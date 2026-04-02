package com.davidwang456.mybatis.one2many;

import lombok.Data;

@Data
public class Address {
	private Integer id;
	private Integer studentId;
	private Integer addressType;//1:home 2:company 3:classroom
	private String detail;
}
