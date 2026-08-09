# M1-01 运行时依赖准入决策

检查日期：2026-08-09。本决策只冻结候选版本、许可证和替换边界，不安装依赖，也不证明组合可运行或已达到生产条件。

## 1. 决策矩阵

| Candidate | Decision | Version | Capability | License |
|---|---|---|---|---|
| `fastapi` | adopted | `0.141.1` | `http_framework` | MIT |
| `litestar` | deferred | `2.24.0` | `http_framework` | MIT |
| `uvicorn` | adopted | `0.52.1` | `asgi_server` | BSD-3-Clause |
| `python-multipart` | adopted | `0.0.32` | `multipart_parser` | Apache-2.0 |
| `psycopg` | deferred | `3.3.4` | `postgres_driver` | LGPL-3.0-only |
| `asyncpg` | adopted | `0.31.0` | `postgres_driver` | Apache-2.0 |
| `alembic` | deferred | `1.19.1` | `migration_runner` | MIT |
| `yoyo-migrations` | rejected | `9.0.0` | `migration_runner` | Apache-2.0 |
| `internal-sql-runner` | adopted | `contract-v1` | `migration_runner` | Apache-2.0 |

`adopted` 在本文中表示“依赖选择获准进入下一实现门禁”，不是已经安装、运行或验收。唯一选择为 FastAPI、Uvicorn base、python-multipart、asyncpg 和 internal runner contract-v1。

Claim boundary (machine-checked): this decision does not install dependencies or prove runtime or production readiness.

## 2. 已确认事实

### HTTP 运行时

