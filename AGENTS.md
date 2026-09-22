# AI 接手说明

本项目是单进程多实例CCI管理台。先读README和 `config/*.yaml`；保留当前部署配置，不擅自重置用户设置。

- `sco-skill/sco-control/` 是供AI安装的技能目录，安装方式见README。它是项目组成部分，不是无用副本。
- 服务入口为 `app.py`；实例执行在 `controller/instance.py`，时间策略在 `controller/policy.py`，GPU监控在 `controller/service.py`。二者独立，禁止将GPU余量作为续杯启动条件。
- 周期到期停止，确认停止后等待5秒启动；定时停止时段优先。运行时间取平台主容器时间，不能用应用创建时间替代。
- 操作失败必须暂停自动重试；“恢复自动运行”清除暂停但不重置计时，超期则重新进入续杯。
- 每实例只允许一个操作，结果以平台状态确认。不同实例状态和日志隔离。
- 实际配置按用户要求随仓库提交，并保持中文注释。凭据、日志、runtime和缓存不提交。
- 修改配置序列化时使用 `controller.config.write_yaml`，以保留生成的中文字段说明。
- 日常验证：`python -m unittest discover -s tests -v`、`node --check static/console.js`。测试不能启停或删除真实CCI。
- 本机已安装launchd标签为 `com.oneday.sco-cci-manager`；公共模板标签为 `com.sco.cci-manager`。重载服务前检查是否存在执行中的操作。
- 保留用户通过网页修改的周期、时间、镜像和开关；不得根据README示例重置。
