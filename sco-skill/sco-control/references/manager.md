# 多实例管理台的已验证行为

- CCI由Workspace和应用name定位，UID用于识别替换和DNAT绑定。本地key隔离日志，不代替平台name。
- `sco cci apps describe` 的应用创建/更新时间不等于本轮容器启动时间。查询应用实例列表，取RUNNING主容器的 `last_started_time`，无此字段时取 `life_span`（秒）。INIT容器不参与续杯计时。
- 周期续杯到期停止，确认停止后等待5秒直接启动，不查询GPU余量。定时停止时段优先。
- 操作失败暂停自动重试并持久记录；恢复自动运行需明确人工操作。恢复清除暂停但不重置本轮计时，超期周期立即重新判断。
- 管理台恢复接口：`POST /api/instances/<key>/actions/resume`，JSON请求体 `{}`。它返回操作ID，执行结果读取该实例operations接口；不要用直接改state.json代替恢复。
- 普通stop/start保留DNAT。镜像替换产生新UID后需要重新绑定。
- CCI的DNAT绑定字段为 `properties.internal_instance_type: CCI_DEPLOYMENT_SERVICE`，目标为应用UID。旧版EIP CLI输出可能遗漏此字段，不能照抄旧字段 `instance_type` 或猜成 `CCI`。
- 只读查询成功不代表启动权限、个人配额或可调度资源充分。平台拒绝后记录错误，不循环提交。
- 仓库实际配置由用户指定可提交；访问密钥仍保存在本机SCO Profile。跨环境部署先阅读项目README和AGENTS.md，不覆盖用户配置。