- FastAPI `0.141.1` 的 [PyPI metadata](https://pypi.org/pypi/fastapi/0.141.1/json) 声明 Python `>=3.10`、MIT，并依赖 Starlette、Pydantic、typing-extensions、typing-inspection 和 annotated-doc。
- FastAPI 冻结快照包含 Starlette `1.6.0`、Pydantic `2.13.4`、pydantic-core `2.46.4`、typing-extensions `4.16.0`、typing-inspection `0.4.2`、annotated-doc `0.0.4`、annotated-types `0.7.0`、AnyIO `4.12.1`、idna `3.18` 与 sniffio `1.3.1`。其中 AnyIO `4.12.1` 的 metadata 已不再把 sniffio 声明为 mandatory edge；这里保留 sniffio 是保守兼容快照，不得将其误述为该版本的必需依赖。
- Uvicorn `0.52.1` 的 [metadata](https://pypi.org/pypi/uvicorn/0.52.1/json) 声明 Python `>=3.10`、BSD-3-Clause。Python 3.12 base 的 active direct 依赖为 Click 与 h11；typing-extensions 的 `<3.11` marker 不生效。固定 Click `8.2.1`、h11 `0.16.0`，并记录 Click 在 Windows 下的 colorama `0.4.6`（BSD-3-Clause）marker；不采用 `standard` extra。
- python-multipart `0.0.32` 的 [metadata](https://pypi.org/pypi/python-multipart/0.0.32/json) 声明 Python `>=3.10`、Apache-2.0 且没有运行时依赖。
- Litestar `2.24.0` 的 [metadata](https://pypi.org/pypi/litestar/2.24.0/json) 声明 Python `>=3.8,<4.0`、MIT；Python 3.12 下 13 个无 extra direct 依赖为 anyio、click、httpx、litestar-htmx、msgspec、multidict、multipart、polyfactory、pyyaml、rich-click、rich、sniffio 和 typing-extensions。完整传递 lock 未解析，因此保持 deferred。

HTTP adapter 只能把服务端认证结果和 multipart 输入转换为 `ImportRequest`。organization、workspace、client、actor 范围不得来自请求体；项目规则继续属于 `ProjectImportService`。

### PostgreSQL driver

- asyncpg `0.31.0` 的 [release](https://github.com/MagicStack/asyncpg/releases/tag/v0.31.0)、[metadata](https://pypi.org/pypi/asyncpg/0.31.0/json) 和 [LICENSE](https://github.com/MagicStack/asyncpg/blob/v0.31.0/LICENSE) 确认 Python `>=3.9.0`、Apache-2.0；固定 README 声明 PostgreSQL 9.5-18。
- asyncpg 直接实现 PostgreSQL 协议并提供 asyncio pool。Python 3.12 下默认依赖不包含仅限 `<3.11` 的 async-timeout；GSS extra 不进入 P0。
- Psycopg core/C/binary `3.3.4` 与 pool `3.3.1` 均为 LGPL-3.0-only；core metadata 还声明 Windows 必需 marker `tzdata`，本次记录 `tzdata 2025.2`（Apache-2.0）。当前 checker 的 adopted 许可证列表不含 LGPL，因此技术适配更直接也不能采用。
- Psycopg `[c]` 从源码构建并链接系统 libpq/libssl；`[binary]` wheels 自带客户端库且实际 native 版本按 wheel/runner 变化；pure Python 仍需要系统 libpq。选定 wheel 的 OpenSSL、NOTICE 和 SBOM 尚未核验。

选择 asyncpg 迫使现有同步 `ProjectImportService`/`ImportRepository` 真正异步化。禁止用 `run_until_complete` 或隐藏线程把 asyncpg 包装成同步 driver。Async adapter 只负责 pool、事务级 RLS scope、参数化 SQL、row mapping 和 driver error translation。

### Migration tooling

- Alembic `1.19.1` 的 [release](https://github.com/sqlalchemy/alembic/releases/tag/rel_1_19_1)、[metadata](https://pypi.org/pypi/alembic/1.19.1/json) 和 [LICENSE](https://github.com/sqlalchemy/alembic/blob/rel_1_19_1/LICENSE) 确认 Python `>=3.10`、MIT。它要求 SQLAlchemy、Mako 和 typing-extensions；当前快照还包含 MarkupSafe 与常见 CPython 平台的 greenlet。
- 对固定 tag `rel_1_19_1` 的 `alembic/` package scope 搜索未发现 `pg_advisory`；检查路径包括 `alembic/ddl/postgresql.py`、`alembic/runtime/migration.py` 与 `alembic/runtime/environment.py`。这是本次固定源码搜索结果，不等于所有扩展或部署全局不存在 advisory lock；P0 部署锁仍需项目提供。
- Alembic 在 PostgreSQL 下声明 transactional DDL，revision function 与版本表更新可处于同一 migration context；但默认版本表不保存 SQL 文件 checksum，hash 漂移拒绝仍需项目扩展。使用 `autocommit_block` 会提前提交，因此 P0 不允许该路径。
- yoyo `9.0.0` 的官方 universal wheel SHA-256 为 `fc65d3a6d9449c1c54d64ff2ff98e32a27da356057c60e3471010bfb19ede081`，没有 sdist，wheel metadata 没有 Requires-Python。许可证锚点是 wheel 内 `yoyo_migrations-9.0.0.dist-info/LICENSE.txt`；mandatory importlib-metadata `9.0.0` 继续依赖 zipp `3.23.0`。不采用未附可追溯 URL 的 issue 说法。
- 固定 wheel 的 `yoyo/backends/base.py:523-534` 显示 `apply_one` 在复制 backend 上执行 steps，之后才在原 backend 写 log 并另开 transaction 标记 applied；`yoyo/migrations.py:62-72` 显示 hash 输入为 migration ID。这些固定路径证据不满足本项目“migration 与版本记录同一事务”及 SQL 内容 checksum 契约。
- internal-sql-runner 当前只有 contract-v1，没有可执行代码、release 或 release 日期。metadata/repository/license URL 均锚定 baseline `9d5111990ff99eb8f7a97cb98398ef2a18a73781` 的既有 migration、目录与 LICENSE。最低契约是在读取版本状态前取得 transaction-scoped advisory lock、保存 SQL 文件 SHA-256、应用后拒绝 hash 漂移、由 runner 统一拥有事务、migration 与版本记录同事务、forward-only、失败不记已应用，以及禁止按分号拆 SQL。

生产 downgrade 策略为 forward-only。工具提供 down/rollback 不代表破坏性 DDL 可安全反转；发布后回退依赖已演练的备份恢复、旧应用部署或显式审查的 forward fix。

## 3. 合理推断

- 现阶段只有一个手写 PostgreSQL migration，没有 ORM metadata 或 revision 分支，因此 internal contract 比引入 SQLAlchemy migration stack 更窄。
- FastAPI 的薄 adapter 与已冻结 OpenAPI 契约匹配，但优势尚未通过真实 endpoint 验证。
- asyncpg 满足当前许可证门禁且默认依赖少，但其异步改造成本高于 Psycopg；这是许可证治理与工程成本之间的明确取舍。
- 若未来经维护者和法律审查允许 LGPL-3.0-only，Psycopg 可重新进入 adopted 评估，但不能把通用“兼容”说法替代本项目分发审查。

## 4. 未知与后续门禁

- 未安装任何 package，也没有 lock file、adopted artifact hash、SBOM 或 NOTICE 更新；yoyo wheel hash 只是 rejected candidate 的可追溯审查证据。
- FastAPI/Starlette/Pydantic/Uvicorn/python-multipart 的冻结组合尚未运行。
- asyncpg adapter 尚未实现，PostgreSQL 18.4 migration、RLS、并发幂等、TLS、恢复和 pool 行为均未测试。
- internal runner 尚未实现。当前 migration 自带 `BEGIN/COMMIT`，与 transaction-level advisory lock 的所有权仍需在实现前冻结。
- 所选 PostgreSQL driver 能否原样执行当前多语句 SQL 文件尚未验证；若不能，必须改变 migration 文件契约或使用 driver 提供的脚本执行能力，不能自行按分号切割。
- yoyo 的 Python 3.12 行为和 Alembic + SQLAlchemy + asyncpg 组合没有运行证据。
- 许可证判断不是法律意见；实际分发前仍需核对每个 wheel/sdist 的 LICENSE、NOTICE 和 native library。

### 4.1 共同未知的最小实验

以下实验一次只改变一个主要变量。它们是后续实现门禁，不属于本次“依赖准入”已完成证据。

| 假设 | 单一主要变量 | 最小实验 | 成功信号 | 失败信号 | 后续保留数据 |
|---|---|---|---|---|---|
| 冻结 HTTP 组合能实现既有 OpenAPI 合同 | 从依赖中立 contract 切换到固定 FastAPI/Starlette/Pydantic/Uvicorn/python-multipart 组合 | 在隔离环境安装精确版本，只实现一个 import adapter，并重放现有 OpenAPI 正反例 | 所有合同用例通过；授权 scope 仍只能由 server 注入；畸形 multipart 返回冻结错误而不写项目状态 | 版本解析冲突、合同漂移、scope 可由 body 注入，或失败路径产生正式状态 | 精确 artifact hashes、解析后的 dependency lock、命令、响应样本、错误类别和耗时 |
| 真正异步的 asyncpg adapter 能保持现有 service 语义 | repository adapter 从内存同步协议切换为 asyncpg end-to-end async | 仅实现 create/import transaction 路径，重放成功、同 key 重放、冲突和 rollback 用例 | 无 `run_until_complete`/隐藏线程；结果和错误码等价；rollback 后无部分数据库事实 | event-loop 桥接、事务泄漏、错误语义漂移或部分写入 | transaction trace、pool state、数据库行快照、错误映射和测试时序 |
| internal runner 能原子执行当前 migration | migration transport 从“未实现合同”切换为一个 transaction-scoped advisory-lock runner | 在 PostgreSQL 18.4 临时实例执行原始 SQL，模拟并发启动、执行中失败和已应用文件 hash 漂移 | 仅一个 runner 应用；失败不记版本；migration 与版本记录同事务；漂移被拒绝 | 双重应用、失败仍标记 applied、锁跨错事务释放、需要按分号拆 SQL，或漂移未阻止 | PostgreSQL image digest、SQL SHA-256、锁/事务日志、版本表和故障注入结果 |
| 当前数据隔离合同能在真实 PostgreSQL 中 fail closed | session/transaction scope 从缺失或合法值切换为错误 workspace/client/project/actor 值 | 使用独立 owner 与 application roles，逐项重放 SELECT/INSERT/UPDATE/DELETE 和连接池复用 | 缺失或错误 scope 均不可见且不可写；连接归还后 scope 不泄漏 | 任一跨 workspace/client/project 访问、owner 绕过误用于 app，或 pool 复用泄漏 | role grants、policy explain、连接 ID、scope 设置/清理记录和越权测试矩阵 |

真实用户需求、节省时间、复用和付费意愿不由上述工程实验验证；它们继续使用 `market-validation-playbook.md` 中的现场任务、重复使用和付款/订金证据。

## 5. Checker hardening RED 记录

在修改 checker 前，将当时的 checker、audit 与 report 复制到唯一系统临时目录，逐项修改临时 audit/report 后运行 `node scripts/check_m1_01_runtime_dependencies.mjs`。本轮 RED 结果（原 checker 均错误返回 exit `0`）：FastAPI boundary 替换为允许请求体注入 tenant scope、`verifiedAt=2099-01-01`、`verifiedAt=2026-02-31`、报告追加一条与准入边界冲突的英文断言、`distributionConditions=[{}]`、Uvicorn `optional=[]`、旧 `generatedAt=2026-01-01T00:00:00+08:00`。这些输入说明仅检查长度、格式或数组存在性不足以保护冻结决策。

修复后 GREEN 结果：同一临时副本逐项重放上述七类攻击，全部返回 exit `1`；正常审计返回 `Runtime dependency admission passed: 9 frozen candidates, 5 unique capability selections, and 67 weakening mutations.`。新增内建变异覆盖 body scope、同步桥接、advisory lock、SQL checksum、同事务记录、禁止按分号拆 SQL、非法日历日期、未来核验日期、对象分发条件、缺失 optional/excluded extra、过期 generatedAt 与报告相反 claim。变异数量由运行时数组动态计算，不是硬编码输出。

P2 RED：修复前，三条分别针对 FastAPI 的英文安装/ready 表述、selected runtime stack 的英文 ready 表述、FastAPI 的中文安装/生产使用表述的报告追加语句，均错误返回 exit `0`。P2 GREEN：修复后同三条语句均返回 exit `1`；明确否定 FastAPI 安装或生产状态的英文和中文句子仍返回 exit `0`，避免把文档的已知限制误判为相反 claim。该 P2 结果仅是 implementer 自检，不替代独立审查。

P2 形状 RED：修复前，将 Psycopg 的 `transitiveSnapshot` 改为 `null` 或将 yoyo 的同字段改为对象，虽返回 exit `1`，但均暴露未处理的 `TypeError` stack trace；FastAPI `alternatives=[{}]` 则错误返回 exit `0`。P2 形状 GREEN：前两类输入均以受控的“必须为 array”错误返回 exit `1` 且无 stack trace；alternatives 对象和文本漂移均返回 exit `1`。alternatives 现在要求非空字符串，并与当前候选的冻结列表精确一致。该结果同样只是 implementer 自检，不替代独立审查。

P2 语义 RED：精确句式修复后，`already installed`、`ready for production use`、中文“已经安装并可用于生产”仍可绕过；外层“do not claim / 不声称”会被误杀，`not installed but production-ready` 的转折正向状态也可能漏报。P2 语义 GREEN：报告守卫按句号和 `but` / `however` / “但” / “但是” / “却”分句，在受控候选或 runtime stack 主体下逐个判断状态谓词及其局部否定，并为并列的否定谓词保留否定作用域。五类同义或转折正向声明进入 weakening mutation；六类直接否定、外层否定和 `not yet` 句进入允许基线。该守卫只保护本报告的冻结 claim boundary，不是通用自然语言事实分类器；本段仍是主线程自检，需独立 reviewer 重放。

P2 分词 RED：候选版本号中的句点会切断主体继承，中文“不仅”会被裸“不”误当否定，旧的泛化正则还会先于分句守卫误杀 `dependencies` / “依赖”的外层否定。P2 分词 GREEN：数字后的句点不再作为句界，裸“不”排除“不仅”，泛化主体统一由分句守卫处理。带版本号和“不仅……而且……”的两类正向声明进入 weakening mutation；中英文泛化外层否定进入允许基线。自由正文仍可能存在未列举的语言边缘，本门禁的高置信事实来源继续是冻结矩阵、canonical 字段与固定 claim boundary，而不是尝试证明任意自然语言都可分类。

P2 继承 RED：Markdown/plain URL 的域名点仍会切断主体，分号转折还会错误清空省略的主体。P2 继承 GREEN：句点仅在后接空白或行尾时作为句界，URL 与版本号保留在同一 clause；分号继续切分局部状态但不清空主体，只有真正句末标点清空继承。两类 URL 与中英文分号转折共四条正向声明进入 weakening mutation。该结果仍需独立 reviewer 重放。

本次合同冻结时间为 `2026-08-09T00:00:00+08:00`；许可证核验和维护检查日期冻结为 `2026-08-09`，生成时间不得早于冻结时间。该自检不构成独立审查或 M1-01 完成批准。

因此 Task 3 Step 5 可以作为依赖决策完成，但 Task 4 和 `M1-01` 必须保持开放。
