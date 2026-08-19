"""Resource 产品 API 包（09 §36–§48，P2-E3）。

- security：远程来源安全（09 §40.6，SSRF/私网/凭证防护）；
- storage：临时上传存储与文件检测（09 §40.5，04 §10.14）；
- execution：受控内容执行面（09 §37/§40.7，Fake 适配层）；
- nodes：Node ID 不透明编码与路径安全（09 §42.3，AC⑨）；
- cipher：远程来源应用层加密（09 §47.3）；
- repository：Watch 配置与产品列表数据访问（09 §43.2）；
- watch：Watch 配置 CRUD（09 §43.2）；
- service：ResourceService 全生命周期编排（09 §46）；
- purge：Resource Purge 处理器（09 §45.2 步骤 6）。
"""
