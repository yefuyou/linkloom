# ES2003 冻结版最终评测

本轮裁判结果尚待人工抽查。标准答案及 v2 均在运行前冻结；未根据本轮结果修改。

运行起点：`80020f11107172bf062a25a22195ca108fe946c8`；冻结提交：`a8dabf40d9a2613781ded1f55747bb6a8e5a81c2`。
gold SHA-256：`fdb653c84c48cfe9041112352614c80fcf0a79e18777ad70d7314150ecaec7b8`。运行前只计算 gold 字节哈希，全部预测保存后才解析 gold 供裁判使用。
模型：gemini-3.8-flash，thinking_level=low，输出上限 32768 token（包含思考和 JSON）；裁判 deepseek-flash。配置和匹配规则沿用冻结脚本。

| 指标 | 基线 | legacy（接线修复后） | v2 |
| --- | ---: | ---: | ---: |
| 待办准确率 | 7/8（87.5%） | 5/8（62.5%） | 5/5（100.0%） |
| 待办召回率 | 7/9（77.8%） | 5/9（55.6%） | 5/9（55.6%） |
| 负责人正确率（匹配任务） | 6/7（85.7%） | 4/5（80.0%） | 4/5（80.0%） |
| 截止时间正确率（匹配且 gold 明确期限） | 0/0（不适用） | 0/0（不适用） | 0/0（不适用） |
| 截止时间端到端正确率（全部明确期限 gold） | 0/1（0.0%） | 0/1（0.0%） | 0/1（0.0%） |
| 讨论误判数 / 全部预测 | 0/8（0.0%） | 1/8（12.5%） | 0/5（0.0%） |

准确率分母为预测数，召回率分母为全部 gold；负责人只在任务匹配后计分。截止时间只在 gold 明确期限的条目计分，同时列出匹配子集和端到端分母；未说明／无法确定单独统计，不算正确或编造。讨论误判行分子为数量。

负责人代词未解析的一律判错；说话人编号按本场 AMI 元数据映射为角色。原文明确指向同一次下次会议的相对时长与事件表述等价，不能据会议技术时间锚推断日历日期。

## AMI 角色映射

来源：官方 AMI ZIP 的 `corpusResources/meetings.xml`，成员 SHA-256 `8ab6cdcf03ed863e839418f31c7380392397a2eed892d73b9bc1b438c7ec520a`。本轮重新读取，与四场预处理转录中的映射逐项核对一致。

| 会议 | A | B | C | D |
| --- | --- | --- | --- | --- |
| ES2003a | User Interface Designer (MEE011) | Project Manager (MEE009) | Marketing Executive (MEE012) | Industrial Designer (MEE010) |
| ES2003b | User Interface Designer (MEE011) | Project Manager (MEE009) | Marketing Executive (MEE012) | Industrial Designer (MEE010) |
| ES2003c | User Interface Designer (MEE011) | Project Manager (MEE009) | Marketing Executive (MEE012) | Industrial Designer (MEE010) |
| ES2003d | User Interface Designer (MEE011) | Project Manager (MEE009) | Marketing Executive (MEE012) | Industrial Designer (MEE010) |

## 基线逐项差异

TP=7，FP=1，FN=2；截止时间弃答 5/8；匹配且有期限 gold 上弃答 0/0。

漏掉的任务：

- ES2003a / ES2003a-c002：Decide on the basic functionality and how the user interface will work, and figure out what the user wants；中文：确定基本功能以及用户界面如何运作，并弄清楚用户想要什么。
- ES2003c / ES2003c-c001：Build a clay prototype, working closely together；中文：制作黏土原型，并紧密合作。

多出的预测：

- ES2003c / p-84eb7bc33948bbcd [UNSUPPORTED]：Look more at the physical make-up of the remote；中文：进一步研究遥控器的物理构造。裁判理由：预测任务“Look more at the physical make-up of the remote”在会议中无明确采纳或分配，且owner为“未说明”，无法对应任何金标任务，属于无依据的预测。

## legacy（接线修复后）逐项差异

TP=5，FP=3，FN=4；截止时间弃答 8/8；匹配且有期限 gold 上弃答 0/0。

漏掉的任务：

- ES2003a / ES2003a-c002：Decide on the basic functionality and how the user interface will work, and figure out what the user wants；中文：确定基本功能以及用户界面的工作方式，并弄清楚用户想要什么。
- ES2003b / ES2003b-c001：Look into the feasibility of a small transmitter and whether an LCD screen requires too much power, based on the design suggested so far；中文：根据目前提出的设计，研究小型发射器的可行性以及LCD屏幕是否耗电过多。
- ES2003b / ES2003b-c002：See what people think about the minimalist approach with a joystick controlling basic functions and mobile-like manipulation；中文：了解人们对这种极简方案（用摇杆控制基本功能、像操作手机一样操作）的看法。
- ES2003c / ES2003c-c002：Look at product evaluation.；中文：研究产品评估。。

多出的预测：

