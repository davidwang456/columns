熟悉Freeswitch第一课：freeswitch用户信息

1. 命令：list_users
   
   命令格式：list_users [group <group>][domain &lt;domain>] [user <user>][context &lt;context>]
   
   示例命令

```shell
>list_users group default
userid|context|domain|group|contact|callgroup|effective_caller_id_name|effective_caller_id_number
1000|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1000|1000
1001|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1001|1001
1002|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1002|1002
1003|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1003|1003
1004|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1004|1004
1005|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1005|1005
1006|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1006|1006
1007|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1007|1007
1008|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1008|1008
1009|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1009|1009
1010|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1010|1010
1011|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1011|1011
1012|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1012|1012
1013|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1013|1013
1014|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1014|1014
1015|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1015|1015
1016|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1016|1016
1017|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1017|1017
1018|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1018|1018
1019|default|172.20.10.46|default|error/user_not_registered|techsupport|Extension 1019|1019
```

2. 命令：user_exists
   
   命令格式：user_exists <key> <user> <domain>
   
   示例命令：
   
   ```shell
   > user_exists id 1000 172.20.10.46
   true
   ```

3. 命令：find_user_xml
   
   使用格式: find_user_xml <key> <user> <domain>
   
   示例命令

```xml
> find_user_xml id 1000 172.20.10.46
<user id="1000" domain-name="172.20.10.46">
    <profile-variables></profile-variables>
    <params>
        <param name="dial-string" value="{^^:sip_invite_domain=${dialed_domain}:presence_id=${dialed_user}@${dialed_domain}}${sofia_contact(*/${dialed_user}@${dialed_domain})},${verto_contact(${dialed_user}@${dialed_domain})}"></param>
        <param name="jsonrpc-allowed-methods" value="verto"></param>
        <param name="password" value="1234"></param>
        <param name="vm-password" value="1000"></param>
    </params>
    <variables>
        <variable name="record_stereo" value="true"></variable>
        <variable name="default_gateway" value="example.com"></variable>
        <variable name="default_areacode" value="918"></variable>
        <variable name="transfer_fallback_extension" value="operator"></variable>
        <variable name="toll_allow" value="domestic,international,local"></variable>
        <variable name="accountcode" value="1000"></variable>
        <variable name="user_context" value="default"></variable>
        <variable name="effective_caller_id_name" value="Extension 1000"></variable>
        <variable name="effective_caller_id_number" value="1000"></variable>
        <variable name="outbound_caller_id_name" value="FreeSWITCH"></variable>
        <variable name="outbound_caller_id_number" value="0000000000"></variable>
        <variable name="callgroup" value="techsupport"></variable>
    </variables>
</user>
```
