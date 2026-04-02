package com.davidwang456.mybatis.plus;

import java.util.List;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.ResponseBody;
import org.springframework.web.bind.annotation.RestController;

import com.davidwang456.mybatis.plus.mapper.StudentMapper;

@RestController
public class MybatisPlusControllerTest {
	@Autowired
	private StudentMapper studentMapper;
	
	
	@GetMapping("/studentList")
	@ResponseBody
	public List<Student> getStudents() {
        List<Student> userList = studentMapper.selectList(null);
        userList.forEach(System.out::println);
        return userList;
	}
}
