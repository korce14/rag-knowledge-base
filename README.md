# RAG 知识库

一个面向企业场景的 RAG 知识库，默认 SQLite 保存元数据，BM25 提供关键词检索；配置 Qdrant、Redis 和模型 API 后可升级为混合检索知识库。主界面负责问答与文档管理，`/admin` 提供 Vue3 + Element Plus + TypeScript 管理后台。

## 功能总览

- 文档解析：TXT、Markdown、DOCX、PDF、CSV、JSON、LOG、Excel/CSV 批量导入
- 中文分词：jieba
- 检索：持久化 BM25 + Qdrant 向量检索 + RRF 融合，检索器重建直接读取 SQLite 分词索引
- 生成：OpenAI 兼容 API，可接 DeepSeek 等模型
- 嵌入：OpenAI 兼容 API，可接 BGE-M3 等模型
- 重排：可选 Rerank API
- 存储：SQLite 元数据 + Qdrant 向量
- 缓存：Redis + BloomFilter + 熔断降级，Redis 未配置时使用进程内缓存
- 权限：用户 + 管理员/编辑者/查看者三级权限，文档粒度权限与共享
- Prompt：YAML 文件版本化管理
- 安全：Guard 输入输出校验、文件类型和大小限制、SQL 白名单、受限 AST 求值
- Agent：ReAct 多轮工具循环，支持检索、计算、画图、只读 SQL 串联
- 企业功能：文件夹索引、RSS/SQLite/API 外部数据源定时同步、分析卡片与报告导出、LLM 知识库目录与概述生成、API Key 鉴权、密码修改/重置
- 工程化：pyproject 安装、pre-commit 规范检查、pytest 测试、Locust 压测、Render 云部署配置

## 快速开始

```powershell
cd rag-knowledge-base
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
python start.py
```

启动后访问：

- 主界面：http://127.0.0.1:8000
- 管理后台：http://127.0.0.1:8000/admin

也可以双击 `启动知识库.bat`，脚本会自动启动 Docker Desktop、Qdrant、Redis、知识库服务并打开浏览器。

默认管理员账号仅用于本地开发：

- 用户名：korce
- 密码：change-me

生产环境请务必修改 `RAG_ADMIN_PASSWORD` 和 `RAG_JWT_SECRET`，并通过管理后台创建自己的 API Key。

## 三级权限

- `admin`：管理用户、知识库、知识库授权、文档和问答
- `editor`：创建知识库，导入和删除文档，使用问答
- `viewer`：只能查看和提问被授权的知识库

## 向量库迁移

旧版使用 `data/vectors/*.npz` 保存本地向量，现在主存储为 Qdrant。配置 `RAG_QDRANT_URL` 后可迁移历史向量：

```powershell
python scripts/migrate_vectors_to_qdrant.py
```

未配置 Qdrant 时，向量检索自动降级为纯关键词检索，不影响系统启动。

## Prompt 版本

Prompt 文件位于 `prompts/`，默认使用 `latest` 版本。可通过 `RAG_PROMPT_VERSION` 指定版本号，例如 `RAG_PROMPT_VERSION=1.0.0`。

## RAG-Agent

Agent 使用 ReAct 循环，最多迭代 5 轮。每一轮由 LLM 根据“问题 + 已有工具结果”决定下一步，支持：

- `retrieve`：检索知识库
- `calculate`：受限 AST 数学求值
- `sql_query`：只读 SQL 白名单查询
- `plot_chart`：matplotlib 图表生成

## Docker 部署

```powershell
docker compose up -d
```

## Render 部署

仓库根目录包含 `render.yaml`，可一键部署到 Render，并挂载持久化磁盘 `/var/lib/rag-data`。

## 测试与压测

```powershell
python -m pytest
python scripts/evaluate_rag.py --kb-id <kb_id> --input eval.jsonl
locust -f scripts/locustfile.py --host http://127.0.0.1:8000
```

## 隐私说明

以下内容不会上传到仓库：

- `.env`：API Key、JWT 密钥、管理员账号密码
- `data/`：SQLite 数据库、上传文档、向量文件、缓存和日志
- `*.log`：服务日志
- `frontend-admin/node_modules/`：前端依赖

请使用 `.env.example` 作为模板，在本地配置真实密钥。
