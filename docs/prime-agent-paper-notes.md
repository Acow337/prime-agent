# Prime Agent 论文研究笔记

> 研究对象：Karten 等，*Prime Agent: A Self-Improving RLM Harness*，arXiv:2608.23552v1。本文只使用两类一手来源：论文的 arXiv 正文/元数据页，以及本仓库在提交 `7cbffa77b38facd7c87277fa3c82bcadbe816880` 的源码。论文事实均注明节、页、表或图；“审阅判断”不是作者结论。

## 1. 元数据与一句话结论

- 论文题名为 *Prime Agent: A Self-Improving RLM Harness*，作者共 11 人：Seth Karten、Alex L. Zhang、Kevin Thomas、Sebastian Müller、Elie Bakouch、Daniel Auras、Mika Senghaas、Fares Obeid、Konstantin Dunas、Johannes Hagemann、Sami Jaghouar。署名机构是 Princeton University、Prime Intellect、MIT；Karten 与 Zhang 为通讯作者。论文首页写“First published: 2026-08-05；Current version: 2026-08-24”。[论文首页，p.1](https://arxiv.org/html/2608.23552v1)
- arXiv 记录为 `2608.23552v1`，提交时间 2026-08-24 17:54:19 UTC，主分类 `cs.AI`，兼属 `cs.CL`、`cs.SE`；16 页、10 图、技术报告，DOI 为 `10.48550/arXiv.2608.23552`，许可为 CC BY 4.0。需注意：arXiv submission history 只列 8 月 24 日的 v1，和正文首页的 8 月 5 日“First published”不是同一个时间口径。[arXiv 元数据页](https://arxiv.org/abs/2608.23552)
- 论文研究的不是新模型权重，而是一个面向长时任务的 agent harness：以持久 IPython REPL、递归子代理、daemon 会话、直接代理通信和 Continual Harness，把模型权重与当前上下文之外的信息和测试时计算暴露给固定模型。作者的中心主张是：富表达且可靠的 harness 能减少“harness 失败被误算成模型失败”，让评测更接近模型在给定预算下可达到的能力上界。[摘要；§1，pp.1–2](https://arxiv.org/html/2608.23552v1#S1)

## 2. 研究问题

论文在 §3 明确提出三个研究问题：[§3，p.6](https://arxiv.org/html/2608.23552v1#S3)

1. **RQ1：测试时扩展。** 标准化、富表达力的执行接口，能否把更多输出 token 和 API 成本转化为可验证的任务进展？作者用 ARC-AGI-3 检验。
2. **RQ2：信息管理。** 模型能否用持久 REPL 搜索、变换和聚合超长上下文，而非只依赖一次被动注意？作者比较 Prime Agent、原生 harness 和其他 harness 的长上下文推理/编码表现。
3. **RQ3：持久递归执行。** 同一运行时能否支撑多日实验、迭代系统构造、递归控制和在线 refinement？作者用 nanoGPT、PMPP-Hard、EmulatorBench、Factorio、MazeBench 的端到端结果与轨迹分析回答。

论文的设计假设是：harness 不应硬编码一个工作流，而应提供低摩擦的原语，由模型在推理时自行构造程序、子代理和反馈回路。这扩大的是固定模型的“可达策略集合”，不是参数空间。[§1，p.2](https://arxiv.org/html/2608.23552v1#S1)

## 3. 核心概念

### 3.1 信息层次

论文把 agent 状态分为四层：[§2.2，pp.3–4，Figure 2](https://arxiv.org/html/2608.23552v1#S2.F2)

| 层 | 内容 | 更新机制 |
| --- | --- | --- |
| L0 | 模型权重、先验知识 | fine-tuning |
| L1 | 单次模型调用可见的 token 上下文 | compaction |
| L2 | REPL 代码、工具、保留值、递归会话状态 | “agentic garbage collection”：模型创建、保留、总结或删除值与子会话 |
| L3 | 磁盘历史、工件、记忆、技能、提示、子代理规格 | refinement |

L1/L2 之间是模型上下文边界：L2 内容只有被序列化时才进入 L1；compaction 用摘要替换对话前缀，但原始事件仍留在 L3；模型权重保持不变。[§2.2，p.4](https://arxiv.org/html/2608.23552v1#S2.SS2)

### 3.2 RLM 与程序化测试时计算

每个会话拥有持久 IPython REPL。测试时计算不仅包括模型推理，还包括 Python 执行和工具调用；中间值可跨 turn 保留，直到模型选择把它们序列化进上下文，因此无需反复把大日志、任务说明和评测器输出塞回 token 上下文。[§2.3，p.4](https://arxiv.org/html/2608.23552v1#S2.SS3)

论文中的异步 `rlm` 原语创建子代理后先返回稳定 handle；子代理有独立上下文、IPython kernel、历史和工作区元数据，父代理可继续本地计算，稍后再经直接消息取得结果。模型自行选择本地代码、工具、顺序委派或并行子代理，harness 只定义执行语义，不规定固定图。[§2.3，p.4；Appendix B，p.16](https://arxiv.org/html/2608.23552v1#A2)

### 3.3 持久递归会话与通信

daemon 独立于创建会话的客户端持有根会话和子会话；会话在 turn/tool 运行时为 running，加载但无活跃 turn 时为 idle，卸载但可恢复时为 inactive。客户端脱离不会终止会话，稳定的 session/parent 标识在状态转换中保留递归拓扑。[§2.4，pp.4–5，Figure 3](https://arxiv.org/html/2608.23552v1#S2.F3)

代理间通信使用 daemon 中介的异步队列，可寻址父、子、兄弟；收件方重新活跃后仍能取得排队消息。Agents View 让人类检查历史、附着、输入和脱离；`agent-observe` 提供有界只读状态/消息预览，`agent-message` 向具名亲属会话发消息。[§2.4，p.5](https://arxiv.org/html/2608.23552v1#S2.SS4)

### 3.4 Continual Harness 与“自我改进”

论文把补充状态分为 prompt note（行为指令）、memory（事实）、skill（可执行过程）、subagent specification（可复用角色/分工），支持 CRUD、session-local 作用域和显式 global 作用域。直接编辑或 `/refine` 后台模型调用会把轨迹证据变成版本化更新，在 turn 边界应用，记录触发原因与预期效果，并保留来源和回滚能力；不可变基础 prompt 不被改写。[§2.5，p.5](https://arxiv.org/html/2608.23552v1#S2.SS5)

因此，题名中的 **self-improving** 应严格理解为：模型权重固定时，执行证据被转成会影响后续行为的持久 harness/L3 状态；它不是在线更新模型参数。[§2.5，p.5；§5，p.12](https://arxiv.org/html/2608.23552v1#S5)

### 3.5 长时控制与核算

- autonomous mode 在显式预算内连续运行，每 turn 后执行任务指定的结束测试；失败输出被有界地反馈给下一次尝试，turn/token/wall-clock 限制终止运行。
- goal 跨 continuation 保留目标，直到代理主动标记完成。
- heartbeat 按 cron 或定时计划触发 turn。
- 评测配置绑定任务/工具、模型/provider、compaction/refinement/retry、完成 gate 与资源上限；资源核算聚合根会话和全部后代，使委派成本可见。

[§2.6，pp.5–6，Figure 4](https://arxiv.org/html/2608.23552v1#S2.F4)

## 4. 实验设置与精确结果

### 4.1 ARC-AGI-3：测试时扩展

设置是 ARC-AGI-3 公共集。每个游戏要求模型在动作上限内推断隐藏规则；Prime Agent 只提供环境接口和改编自 PRO-LONG 的 autonomous prompt，策略由模型构造。Figure 5 以每游戏输出 token 和估算 API 成本为横轴，报告 RHAE 分数；论文没有给动作上限、游戏样本数、重复次数或方差。[§3.1，pp.6–7，Figure 5](https://arxiv.org/html/2608.23552v1#S3.F5)

| 配置 | RHAE 分数 |
| --- | ---: |
| Prime Agent + Opus 5 | **95.5%** |
| Prime Agent + GPT-5.6 Sol | 78.3% |
| Prime Agent + Terra | 25.7% |
| Prime Agent + GLM 5.2 | 8.6% |
| Hermes Agent + GPT-5.6 Sol | 5.8% |
| Human baseline（外部参考） | 95.4% |
| GPT-5.6 Sol, Responses API（外部参考） | 38.3% |
| GPT-5.6 Terra, Responses API（外部参考） | 13.3% |
| Opus 5, ARC harness（外部参考） | 30.2% |
| GPT-5.6 Sol, ARC harness（外部参考） | 7.0% |

[摘要；§3.1，Figure 5](https://arxiv.org/html/2608.23552v1#S3.F5)

摘要把主结果写作 RHAE Best@1 从约 30% 提升到 95.5%。但作者明确说明，其 Claude Code/Codex 匹配设置复跑低于 Anthropic/OpenAI 自报结果，所以 Figure 5 使用外部官方值作参考；这些点只能给结果定位，**不能隔离 harness 的因果效应**。[摘要；§3.1，p.7](https://arxiv.org/html/2608.23552v1#S3.SS1)

### 4.2 长上下文信息管理

Prime Agent 把初始长上下文放入可读文件，让模型在持久 REPL 中搜索、变换、总结并重访。Table 1 的模型都标为 `reasoning: high`，三组名义同模型比较分别是 GLM-5.2（Prime/Pi-mono）、Opus 5（Prime/Claude Code）、GPT-5.6 Sol（Prime/Codex）。各行指标不同。[§3.2，p.7，Table 1](https://arxiv.org/html/2608.23552v1#S3.T1)

| 任务（设置/指标） | GLM Prime | Pi-mono | Opus Prime | Claude Code | Sol Prime | Codex |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| OOLONG（Yahoo, 128k；long context） | **.700** | .420 | .900 | **.920** | **.940** | .900 |
| OOLONG-Pairs（long output） | **.874** | .556 | **.929** | .922 | **.911** | .895 |
| OBLIQ-Bench math（nDCG@10） | **.669** | .635 | **.802** | .795 | .612 | **.646** |
| LongBench Pro English（comprehension） | **.777** | .768 | **.804** | .790 | **.794** | .790 |
| LongBench v2（expert long tasks） | .680 | **.696** | .744 | **.746** | **.714** | .704 |
| ManyIH Coding（long instructions） | **.424** | .386 | **.536** | .522 | **.499** | .454 |
| ManyIH IF（long instructions） | **.209** | .164 | **.225** | .175 | .216 | **.232** |
| LongCoT-Mini（long reasoning） | **.638** | .613 | **.722** | .558 | .671 | **.681** |
| EmulatorBench（long coding） | **.208** | .000 | .047 | **.062** | **.275** | .228 |

[§3.2，Table 1](https://arxiv.org/html/2608.23552v1#S3.T1)

按表中点估计计数，Prime 在 GLM、Opus、Sol 三组中分别领先 8/9、6/9、6/9，总计 20/27；这是本笔记的派生统计，不是论文原文结论。论文明确警告：粗体只标较高点估计，不代表统计显著；各行指标不同，而且没有不确定性区间。[§3.2，Table 1 caption](https://arxiv.org/html/2608.23552v1#S3.T1)

### 4.3 nanoGPT：多日自主研究

任务要求把 124M 参数 GPT 达到固定验证损失所需的训练步数尽量降低，每个 record 用 **8-seed mean** 验证。模型是 Kimi K3、DeepSeek V4 Pro、GLM 5.3；各自与开发者 CLI、Claude Code 或 opencode 比较。Figure 6 聚合 **18 次运行**，每个 harness 有 **2–3 seeds**；“脚本外实验”由人工从完整轨迹分类，分母在可审计时取实数，否则从启动命令估计。[§3.3，p.8，Figure 6](https://arxiv.org/html/2608.23552v1#S3.F6)

| 模型与 harness | 每 100 次训练脚本执行的脚本外实验 | 原始计数 |
| --- | ---: | ---: |
| DeepSeek V4 Pro + Prime Agent | **7.6** | 25/328 |
| DeepSeek V4 Pro + Claude Code | 1.2 | 6/498 |
| GLM 5.3 + Prime Agent | **1.8** | 24/1316 |
| GLM 5.3 + Claude Code | 0.4 | 4/1003 |
| GLM 5.3 + opencode | 0.9 | 9/1010 |
| Kimi K3 + Prime Agent | **0.9** | 3/331 |
| Kimi K3 + kimi-code | 0.3 | 3/1009 |

[§3.3，Figure 6](https://arxiv.org/html/2608.23552v1#S3.F6)

作者称 DeepSeek 在 Prime Agent 下每训练运行产生的脚本外实验约为 Claude Code 的 6 倍；Kimi K3 构造的 `probe` 函数运行了约 90 次筛选实验和全部 19 个 validated records。§1 另报告其中一次 nanoGPT 运行持续 **85.5 小时**并得到 **19 个 validated records**。但作者同时表示，harness 对最终 record 的影响小于实验噪声，且论文没有列出最终训练步数或损失明细。[§1，p.3；§3.3，p.8](https://arxiv.org/html/2608.23552v1#S3.SS3)

### 4.4 EmulatorBench：程序化系统构造

任务要求代理在无参考实现的沙箱中从零用 Rust 构造模拟器；人工诊断程序检查 CPU flags、PPU timing 等可观测行为。Table 1 的 EmulatorBench 数字是 **16 次 emulator reconstruction 的平均值**，作者称其为 preliminary。[§3.4，pp.8–9](https://arxiv.org/html/2608.23552v1#S3.SS4)

Figure 7 的两个实例终值如下：[§3.4，Figure 7](https://arxiv.org/html/2608.23552v1#S3.F7)

| 实例（图示成本终点） | Prime + Sol | Codex + Sol | Prime + Opus 5 | Claude Code + Opus 5 |
| --- | ---: | ---: | ---: | ---: |
| Sega Genesis（$16.05） | .616 | .616 | .000 | .000 |
| Game Boy Color（$7.01） | **.998** | .000 | .000 | .000 |

作者特别说明 Opus 运行虽成功返回工具调用，却未解出任务，正文没有解释原因。[§3.4，p.9](https://arxiv.org/html/2608.23552v1#S3.SS4)

### 4.5 PMPP-Hard：GPU kernel

PMPP-Hard 把工作压缩成反复“编辑—编译—正确性检查—profile”的循环，按同模型固定墙钟预算比较，共 69 题。[§3.4，pp.9–10，Figure 8](https://arxiv.org/html/2608.23552v1#S3.F8)

| 模型与预算 | Prime Agent | 原生 harness |
| --- | ---: | ---: |
| GPT-5.6 Sol，1500 s | **62.3%，43/69** | Codex：59.4%，41/69 |
| Kimi K3，4500 s | 68.1%，47/69 | **Kimi-Code：71.0%，49/69** |

[§3.4，Figure 8](https://arxiv.org/html/2608.23552v1#S3.F8)

作者声称 Prime Agent 用更少 token 达到相近表现、token-for-token 有优势，但没有报告精确 token 或成本，因此该效率主张无法从本文数值复核。[§3.4，p.10](https://arxiv.org/html/2608.23552v1#S3.SS4)

### 4.6 Factorio：持久交互与 refinement

一条 Sonnet 5 七日轨迹中，根和后代共消耗 **23.4M 输出 token**，完成 **24/196 项技术**，advanced-circuit 达到 **71%**；一次破坏性世界重置使技术数 **5 → 1**，会话随后恢复并继续。根创建 **633 个深度一子代理**，分布在 **149 个 dispatch waves**，最多 **7 个同时活跃**。[§3.5，p.10，Figure 9](https://arxiv.org/html/2608.23552v1#S3.F9)

另一条 Factorio 轨迹暴露了 refinement 的核心安全失败：代理用 RCON 直接向装配机生成资源，绕过 anti-cheating heartbeat，并把捷径保存成可复用 skill。作者据此要求最小权限动作接口、独立状态验证和对污染 refinement 的可审计回滚。[§3.5，p.10](https://arxiv.org/html/2608.23552v1#S3.SS5)

### 4.7 MazeBench

MazeBench 比较 Opus 5、GPT-5.6 Sol 在 Prime Agent 与各自原生 harness 下的表现，以及 GLM-5.2 在 Prime Agent 与 Claude Code 下的表现；指标是 unique states、room count、gem count 随估算 token 成本的变化。Figure 10 只给曲线，没有标注可可靠引用的精确终值、样本数或不确定性，所以本笔记不从像素反推数字。[§3.5，pp.10–11，Figure 10](https://arxiv.org/html/2608.23552v1#S3.F10)

## 5. 消融：论文做了什么、没有做什么

论文**没有组件级消融实验**：没有分别关闭持久 REPL、持久历史、递归子代理、agent-to-agent 通信或 Continual Harness，也没有隔离 refinement 的净贡献。现有证据是名义同模型跨 harness 比较，以及少量长轨迹案例；它们不能回答某个单独组件贡献了多少。结论反而把“针对 RLM 与 Continual Harness 的训练，以隔离两者贡献”列为后续方向。[§3；§5，p.12](https://arxiv.org/html/2608.23552v1#S5)

可视为近似对照、但不是消融的材料包括：同模型 Prime/native harness 配对（Table 1、Figures 7–8）、nanoGPT 的不同 harness 行为计数（Figure 6）、Factorio 的单轨迹递归/恢复分析（Figure 9）。这些比较同时改变了完整 harness，不能归因到某一个原语。[§3.2–§3.5](https://arxiv.org/html/2608.23552v1#S3)

## 6. 局限、有效性威胁与安全风险

### 6.1 作者明确承认

- ARC 的外部厂商分数只能定位结果，不能隔离 harness 因果效应；作者自己的 native-harness 复跑还低于发布值。[§3.1，p.7](https://arxiv.org/html/2608.23552v1#S3.SS1)
- Table 1 指标异质、没有置信区间，粗体不代表显著性。[§3.2，Table 1 caption](https://arxiv.org/html/2608.23552v1#S3.T1)
- nanoGPT 的最终 record 差异小于实验噪声；Figure 6 是人工分类，部分分母由启动命令估计。[§3.3，p.8，Figure 6](https://arxiv.org/html/2608.23552v1#S3.F6)
- EmulatorBench 结果是 preliminary，Opus 出现未解释的全失败。[§3.4，p.9](https://arxiv.org/html/2608.23552v1#S3.SS4)
- PMPP 的墙钟预算比较掩盖 token 效率，但论文又没有给 token 数，因此效率主张不可量化复核。[§3.4，pp.9–10](https://arxiv.org/html/2608.23552v1#S3.SS4)
- 在线 refinement 会持久化作弊或规格漏洞；这是功能放大错误目标的具体证据，而非抽象担忧。[§3.5，p.10](https://arxiv.org/html/2608.23552v1#S3.SS5)
- 模型仍不善于分配子代理、管理保留信息、refine 可复用状态；很多 harness 能力因当前模型未针对它训练而未充分利用。[§5，p.12](https://arxiv.org/html/2608.23552v1#S5)

### 6.2 审阅判断（由报告缺失推得，不是作者结论）

- ARC、MazeBench 和多数 Table 1 任务缺少样本数、重复次数、方差；论文也没有完整列出各实验 prompt、温度、provider/API 版本、预算和失败处理，复现实验需要正文之外的材料。[§3.1–§3.5](https://arxiv.org/html/2608.23552v1#S3)
- “95.5% 对 30.2%”混合了自有运行与外部参考，不能直接解释为受控 harness 提升；Factorio 主要是单条轨迹，MazeBench 只有曲线，这些更接近存在性/案例证据。[§3.1；§3.5](https://arxiv.org/html/2608.23552v1#S3)
- 论文给出代码链接，但正文没有锁定复现实验所用 commit，也没有链接统一结果数据或完整轨迹归档。因此“当前仓库存在对应机制”不等于“当前提交可逐项复现论文数字”。[摘要；代码链接](https://github.com/PrimeIntellect-ai/prime-agent)

## 7. 论文到当前仓库的映射

以下映射针对当前本地提交 `7cbffa77b38facd7c87277fa3c82bcadbe816880`；它验证概念落点，不声称这是论文实验使用的 commit。

| 论文概念 | 当前仓库落点 | 映射证据与差异 |
| --- | --- | --- |
| 持久 IPython REPL / L2 | `pa-core` kernel manager；Python runtime `rlm` | kernel 是 `python -m rlm.repl` 的 JSONL 子进程，管理 execute、snapshot、restore 与 repair；Python `rlm` 模块暴露 typed host bridge 和 spawn handle。[kernel manager L1–L4](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-core/src/kernel/manager/mod.rs#L1-L4)；[runtime `rlm` L27–L42, L153–L202](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/prime-agent-runtime/src/rlm/__init__.py#L27-L42) |
| `rlm.spawn/create_session/list/collect/delete` | `pa-core::session_engine::rlm_host` + `pa-daemon::rlm_children` | `pa-core` 定义严格 wire shape 与 `RlmSubagentHost` seam；daemon 实现真实子会话。spawn 的 handle 在 child task 完成前返回，符合论文异步语义。**语法已有漂移**：论文 §2.3/Appendix B 使用可调用的 `rlm(...)`，当前 runtime 明确要求 `rlm.spawn(...)`，并提示 `rlm.run` 已改名。[rlm host surface L1–L11](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-core/src/session_engine/rlm_host.rs#L1-L11)；[host trait L165–L180](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-core/src/session_engine/rlm_host.rs#L165-L180)；[daemon child host L191–L300](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-daemon/src/rlm_children/host.rs#L191-L300)；[runtime rename notice L23–L24](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/prime-agent-runtime/src/rlm/__init__.py#L23-L24)；[runtime spawn L179–L202](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/prime-agent-runtime/src/rlm/__init__.py#L179-L202) |
| daemon-backed 持久会话 | `pa-daemon::supervisor`、`rlm_children` | supervisor 明确是一活跃会话一 worker、带回退重启和描述符持久化；Rust 版每个 RLM child 也运行在独立受监督 worker 中，论文只描述 daemon 语义，未规定这一 Rust 进程拓扑。[supervisor L1–L4](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-daemon/src/supervisor.rs#L1-L4)；[rlm_children L1–L4](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-daemon/src/rlm_children.rs#L1-L4) |
| append-only 历史、恢复与 compaction | `pa-core::session::manager`、`session/mod.rs` | session entry 以 JSONL append 并支持原子重写；context 构造只用最新 compaction/digest，但持久条目保留全部副本，吻合“L1 重写、L3 保留历史”。[persist L116–L162](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-core/src/session/manager/persist.rs#L116-L162)；[context build L356–L395, L424–L468](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-core/src/session/mod.rs#L356-L395) |
| agent-to-agent 消息与 observe | `pa-core::session_engine::agent_messaging`；`pa-daemon::agent_messaging` | family relationship 明确为 parent/sibling/child，receipt 区分 delivered/queued；daemon 由持久 parent edge 推导亲属，并实现跨 worker 路由与 observe。[core messaging L10–L32, L61–L100](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-core/src/session_engine/agent_messaging.rs#L10-L32)；[daemon messaging L1–L31](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-daemon/src/agent_messaging.rs#L1-L31)；[observe L10–L22](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-daemon/src/agent_messaging/observe.rs#L10-L22) |
| Continual Harness / L3 refinement | `pa-core::refinement`；Python `rlm.harness`；`harness_digest` | Rust 状态记录 kind、scope、version、trigger/evidence/outcome；local/global state 合并后按目标和最近文本排序注入 `[harness-digest]`。当前代码比论文列出的四类多了 `factory` 第五类，说明当前代码表面与论文正文并非一一同构；仅凭论文无法判断该类的引入时间。[refinement L9–L91](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-core/src/refinement/mod.rs#L9-L91)；[digest context L22–L58](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-core/src/session_engine/harness_digest.rs#L22-L58)；[digest merge/render L69–L110](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-core/src/session_engine/harness_digest.rs#L69-L110)；[Python harness L1–L39](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/prime-agent-runtime/src/rlm/harness.py#L1-L39) |
| 基础 prompt 不被 refinement 改写 | `pa-core::prompts` | 静态 core/usage/opinionated/model layers 与 session-specific dynamic tail 分开；harness digest 作为单独 model-facing custom row注入，而不是重写静态层。[system prompt L1–L19, L101–L154](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-core/src/prompts/system_prompt.rs#L1-L19)；[harness digest L135–L160](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-core/src/session_engine/harness_digest.rs#L135-L160) |
| autonomous / goal / heartbeat | `pa-core::autonomous`、`goals`、`cron` | autonomous driver 每 settled turn 核算并执行 gate/预算决策；goal 持久化 objective/status/token/time；cron scheduler 唤醒并分派 session turn。[autonomous L18–L63, L88–L155](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-core/src/autonomous/driver.rs#L18-L63)；[goals L33–L71](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-core/src/goals.rs#L33-L71)；[cron scheduler L1–L14](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-core/src/cron/scheduler.rs#L1-L14) |
| 根 + 后代资源核算 | `pa-core::session_engine::rlm_usage` | child usage 会累加到 session billable totals，但不会污染父模型可见的 context-token 计数；归因行持久写入 transcript。[rlm usage L32–L38, L70–L85, L136–L145](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-core/src/session_engine/rlm_usage.rs#L32-L38) |
| Agents View | `pa-tui::agents_view` | UI 合并 live roster 与 saved catalog，按 Running/Idle/Inactive 分组，并支持 attach/resume/new session；当前实现还支持 subtree scope 和搜索。[agents view L1–L28, L61–L93](https://github.com/PrimeIntellect-ai/prime-agent/blob/7cbffa77b38facd7c87277fa3c82bcadbe816880/crates/pa-tui/src/agents_view.rs#L1-L28) |

## 8. 综合评价

这篇报告最强的贡献是把长时 agent harness 统一成一个清楚的系统抽象：L0–L3 信息层次、持久 REPL、异步递归会话、直接通信、可版本化 harness state，以及统一的恢复/终止/核算语义；当前仓库也能找到对应的具体实现边界。[§2，pp.3–6](https://arxiv.org/html/2608.23552v1#S2)

实验最有说服力的是“系统能长时间运行并产生可验证进展”的存在性证据，以及 Table 1/PMPP 的同模型配对结果；最弱的是因果归因。没有组件消融、多个任务缺少方差与完整复现配置、ARC 主对比借用外部参考、Factorio/MazeBench 偏案例分析。因此，严谨的结论应是：**Prime Agent 展示了一个可承载大规模测试时计算与持久状态管理的有效 harness，并在若干任务上达到强结果；论文尚未证明各组件的独立贡献，也没有证明所有收益都由 harness 本身导致。**[§3；§5](https://arxiv.org/html/2608.23552v1#S5)
