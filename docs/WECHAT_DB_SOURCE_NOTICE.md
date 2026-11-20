# 微信数据库最小读取层的来源

`bridges/wechat/keyprobe.py` 的 cfg 特征、布局偏移、XOR 材料提取和页密钥校验参考并改写自：

- 项目：https://github.com/fanyuantaier/wechatauto-replica
- 固定提交：`6e603974bbac3bda93d88bbd6995510ae2547f5c`
- 文件：`wechatauto/db.py`；原模块注明 cfg 提取方法与 ReadWeixinKey-rev 同源。
- 版本元数据：1.2.8.1。未安装或运行上游包及其 WeChatDB 构造器。
- Apache-2.0 许可完整文本随项目保存于 `src/wechat_agent/assets/licenses/wechatauto-replica-LICENSE.txt`。

修改说明（2026-11-02）：仅允许已核验进程/窗口/二进制代际，静态特征与运行期配置锚点必须唯一；先取 DLL `.data` 及已匹配配置指针，绑定 cfg 中的账号并匹配唯一目录。cfg 主密钥派生实测失败，保留失败证据；新增 `wcdb_probe.py` 显式路线，依据静态 WCDB 名称指针，在同一进程私有可读写页中分块寻找配置对象，上限 512 MiB/30 秒/256 个候选，只用已经选定的一个分片页头校验。它会只读扫描该进程部分内存，但不转储、输出或持久保存内存内容，不访问其他微信进程或账号。实际两次分片校验分别约扫描 8 MiB、14 MiB。

删除自动换账号、其他账号密钥尝试、明文密钥缓存、解密文件缓存及日志中的私有数据。错误仅返回固定原因码。`snapshot.py` 与 `reader.py` 为本项目的最小实现：SQLCipher 页及 WAL 校验、内存 SQLite、规范会话查询及复合游标；不导入上游包。快照会在内存中解密整个选定分片（其中可能物理包含其他会话），SQL 查询仅访问文件传输助手及所需发送者映射，不查询/导出其他会话历史。

SQLCipher 页格式依据：[SQLCipher Design](https://www.zetetic.net/sqlcipher/design/)。接收读取还必须处理事务日志及稳定快照，不能仅用页头 HMAC 成功宣布消息读取完成；日志格式依据：[SQLite Database File Format](https://www.sqlite.org/fileformat2.html)。
