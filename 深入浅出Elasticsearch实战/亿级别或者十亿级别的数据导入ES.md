# 背景

在使用关系型数据库或者文档型数据库时，有时需要进行模糊查询，对于记录数比较少的数据表，模糊查询不会耗费太多时间，但对于记录数比较大的数据表来说，模糊查询就是个灾难。这种情况下，Elasticsearch(简称ES)就派上用场了。ES适用的场景：

- 记录数比较大，需要大量的后台计算，使用关系型数据库会影响主流程的执行；
- 大体量数据的关系型数据库分库分表，这就导致很多场景没有办法或者很难实现，如：分页、排序、分组查询。

当传统数据库面对这种大体量数据查询而感到无力的时候，可以使用 ES 来处理这种业务。

# 亿级别或者十亿级别的数据导入ES实例

想要使用ES，首先要将数据导入到ES中。增量数据的导入一般使用canal这样的工具，通过读取binlog来插入到ES，这方面资料比较齐全，可以从官方网站获取到文档。全量数据一般比较大，导入困难，通常需要写代码实现。一条一条记录的插入往往太慢，往往需要批量处理。本文演示方面，将数据从mongodb导入到ES，采用分页的形式插入，完整的代码实现如下所示：(仅供参考)

```
import java.io.IOException;
import java.net.UnknownHostException;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.apache.commons.codec.binary.Base64;
import org.apache.http.HttpHost;
import org.bson.types.ObjectId;
import org.elasticsearch.action.bulk.BulkItemResponse;
import org.elasticsearch.action.bulk.BulkRequest;
import org.elasticsearch.action.bulk.BulkResponse;
import org.elasticsearch.action.index.IndexRequest;
import org.elasticsearch.client.RestClient;
import org.elasticsearch.client.RestHighLevelClient;
import org.elasticsearch.common.xcontent.XContentType;

import com.mongodb.BasicDBObject;
import com.mongodb.DB;
import com.mongodb.DBCollection;
import com.mongodb.DBCursor;
import com.mongodb.DBObject;
import com.mongodb.MongoClient;
import com.mongodb.MongoException;


public class Test {

    public static void main(String[] args) throws IOException {
        int pageSize=10000;

        try {
            MongoClient mongo = new MongoClient("localhost", 27017);

            /**** Get database ****/
            // if database doesn't exists, MongoDB will create it for you
            DB db = mongo.getDB("www");

            /**** Get collection / table from 'testdb' ****/
            // if collection doesn't exists, MongoDB will create it for you
            DBCollection table = db.getCollection("person");

             RestHighLevelClient client = new RestHighLevelClient(
                        RestClient.builder(
                                new HttpHost("localhost", 9200, "http")));
            DBCursor dbObjects;            
            Long cnt=table.count();
            System.out.println(table.getStats().toString());
            Long page=getPageSize(cnt,pageSize);
            ObjectId lastIdObject=null;    
            Long start=System.currentTimeMillis();
            long ss=start;
            for(Long i=0L;i<page;i++) {
                start=System.currentTimeMillis();
                dbObjects=getCursorForCollection(table, lastIdObject, pageSize);
                System.out.println("第"+(i+1)+"次查询，耗时:"+(System.currentTimeMillis()-start)+" 毫秒");
                List<DBObject> objs=dbObjects.toArray();
                start=System.currentTimeMillis();
                batchInsertToEsSync(client,objs,"person","doc");
                lastIdObject=(ObjectId) objs.get(objs.size()-1).get("_id");
                System.out.println("第"+(i+1)+"次插入，耗时:"+(System.currentTimeMillis()-start)+" 毫秒");                
            }            
            System.out.println("耗时:"+(System.currentTimeMillis()-ss)/1000+"秒");    
        } catch (UnknownHostException e) {
            e.printStackTrace();
        } catch (MongoException e) {
            e.printStackTrace();
        }


    }

    public static void batchInsertToEsSync(RestHighLevelClient client,List<DBObject> objs,String tableName,String type) throws IOException {
        BulkRequest bulkRequest=new BulkRequest();
        for(DBObject obj:objs) {
            IndexRequest req = new IndexRequest(tableName, type);            
            Map<String,Object> map=new HashMap<>();
            for(String key:obj.keySet()) {
                if("_id".equalsIgnoreCase(key)) {
                    map.put("id", obj.get(key));
                }else {
                    String valStr="";
                    Object val=obj.get(key);
                    if(val!=null) {
                        valStr=Base64.encodeBase64String(val.toString().getBytes());
                    }
                    map.put(key, valStr);
                }
            }
            req.id(map.get("id").toString());
            req.source(map, XContentType.JSON);
            bulkRequest.add(req);
        }   
        BulkResponse bulkResponse=client.bulk(bulkRequest);
        for (BulkItemResponse bulkItemResponse : bulkResponse) {
            if (bulkItemResponse.isFailed()) { 
                System.out.println(bulkItemResponse.getId()+","+bulkItemResponse.getFailureMessage());
            }
        }
    }

    public static DBCursor getCursorForCollection(DBCollection collection,ObjectId lastIdObject,int pageSize) {
        DBCursor dbObjects=null;
        if(lastIdObject==null) {
            lastIdObject=(ObjectId) collection.findOne().get("_id");
        }
        BasicDBObject query=new BasicDBObject();
        query.append("_id",new BasicDBObject("$gt",lastIdObject));
        BasicDBObject sort=new BasicDBObject();
        sort.append("_id",1);
        dbObjects=collection.find(query).limit(pageSize).sort(sort);
        return dbObjects;
    }

    public static Long getPageSize(Long cnt,int pageSize) {
        return cnt%pageSize==0?cnt/pageSize:cnt/pageSize+1;
    }
```

