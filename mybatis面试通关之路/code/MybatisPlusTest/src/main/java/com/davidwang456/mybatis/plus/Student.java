package com.davidwang456.mybatis.plus;

import lombok.Data;

@Data
public class Student {
    private Integer id;

    private String firstName;

    private String lastName;

    private Integer age;
	@Override
	   public String toString() {
	    return "student [id=" + id + ", firstName=" + firstName
	    		 + ", lastName=" + lastName + ", age=" +age+ ']';
	   }
}