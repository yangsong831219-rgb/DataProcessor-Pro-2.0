---
title: 花一小时搭好 Claude Code Harness，AI 写代码质量立竿见影
source_url: https://mp.weixin.qq.com/s/UV5r7lPf6Q7HHbwpUTH17g
date: 2026-05-30 18:45:20
---

---
title: 花一小时搭好 Claude Code Harness，AI 写代码质量立竿见影
author: 克劳德猎手
url: https://mp.weixin.qq.com/s/UV5r7lPf6Q7HHbwpUTH17g
hostname: weixin.qq.com
description: 同样的 Claude Code、同样的模型，为什么别人的产出总比你好？差距不在模型，在\x26quot;AI 层\x26quot;——CLAUDE.md、Hooks、Skills、LSP、MCP、Sub-agents、Plugins 七大组件的配置与实战指南。
sitename: 微信公众平台
---
你装了 Claude Code，切了 Opus 4.7，但同事的产出还是比你高一截。问题大概率不在模型—— |

Claude Code 跑起来之后，实际表现由两样东西决定：**模型本身的能力**，和**包裹在模型外面的 Harness**——CLAUDE.md、Hooks、Skills、LSP、MCP、Sub-agents 这些。模型决定上限，Harness 决定你实际能拿到多少。

说白了，模型是一台发动机，Harness 是底盘、变速箱和导航。发动机再好，装在没有方向盘的车上也跑不起来。今天带你从零搭一套。

| 开工前确认三件事 |

| 先把 CLAUDE.md 写对，回报率最高的 5 分钟 |

CLAUDE.md 是 Claude Code 启动时自动加载的文件，之后**每个对话回合它都在上下文里**。你写的每一个字，AI 每次回复都在为它买单。所以你塞进去的东西必须精炼——只写"AI 需要在你开口之前就知道的事"。

在项目根目录新建`CLAUDE.md`

。记住一个原则：**规则放 CLAUDE.md，工作流放 Skills**。比如"金额单位永远是分"是规则，放这里；"新建 API 路由要跑 8 个步骤"是工作流，放 Skill。

如果你的项目有子包（比如 monorepo 里的`packages/api/`

），也在里面放一个 CLAUDE.md，只写跟这个子包相关的约定。Claude Code 会从你初始化的目录往上走，自动加载沿途所有 CLAUDE.md。

| 配一个 Hook，别每次都从头自我介绍 |

Hooks 是在 Claude Code 生命周期事件上自动触发的脚本。最有用的两类：**SessionStart**——对话开始时自动把 git 状态、当前分支、最近提交日志塞进上下文；**Stop**——对话结束后自动跑审查，检查这次改动有没有违反 CLAUDE.md 里的约定。

如果你每次开 Claude Code 都要先回答"项目长什么样""现在在哪个分支""最近改了啥"——这些就该自动化。在项目根目录建`.claude/settings.json`


SessionStart Hook 脚本本身很简单——拼一段 git status + git log + 当前分支名，打印到 stdout 就行。Claude Code 会把 Hook 的输出直接注入对话开头。

如果你团队的 CLAUDE.md 经常需要更新，Stop Hook 还有一个进阶玩法：用一个 headless Claude 在每次会话结束后审查改动，自动生成 CLAUDE.md 的修改建议——让 Harness 越用越聪明。

| 把重复工作流写成 Skill，用到的时候自己跳出来 |

Skill 的精妙之处在于**延迟加载**：你可以写 50 个 Skill，但每个对话回合只为一个 Skill 的描述付 token 费。Skill 的正文只在描述匹配当前任务时才会被加载进上下文。

记住第 1 步说的那条线——"规则放 CLAUDE.md，工作流放 Skills"。举个实际例子：你们团队每次新建 API 路由都要跑一套固定流程，那就把它写成 Skill。

注意`paths: packages/api/**`

这行——它让这个 Skill 只在你在 api 包目录下工作时才会被激活。路径作用域是 Skill 最被低估的功能，**它让几百人的 monorepo 里每个人只看到跟自己相关的 Skill**。

| 装上 LSP，别让 grep 在十万行代码里大海捞针 |

Claude Code 不用向量数据库、不预建索引。它像人一样导航代码——读文件、grep 搜索、跟踪 import。在 5000 行项目里毫无感觉，到了 10 万行以上，一个简单的`grep handlePayment`

能炸出几百条结果——注释里的、日志里的、测试 fixture 里的、字符串拼接里的。

LSP 解决的就是这个：它理解作用域、类型和导入关系，做的是**符号级搜索，不是字符串匹配**。同一个变量名，LSP 给出的是一处定义和两处引用，而不是几百个字符串匹配。

装法不复杂——你需要两样东西：一个 Claude Code 的 LSP 插件（code intelligence plugin），加上你的语言对应的 language server 二进制。比如 TypeScript 项目装`typescript-language-server`

，Python 项目装`pyright`

。装完后 Claude 的每一次代码搜索都精准一个数量级。

| 拆个 Sub-agent 去探路，主线别被上下文撑爆 |

Sub-agent 是一个**拥有独立上下文窗口的隔离 Claude 实例**。你派它去探路，它在干干净净的上下文里执行，返回结果后退出。你的主会话永远看不到它的对话过程——只收最终答案。

最实用的模式：**探索和编辑拆开**。比如你要重构 billing 模块但不熟悉它——别在主会话里一边 grep 一边读文件一边改代码，上下文很快就爆了。

代码库越大，这个模式越值钱。10 万行以上的 monorepo 不拆 sub-agent，单个会话的上下文一大半都喂给了探索过程，留给正经改代码的空间没多少。


| 配完这 5 步，你的 Claude Code 已经不一样了 Harness 不是一次性工程——每 3 个月检查一次，每次模型大版本发布后再看一眼。 |

坦白讲，搭 Harness 这事最难的其实是"肯不肯花这一个小时"。技术门槛几乎没有，就是写几个文件、跑几条命令。但真配过一次你就知道了——同一个模型，同一个项目，配和不配，产出完全两个档次。

如果你们团队已经在用 Claude Code，下一步该考虑的是 Plugin——把 CLAUDE.md、Hooks、Skills 打包成一个可安装的单元，让团队 40 个人一句命令就全装上，而不是每个人花半小时手动拷贝。

模型会一直变强，这不用你操心。但模型下面那层 Harness——**那是你的，而且它会复利**。