```
nt client = new RestHighLevelClient(
                        RestClient.builder(
                                new HttpHost("localhost", 9200, "http")));
            DBCursor dbObjects;            
            Long cnt=table.count();
            System.out.println(table.getStats().toString());
            Long page=getPageSize(cnt,pageSize);
            ObjectId lastIdObject=null;    
            Long start=System.currentTimeMillis();
            long ss=start;
            for(Long i=0L;i<page;i++) {
                start=System.currentTimeMillis();
                dbObjects=getCursorForCollection(table, lastIdObject, pageSize);
                System.out.println("第"+(i+1)+"次查询，耗时:"+(System.currentTimeMillis()-start)+" 毫秒");
                List<DBObject> objs=dbObjects.toArray();
                start=System.currentTimeMillis();
                batchInsertToEsSync(client,objs,"person","doc");
                lastIdObject=(ObjectId) objs.get(objs.size()-1).get("_id");
                System.out.println("第"+(i+1)+"次插入，耗时:"+(System.currentTimeMillis()-start)+" 毫秒");                
            }            
            System.out.println("耗时:"+(System.currentTimeMillis()-ss)/1000+"秒");    
        } catch (UnknownHostException e) {
            e.printStackTrace();
        } catch (MongoException e) {
            e.printStackTrace();
        }


    }

    public static void batchInsertToEsSync(RestHighLevelClient client,List<DBObject> objs,String tableName,String type) throws IOException {
        BulkRequest bulkRequest=new BulkRequest();
        for(DBObject obj:objs) {
            IndexRequest req = new IndexRequest(tableName, type);            
            Map<String,Object> map=new HashMap<>();
            for(String key:obj.keySet()) {
                if("_id".equalsIgnoreCase(key)) {
                    map.put("id", obj.get(key));
                }else {
                    String valStr="";
                    Object val=obj.get(key);
                    if(val!=null) {
                        valStr=Base64.encodeBase64String(val.toString().getBytes());
                    }
                    map.put(key, valStr);
                }
            }
            req.id(map.get("id").toString());
            req.source(map, XContentType.JSON);
            bulkRequest.add(req);
        }   
        BulkResponse bulkResponse=client.bulk(bulkRequest);
        for (BulkItemResponse bulkItemResponse : bulkResponse) {
            if (bulkItemResponse.isFailed()) { 
                System.out.println(bulkItemResponse.getId()+","+bulkItemResponse.getFailureMessage());
            }
        }
    }

    public static DBCursor getCursorForCollection(DBCollection collection,ObjectId lastIdObject,int pageSize) {
        DBCursor dbObjects=null;
        if(lastIdObject==null) {
            lastIdObject=(ObjectId) collection.findOne().get("_id");
        }
        BasicDBObject query=new BasicDBObject();
        query.append("_id",new BasicDBObject("$gt",lastIdObject));
        BasicDBObject sort=new BasicDBObject();
        sort.append("_id",1);
        dbObjects=collection.find(query).limit(pageSize).sort(sort);
        return dbObjects;
    }

    public static Long getPageSize(Long cnt,int pageSize) {
        return cnt%pageSize==0?cnt/pageSize:cnt/pageSize+1;
    }
```

# 总结

对一些影响到主流程的大表查询或者分库分表查询的后台任务，将之从关系型数据库或者文档型数据库导入到ES，进行分组查询等是一个通用的方式。增量插入的方法一般有两种方式：1.双写 2.使用诸如canal这样的工具，利用binlog(mysql)来插入。全量的导入一般需要写脚本实现。为效率考虑，一般是批量插入。
