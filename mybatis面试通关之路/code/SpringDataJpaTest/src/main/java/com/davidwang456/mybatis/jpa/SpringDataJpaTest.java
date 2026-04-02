/*
 * Copyright 2002-2020 the original author or authors.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package com.davidwang456.mybatis.jpa;

import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import com.davidwang456.mybatis.jpa.config.JPAConfig;
import com.davidwang456.mybatis.jpa.entity.Student;
import com.davidwang456.mybatis.jpa.respository.StudentRepository;

public class SpringDataJpaTest {
	@SuppressWarnings({ "resource" })
	public static void main(String[] args) {
		AnnotationConfigApplicationContext context=new AnnotationConfigApplicationContext(JPAConfig.class);		
		//for(String beanName:context.getBeanDefinitionNames())
			//System.out.println(beanName);		
		StudentRepository studentRepository= (StudentRepository)context.getBean("studentRepository");
		delete(studentRepository,10);
	}
	
	public static void delete(StudentRepository studentRepository,Integer id) { 
		studentRepository.deleteById(id);
	}
	
	public static void update(StudentRepository studentRepository,Integer id) { 
		Student entity=new Student();
		entity.setFirstName("www1");
		entity.setLastName("baidu.com1");
		entity.setAge(13); 
		entity.setId(id);
		studentRepository.save(entity);
		System.out.println("data update success");
	}
	
	public static void select(StudentRepository studentRepository,Integer id) { 
		System.out.println(studentRepository.findById(id));
	}
	
	public static void save(StudentRepository studentRepository) {
		Student entity=new Student();
		entity.setFirstName("www");
		entity.setLastName("baidu.com");
		entity.setAge(23);  
		studentRepository.save(entity);
		System.out.println("data insert success");
	}	
}
