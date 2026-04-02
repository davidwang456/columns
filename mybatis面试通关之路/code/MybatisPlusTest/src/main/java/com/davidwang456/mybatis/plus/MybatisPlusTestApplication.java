package com.davidwang456.mybatis.plus;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
@MapperScan("com.davidwang456.mybatis.plus.mapper")
public class MybatisPlusTestApplication {
	public static void main(String[] args) {
		SpringApplication.run(MybatisPlusTestApplication.class, args);
	}
}
