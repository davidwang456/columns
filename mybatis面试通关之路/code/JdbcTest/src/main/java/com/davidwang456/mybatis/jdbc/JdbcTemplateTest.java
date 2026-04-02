package com.davidwang456.mybatis.jdbc;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.Arrays;
import java.util.List;

import javax.sql.DataSource;

import org.springframework.context.ApplicationContext;
import org.springframework.context.support.ClassPathXmlApplicationContext;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;

import com.davidwang456.mybatis.jdbc.dto.StudentDTO;

public class JdbcTemplateTest {

	public static void main(String[] args) {
		queryBatch();
	}
	
	@SuppressWarnings("resource")
	public static void queryBatch() {
        List<Integer> ids = Arrays.asList(5, 6, 8);

        MapSqlParameterSource parameters = new MapSqlParameterSource();
        parameters.addValue("ids", ids);
		ApplicationContext context = new ClassPathXmlApplicationContext("application.xml");
		DataSource dataSource=(DataSource) context.getBean("dataSource");
		NamedParameterJdbcTemplate jdbcTemplate = new NamedParameterJdbcTemplate(dataSource);
		
        List<StudentDTO> list = jdbcTemplate.query("SELECT id,first_name,last_name,age FROM student WHERE id IN (:ids)",
                parameters, new RowMapper<StudentDTO>() {
                    @Override
                    public StudentDTO mapRow(ResultSet resultSet, int i) throws SQLException {  
                    	StudentDTO stu=StudentDTO.create(resultSet.getString("first_name"), resultSet.getString("last_name"), resultSet.getInt("age"));
                    	stu.setId(resultSet.getInt("id"));
                    	return stu;
                    }
                });

        list.stream().forEach(System.out::println);
	}

}
