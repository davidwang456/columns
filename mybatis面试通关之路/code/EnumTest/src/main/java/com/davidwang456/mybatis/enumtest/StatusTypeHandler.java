package com.davidwang456.mybatis.enumtest;

import java.sql.CallableStatement;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;

import org.apache.ibatis.type.BaseTypeHandler;
import org.apache.ibatis.type.JdbcType;

public class StatusTypeHandler extends BaseTypeHandler<Status> {

	@Override
	public void setNonNullParameter(PreparedStatement ps, int i, Status parameter, JdbcType jdbcType)
			throws SQLException {
		ps.setInt(i, parameter.getCode());
	}

	@Override
	public Status getNullableResult(ResultSet rs, String columnName) throws SQLException {
		Integer code=rs.getInt(columnName);
		return getStatus(code);
	}

	@Override
	public Status getNullableResult(ResultSet rs, int columnIndex) throws SQLException {
		Integer code=rs.getInt(columnIndex);
		return getStatus(code);
	}

	@Override
	public Status getNullableResult(CallableStatement cs, int columnIndex) throws SQLException {
		Integer code=cs.getInt(columnIndex);
		return getStatus(code);
	}
	
	public Status getStatus(Integer code) {
		Status[] ss=Status.values();
		for(Status s:ss) {
			if(s.getCode().equals(code)) {
				return s;
			}
		}
		return null;
	}

}
