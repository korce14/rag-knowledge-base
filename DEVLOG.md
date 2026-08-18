# DEVLOG

本文件记录 RAG 知识库的开发过程、技术决策与迭代记录，便于复盘和简历素材整理。

## 2026-08-13 基础能力

- 搭建 FastAPI 后端、SQLite 元数据存储、上传/解析/分块链路。
- 实现 BM25 中文检索、jieba 分词、Qdrant 向量检索与 RRF 融合。
- 实现用户三级权限、知识库权限、文档权限与文档共享。
- 增加 Guard 输入输出校验、上传大小/类型限制、SQL 白名单与受限 AST 求值。

## 2026-08-14 检索与体验

- 接入 Redis 缓存、BloomFilter、熔断降级。
- 增加流式问答、来源引用、多会话持久化、单条消息删除。
- 增加检索空白分析、用户反馈权重调整、LLM 追问建议与重新生成。
- 增加 Prompt YAML 版本化管理和 pipeline 组件化调度。

## 2026-08-17 企业功能

- Agent 改为 ReAct 多轮循环，支持检索、计算、画图、只读 SQL 工具串联。
- 上传改为后台任务队列，Qdrant 权限过滤下推服务端。
- 新增批量导入 Excel/CSV、文件夹索引、RSS/DB/API 外部数据源定时同步。
- 新增分析报告、问题分组卡片、CSV 导出、LLM 知识库目录与概述生成。
- 新增 API Key 鉴权、用户改密与管理员重置密码。
- BM25 分词索引持久化到 SQLite，检索器重建不再全量重算。
- 新增 Vue3 + Element Plus + TypeScript 管理后台，前端改用组合式函数组织逻辑。
- 工程化：pyproject 安装、pre-commit、render.yaml、Locust 压测脚本。

## 2026-08-18 编码 Agent 与安全

- Generator 支持原生 Function Calling（tools 参数）。
- 新增编码 Agent：Developer 写代码、Tester 读文件写测试并运行，工作流在沙箱内执行。
- 沙箱在每次任务开始时复制项目文件，排除 .env、data、node_modules、日志等敏感内容。
- 新增 Prompt Injection 防护：拦截忽略历史指令、泄露系统提示、jailbreak 等模式。
- 新增危险代码检测：AST 扫描 subprocess/eval/exec/__import__ 等危险调用。
- 新增敏感文件黑名单：禁止读取/写入 .env、密钥、数据库、日志等文件。
- 新增代码安全、沙箱、编码 Agent 工作流、注入防护单元测试。

## 测试状态

- 当前测试：77 passed, 1 skipped。
- 覆盖模块：检索、权限、上传队列、Agent、路由、向量过滤、代码安全、沙箱、编码工作流、企业功能。

## 已知边界

- 编码 Agent 的命令白名单仅允许 python/pytest/npm/node/pip，适合测试场景，不适合任意系统运维。
- 外部数据源定时同步为进程内调度，多实例部署时应使用独立调度器。
- 未配置生成模型时，编码 Agent 需要注入 planner 才能运行完整工作流。
