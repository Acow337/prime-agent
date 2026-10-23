# DeepSeek 小预算基线烟雾实验设计

## 目标

用官方 DeepSeek API 和当前 Prime Agent，在不修改产品代码的前提下运行三个小型、可自动验证的任务，确认模型接入、基础任务正确率、工具链和费用记录是否可用。实验是后续 Harness 消融研究的流程预演，不估计 `R/S/M` 组件效应，也不形成论文结论。

## 固定条件

- provider：`deepseek`；模型：`deepseek-v4-flash`；reasoning：`off`；
- 每个任务使用新的临时工作目录、agent directory、session directory 和 daemon socket；
- 使用 headless JSON 输出，不启用 autonomous continuation；
- 禁止读取当前仓库的研究材料作为答案来源；
- 三个任务按顺序运行，前一任务的会话、文件和上下文不传给后一任务；
- API 密钥只从 macOS Keychain 读入子进程环境，不进入仓库、命令参数、结果文件或日志。

## 任务

### T1：结构化计算

在 prompt 中提供一张小型事件表，要求完成筛选、分组求和和时间窗口判断，并按固定 JSON schema 输出四个答案。答案由独立脚本计算；exact match 为成功，字段级正确率为连续得分。

### T2：日志检索与关联

在临时目录提供约 10 KB 的合成日志，其中包含干扰记录、重复 ID 和相互矛盾的状态。要求读取文件、关联三条证据并输出固定 JSON。生成器保存真值；exact match 和字段级得分由 verifier 计算。

### T3：小型代码修复

在临时目录生成一个最小 Python 包和公开测试，其中解析器在转义字符与空字段组合上存在一个缺陷。要求修改实现并运行公开测试。隐藏测试覆盖目标缺陷和两个相邻边界；全量通过为成功，通过比例为连续得分。

## 预算保护

实验总预算为人民币 10 元，并以人民币 8 元兑 1 美元作为保守换算。运行入口使用隔离的模型覆盖，将单次响应上限固定为 1024 tokens。一个仅监听 loopback 的临时代理最多向 DeepSeek 转发六个模型请求；达到上限后拒绝后续请求。

按仓库当前 catalog 中 `deepseek-v4-flash` 的每百万 input/output tokens 价格 0.15/0.60 美元和 1,000,000-token context 上限计算，即使六次请求都达到完整上下文，理论上界仍低于人民币 7.3 元。正式请求前必须重新读取实际模型元数据；若模型、价格或 token 上限无法确认，或保守上界达到 10 元，则不发送 API 请求。

每个任务结束后记录 provider usage 并重新计算累计费用。出现以下任一条件即停止：

- usage 缺失、无法解析或价格未知；
- 代理请求数达到六次；
- 已结算费用加下一请求的保守上界达到人民币 10 元；
- 单任务超过三分钟；
- provider 返回认证、余额或限流错误。

超时终止只作为最后保护；预算安全主要由响应 token 上限、请求数上限和请求前的保守成本检查保证。

## 记录与验证

每个任务记录：任务 ID、Git commit、模型、开始/结束时间、延迟、模型请求数、input/output/cache tokens、美元费用、保守人民币费用、verifier 输出、退出状态和错误分类。不得保存 Authorization header、API key、原始环境变量或包含密钥的诊断信息。

去敏后的 manifest、任务 fixture、verifier、汇总 JSON 和简短 Markdown 报告可以提交到 `experiments/harness-ablation/smoke/`。原始 provider 流、完整会话和临时工作目录不提交。

## 验收标准

- 三个任务中至少两个完成有效计费并产生 verifier 结果；
- 累计实际费用与保守费用均低于人民币 10 元；
- 每个结果都能追溯到固定任务 fixture、模型和 commit；
- 密钥扫描确认实验文件中不存在 DeepSeek key；
- 报告明确标注这是单模型、小样本、无 treatment 对照的烟雾实验。

若少于两个任务产生有效结果，实验只报告接入失败及费用，不补跑到突破请求数或金额上限。
