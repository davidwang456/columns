package com.davidwang456.mybatis.hibernate;

import java.util.List;

import javax.persistence.criteria.CriteriaQuery;

import org.hibernate.HibernateException;
import org.hibernate.Session;
import org.hibernate.SessionFactory;
import org.hibernate.Transaction;

import com.davidwang456.mybatis.hibernate.entity.Student;
import com.davidwang456.mybatis.hibernate.util.HibernateUtil;

public class HibernateTest {

	public static void main(String[] args) {
		deleteStudent(9);

	}
	
	   public static void deleteStudent(Integer studentID){
	      SessionFactory sessFact = HibernateUtil.getSessionFactory();
		  Session session = sessFact.getCurrentSession();
	      Transaction tx = null;
	      try{
	         tx = session.beginTransaction();
	         Student Student = 
	                   (Student)session.get(Student.class, studentID); 
	         session.delete(Student); 
	         tx.commit();
	      }catch (HibernateException e) {
	         if (tx!=null) tx.rollback();
	         e.printStackTrace(); 
	      }finally {
	         session.close(); 
	      }
	   }
		
		public static void updateStudent(Integer StudentID, Integer age ){
			SessionFactory sessFact = HibernateUtil.getSessionFactory();
			Session session = sessFact.getCurrentSession();
		      Transaction tx = null;
		      try{
		         tx = session.beginTransaction();
		         Student Student = 
		                    (Student)session.get(Student.class, StudentID); 
		         Student.setAge(age);
		         session.update(Student); 
		         tx.commit();
		         System.out.println("Data updated");
		      }catch (HibernateException e) {
		         if (tx!=null) tx.rollback();
		         e.printStackTrace(); 
		      }finally {
		         session.close(); 
		      }
		   }
		
		
		public static void getAllData() {
			SessionFactory sessFact = HibernateUtil.getSessionFactory();
			Session session = sessFact.getCurrentSession();
			Transaction tr = session.beginTransaction();
			
			CriteriaQuery<Student> cq = session.getCriteriaBuilder().createQuery(Student.class);
			cq.from(Student.class);
			List<Student> StudentList = session.createQuery(cq).getResultList();

			for (Student student : StudentList) {
				System.out.println(student.toString());
			}  
			

			tr.commit();
			System.out.println("Data printed");
			sessFact.close();
		}
		
		public static void saveStudent() {
			SessionFactory sessFact = HibernateUtil.getSessionFactory();
			Session session = sessFact.getCurrentSession();
			Transaction tr = session.beginTransaction();
			Student emp = new Student();
			emp.setFirstName("david");
			emp.setLastName("000000");
			emp.setAge(20);
			session.save(emp);
			tr.commit();
			System.out.println("Successfully inserted");
			sessFact.close();
		}
}
