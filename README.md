# RAG 知识库

一个本地优先、零外部数据库也能运行的 RAG 知识库。默认 SQLite 保存元数据，BM25 提供关键词检索；配置 Qdrant、Redis 和模型 API 后，可升级为企业级混合检索知识库。

## 技术架构

- 后端：FastAPI
- 文档解析：TXT、Markdown、DOCX、PDF、CSV、JSON、LOG
- 中文分词：jieba
- 检索：BM25 + Qdrant 向量检索 + RRF 融合
- 生成：OpenAI 兼容 API，可接 DeepSeek 等模型
- 嵌入：OpenAI 兼容 API，可接 BGE-M3 等模型
- 重排：可选 Rerank API
- 存储：SQLite 元数据 + Qdrant 向量
- 缓存：Redis + BloomFilter + 熔断降级，Redis 未配置时使用进程内缓存
- 权限：用户 + 管理员/编辑者/查看者三级权限
- Prompt：YAML 文件版本化管理
- 安全：Guard 输入输出校验、文件类型和大小限制

## 快速开始

```powershell
cd D:\桌面\RAG知识库
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python start.py
```

启动后访问 http://127.0.0.1:8000

默认管理员账号：

- 用户名：korce
- 密码：change-me

生产环境请务必修改 `RAG_ADMIN_PASSWORD` 和 `RAG_JWT_SECRET`。

## 三级权限

- `admin`：管理用户、知识库、知识库授权、文档和问答
- `editor`：创建知识库，导入和删除文档，使用问答
- `viewer`：只能查看和提问被授权的知识库

管理员可以通过 API 创建用户并分配知识库权限。

## 向量库迁移

旧版使用 `data/vectors/*.npz` 保存本地向量。现在主存储为 Qdrant。

配置 `RAG_QDRANT_URL` 后，可迁移历史向量：

```powershell
python scripts/migrate_vectors_to_qdrant.py
```

未配置 Qdrant 时，向量检索自动降级为纯关键词检索，不影响系统启动。

## Prompt 版本

Prompt 文件位于 `prompts/`，默认使用 `latest` 版本。可通过 `RAG_PROMPT_VERSION` 指定版本号，例如 `RAG_PROMPT_VERSION=1.0.0`。

## 文档粒度权限

文档支持 `public` 和 `restricted` 两种访问模式。受限文档需要单独授权用户，才能查看、检索或管理。

## RAG-Agent 自检与重试

生成回答后会自动检查引用、是否拒绝基于资料作答、回答是否为空；不通过时自动追加修正指令并重试。

## SQL 安全与 AST 求值

提供 SQL 白名单校验和受限 AST 表达式求值，禁止危险 SQL 和任意代码执行。

## Docker 部署

```powershell
docker compose up -d
```

## 评测与压测

```powershell
python scripts/evaluate_rag.py --kb-id <kb_id> --input eval.jsonl
python scripts/load_test.py --kb-id <kb_id> --concurrency 10 --requests 50
```

