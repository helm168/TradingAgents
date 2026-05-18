# TradingAgents 项目 — Agent 工作笔记

## Git 提交约定

### 作者身份

跟随仓库 / 全局 git config，不要在 commit 里硬编码作者；提交时不要传 `--author`，除非用户明确要求改历史或指定身份。当前 local config：

agent 生成 commit message 时不要添加 GitHub 共同作者 trailer，也不要添加 `Signed-off-by` / `Generated-by` / `Authored-by-AI` 等自动归属 trailer。除非用户明确要求，commit author 只保留当前 Git 配置里的作者。

```
user.name  = helm
user.email = sunhao_1988@msn.cn
```

### Commit message 格式

commit message 保持一行描述即可，不写多段正文，除非用户明确要求。
