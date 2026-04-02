package com.davidwang456.mybatis.integrate;

import java.util.List;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseBody;

@Controller
public class TestController {
	  @Autowired
	  private StudentMapper studentMapper;
	  
	  
	  @PostMapping("/list")
	  @ResponseBody
	  public List<StudentDTO> list(@RequestBody StudentQueryDTO studentQueryDTO) {
		  return studentMapper.getStudentInfoByCondition(studentQueryDTO);
	  }
}