- ES2003a / p-145d7d661374aae6 [WRONG_TASK]：get that out to everybody；中文：把内容发给大家。裁判理由：该预测仅截取“把内容发给大家”这一片段，属于同一项写作任务的下游动作，与金标任务“撰写并发送”相比范围过窄，且与另一预测重复指向同一金标，按一对一规则不匹配。
- ES2003c / p-00fc400f194709a0 [DISCUSSION]：look into the costs of those；中文：调查这些的成本。裁判理由：该句为讨论批量采购成本，属于一般性讨论，未明确采纳为具体任务，故为讨论。
- ES2003c / p-67844f233517f666 [WRONG_TASK]：load it up；中文：加载它。裁判理由：该句为会议中加载演示文稿的操作，属于会议机制，不是实质性未来工作，故为错误任务。

## meeting_v2（冻结版）逐项差异

TP=5，FP=0，FN=4；截止时间弃答 4/5；匹配且有期限 gold 上弃答 0/0。

漏掉的任务：

- ES2003a / ES2003a-c002：Decide on the basic functionality and how the user interface will work, and figure out what the user wants；中文：确定基本功能以及用户界面如何运作，并弄清楚用户想要什么。
- ES2003b / ES2003b-c003：Look into the speaker, LED and transmitter for locating the remote；中文：研究用于定位遥控器的扬声器、LED和发射器。
- ES2003c / ES2003c-c001：Build a clay prototype, working closely together；中文：制作一个黏土原型，并紧密合作。
- ES2003c / ES2003c-m002：Start writing up the project documentation；中文：开始撰写项目文档。

多出的预测：

无。

## 请求、费用与抽查

请求 39/80；按 API 实际 token 用量和沿用费率计算 $0.246469698/$1.50（用量估算，不是账户账单）。失败请求的未知费用保守保留。

| 模型 | 请求 | 输入 token | 输出 token（含思考） | 费用 USD |
| --- | ---: | ---: | ---: | ---: |
| gemini-3.8-flash | 26 | 285989 | 5228 | 0.234096750 |
| deepseek-flash | 13 | 149290 | 8293 | 0.012372948 |

## 首次中止与唯一一次裁判续跑

首次运行在 ES2003a / legacy 裁判处中止：两条预测 p-145d7d661374aae6 和 p-752e663f76945fc4 都被判为 MATCH，且均指向 ES2003a-c001，违反同一 gold 最多匹配一条预测的既定规则。首次结果见 [中止报告](results_es2003_attempt1.md)。
无效响应原文完整保存在 [裁判响应原文](judge_es2003a_legacy_invalid_response.txt)，UTF-8 内容 SHA-256 为 `745e83eedf046d8a432cdce5a22452efaf4eaae01e6be0a0f6b6d48de0dfa115`。原始提供商响应封装仍在被忽略的首次运行目录中。
用户随后明确授权只续跑裁判一次。重判提示仅追加 ES2003a 当前响应的校验错误、冲突的预测 ID/gold ID 和既有一对一规则原文，没有发送其他会议的判断结果。所有预测和已通过校验的 ES2003a 基线裁判响应均复用；没有新增 Gemini 调用。重判及余下判断均通过同一个冻结校验器，未自动修复裁判结论。

| 执行 | 状态 | 请求数 | 费用 USD |
| --- | --- | ---: | ---: |
| 首次运行 | 裁判不合规，中止 | 28 | 0.235667634 |
| 唯一一次裁判续跑 | 完成 | 11 | 0.010802064 |
| 累计 | 完成 | 39 | 0.246469698 |

抽查保证包含 ES2003a / legacy 的实际重判判断，位于 CSV 数据行 [3]（不含表头）。固定随机种子，在该系统样本内置换一个位置满足此条件，总样本量保持各系统向上取整的 20%。

抽查固定种子 20261029，各系统分别向上取整抽 20%，共 7/31 条。包括预测判断与未匹配 gold，附中文翻译、裁判理由及空白人工判断列。

## 执行与边界

全部预测只生成一次；未自动重试或择优选结果。裁判续跑仅限报告中明确披露的授权范围。所有系统的全部预测完成并保存之后，才读取标准答案进行评分。预测输入仅来自转录及原始角色元数据；候选 CSV、AMI 摘要和 gold 未进入被评测系统。baseline 整场读取；legacy 保持 32 KiB 适配器上限和发言接线；v2 使用冻结的可信 Host 192 KiB 上限。

未修改冻结的 v2 源码、提示词、规则、阈值和 gold，原评测脚本也未修改。ES2002 开发集结果不在本轮重跑。完整原始响应、转录和中间结果留在忽略的 work/；仓库只收结果、必要的抽查引用和本轮评测接线。

AMI Meeting Corpus / AMI Project / AMI Consortium；来源 https://groups.inf.ed.ac.uk/ami/corpus/ ，CC BY 4.0：https://creativecommons.org/licenses/by/4.0/ 。

抽查文件：[spot_check_es2003_final.csv](spot_check_es2003_final.csv)。新增评测接线及相关离线测试共 107 项通过；本轮未重跑全量 pytest，冻结版全量测试结果保持在 v2_frozen.json 中。
