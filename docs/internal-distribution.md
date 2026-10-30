# 内部分发与升级 SOP（管理员手册）

> 原则一句话：**同事永远只从内网拿包；所有第三方项目以"冻结版本"进包；升级节奏由管理员决定。**
> 同事机器上没有任何东西会访问 GitHub。

## 一、整体架构

```
GitHub 上游项目（自己会升级，与你无关）
   │  ① 管理员（有代理）按需拉取
   ▼
管理员本机验收（跑测试 + 试用一次）
   │  ② make_dist 冻结打包（钉死版本）
   ▼
内网分发点（群文件 / 内网 HTTP / 共享盘）
   │  ③ 同事解压双击安装（含 vendor 冻结技能时一并安装）
   ▼
同事机器：nana_image2 upgrade → 读内网清单 → 提示新版去哪下载
```

## 二、日常升级流程（每次发新版五步）

1. **看上游有没有新版**（有代理的机器上）：
   ```bash
   HTTPS_PROXY=http://127.0.0.1:7897 python3 tools/check_upstream.py
   ```
   输出 `UPDATE` 的项目才有动作；建议每季度跑一次，或出问题再升。

2. **拉新版并验收**：clone / pull 对应项目到本地，在你自己机器上试用（技能装进自己的 Codex 试几轮）。**验收不通过就不发**。

3. **冻结打包**：
   ```bash
   # 基础包（不带第三方技能）
   python3 tools/make_dist.py --win-runtime <win.tar.gz> \
       --mac-runtime <arm64.tar.gz> --mac-runtime-x64 <x64.tar.gz>

   # 冻结第三方技能（例如 taste-skill；目录必须是含 SKILL.md 的 git clone，
   # 其远程地址+commit 会自动记录进包内 VENDOR-VERSIONS.txt）
   git clone https://github.com/Leonxlnx/taste-skill /tmp/taste-skill
   python3 tools/make_dist.py ... --vendor taste-skill=/tmp/taste-skill
   ```
   macOS 记得把 dist/nana_image2-macos.zip 按架构拆名为 `-apple-silicon` / `-intel` 两个包（见 make_dist 输出）。

4. **发布**：三个 zip 放到内网分发点（固定路径，方便写清单）；同时把 `tools/manifest.example.json` 抄一份改成新版本号和内网链接，放到同事可访问的 HTTP 位置（或共享盘固定路径）。清单里的 `version` 必须与包内一致。

5. **通知**：群发一句"新版 x.y.z 已就位，解压双击安装即升级"。已装旧版的同事在 Codex 里说"检查 nana_image2 升级"也会看到提示（前提是设置了 `NANA_IMAGE2_MANIFEST` 环境变量指向清单，或让 Codex 用 `--manifest` 调用）。

## 三、各角色职责

| 角色 | 职责 | 需要什么 |
|---|---|---|
| 管理员（你） | 检查上游、验收、打包、发版、维护清单 | 有代理的机器 + GitHub 账号 |
| 同事 | 双击安装/升级；出问题找管理员 | 无（不需要 Python/Git/GitHub） |
| 上游项目 | 自己升级去 | 与同事完全隔离 |

## 四、冻结（vendor）技能的注意点

- vendor 的技能装在同事机器上会是**独立技能**（和 nana_image2 并列），卸载时随包一起清理（安装时写入 `.installed-manifest`）；
- 升级某个 vendor 技能 = 拉它的最新版 → 重新 make_dist → 整包重发（不做单技能热升级，保持"整包=一个版本"的简单模型）；
- 每个 vendor 技能的来源和 commit 记录在包内 `VENDOR-VERSIONS.txt`，出问题可精确追溯；
- **License 合规**：只冻结 MIT/Apache 等宽松协议的项目，保留其 LICENSE 文件（copytree 原样带入）。

## 五、下游工具（Real-ESRGAN / rembg 等）

这些是独立应用/库，不塞进技能包：

- **realesrgan-gui**：下载其 Release 的免安装版放内网软件清单，同事按普通软件使用；
- **rembg**：给设计同事的机器装一次（`pip install rembg`，或后续由 nana_image2 以可选子命令集成）；
- 版本同样记入 `tools/upstream.json` 的 `pinned` 字段统一盯。

## 六、故障排查速查

| 现象 | 处理 |
|---|---|
| 同事 `upgrade` 报 "No upgrade manifest configured" | 没设 `NANA_IMAGE2_MANIFEST`，或没传 `--manifest`；清单地址固定后写进安装通知里 |
| 清单拉不到 | 清单 URL/路径错或内网点没起；`curl` 一下确认 |
| vendor 技能装了没生效 | 重启 Codex；确认 `~/.codex/skills/<name>/SKILL.md` 存在 |
| 卸载残留 | 正常按 `.installed-manifest` 全清；手动删除对应 `~/.codex/skills/<name>` 即可 |
