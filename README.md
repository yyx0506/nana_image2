# nanaiamge

![GPT Image 2.5：Sunburst 精细编辑、Flare 快速生图，支持 auth.json 回退和 uv 启动](assets/nanaiamge-hero-2.5-workshops.png)

> 来源仓库：https://github.com/yyx0506/nanaiamge
> 内置：PSD 图层处理（`psd-layers` / `psd-export` / `psd-compose`，纯标准库）、
> Python 3.9+ 兼容、4K 流式与降级判定、以及给非技术同事用的双平台一键安装/卸载脚本。

一个给 Codex 使用的图片生成 Skill：当内置 `$imagegen` 不可用、缺失或被自定义 provider / sub2api 拦截时，改用当前 Codex provider 直接调用兼容的 Images API。

## 特点

- 自动读取当前 Codex provider 的 `base_url`、`experimental_bearer_token` 和自定义请求头。
- 使用非 OpenAI SDK 的 User-Agent，绕开已知的 sub2api / Cloudflare SDK 请求头拦截。
- 支持图片生成、图片编辑、无费用连通性检查和脱敏 dry run。
- **PSD 图层工作流**：列出图层、导出指定图层为 PNG 喂给生成/编辑、把多张生成图合成为新的分层 PSD（Photoshop / Photopea / Krita / Affinity 均可打开）。
- **4K 双保险**：慢任务（high/xhigh/max 质量或 ≥2MP 显式尺寸）的 `generate` 自动改用流式，规避 Cloudflare 524 超时；`edit` 永不流式（规避 sub2api [#8027](https://github.com/Wei-Shaw/sub2api/issues/8027) 的 502+号池耗尽）。
- **降级明确判定**：显式请求尺寸时校验实际输出，发现被上游静默降级（[#6120](https://github.com/Wei-Shaw/sub2api/issues/6120)/[#3302](https://github.com/Wei-Shaw/sub2api/issues/3302)/[#7119](https://github.com/Wei-Shaw/sub2api/issues/7119)）时输出 `degraded: true` 的 JSON 判定与建议，不再只给一句 warning。
- 仅依赖 Python 标准库，不安装 OpenAI SDK。
- 默认使用 `gpt-image-2.5-sunburst`，尺寸为 `auto`；服务端返回多大就原样保存多大，不在本地超分、缩放、补边或重采样。

## 环境要求

- Codex
- Python 3.9+（3.9/3.10 配置解析自动回退，复杂 TOML 会明确报错），或 uv
- 已配置且支持 Images API 的 Codex custom provider / sub2api

## 安装

### 方式一：一键安装（推荐给非技术同事，无需 git）

解压后双击：

| 平台 | 安装 | 卸载（一键删除） |
| --- | --- | --- |
| Windows | `quick-install-windows.bat` | `quick-uninstall-windows.bat` |
| macOS | `quick-install-mac.command` | `quick-uninstall-mac.command` |

安装只是把 Skill 拷贝到 `~/.codex/skills/nanaiamge/`，**不修改任何 Codex 配置**；卸载就是删掉这个文件夹，机器恢复原样。安装脚本会先跑一遍离线自检。

### 方式二：git clone

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/yyx0506/nanaiamge.git ~/.codex/skills/nanaiamge
```

仓库根目录就是 Skill 根目录，不需要绑定或创建专属 Agent。重启 Codex 或开启一个新任务即可使用。

## 使用

在提示词中指定模型，Codex 会把选择转换为客户端的 `--model` 参数：

```text
$nanaiamge 用 Flare 快速生成一张雨夜霓虹街道的横版图片
```

```text
$nanaiamge 用 Sunburst 编辑 input.png，只把背景换成日落，主体和文字保持不变
```

也可以直接写完整模型名，例如：

```text
$nanaiamge 使用 gpt-image-2.5-sunburst，纯文字生成一张像素风海报，不使用参考图
```

| 提示词意图 | Codex 传入的模型 |
| --- | --- |
| 明确指定 Flare / Sunburst 或完整模型名 | 严格使用指定型号，优先于下面的自动选择 |
| 未指定型号，强调快速、草稿、日常生图 | `gpt-image-2.5-flare` |
| 未指定型号，强调精细编辑、保留主体或文字细节 | `gpt-image-2.5-sunburst` |
| 未指定型号，也没有明确偏好 | `gpt-image-2.5-sunburst`（默认） |

两型号都能生成和编辑。上述路由由 Codex 按 SKILL 指令执行；Python 客户端不会解析 `--prompt` 中的模型名。直接运行命令时必须用 `--model` 选择，不能只在画面描述里写“Flare”。若所选型号不可用，报告错误，不静默换型号。

Codex 当前显式调用 Skill 使用 `$skill-name`；不需要手动执行底层 Python 命令。只有排查 provider 连通性时才需要：

```bash
python3 ~/.codex/skills/nanaiamge/scripts/nanaiamge.py doctor
```

没有合适的 Python 时，可直接使用 uv；脚本已声明 Python 版本，uv 会自动准备解释器（首次可能需要下载），无需安装 SDK：

```bash
uv run ~/.codex/skills/nanaiamge/scripts/nanaiamge.py doctor
uv run ~/.codex/skills/nanaiamge/scripts/nanaiamge.py generate --model gpt-image-2.5-flare --prompt "雨夜霓虹街道" --out output/imagegen/street.png
```

`generate`、`edit`、`doctor` 均通过 `--model` 选择完整模型名，无需额外分型参数：

| 模型 | 适用场景 |
| --- | --- |
| `gpt-image-2.5-sunburst`（本客户端默认） | 精细编辑、强调保留参考图细节 |
| `gpt-image-2.5-flare` | 快速、日常高质量生图；也支持编辑 |

两者都支持 `-2026-09-08` 快照名。`gpt-image-2.5` 不是官方文档列出的模型 ID；显式传入时仅作为代理自定义别名透传并警告，不自动映射或回退。模型列表和出图成功均不能证明代理实际使用的上游分型。

`--quality` 支持 `low/medium/high/xhigh/max/auto`，其中 `xhigh/max` 是 2.5 新增档位；客户端保留 `medium` 默认值，官方默认是 `auto`。两型号 token 单价相同，但耗用 token 可不同，不代表每张图同价。

2.5 默认尺寸为 `auto`；显式尺寸须为 16 的倍数，最长边 ≤3840，宽高比在 1:3～3:1，总像素 655,360～8,294,400；高于 2560×1440 属实验范围。透明背景使用 PNG 或 WebP。

来源：[官方生图指南](https://developers.openai.com/api/docs/guides/image-generation)、[Sunburst](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst)、[Flare](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare)（2026-09-27 核对）。当前 Hero 仅通过文字提示词生成，未传入参考图；请求使用 `model=gpt-image-2.5-sunburst`、`quality=high`，服务端返回 1672×941，原样保存。

## PSD 图层工作流

三个**离线**子命令（不碰 provider、不需要凭证），把分层 PSD 和 AI 生图串起来：

```bash
# 1. 列出图层：名称 / 序号 / 位置 / 可见性 / 是否有像素数据
python3 scripts/nanaiamge.py psd-layers poster.psd

# 2. 导出指定图层为 PNG（喂给 edit 做参考图；--all --outdir DIR 导出全部）
python3 scripts/nanaiamge.py psd-export poster.psd --name 背景 --out output/bg.png

# 3. 生成/编辑（走正常付费链路）
python3 scripts/nanaiamge.py edit --image output/bg.png \
  --model gpt-image-2.5-sunburst --prompt "换成夕阳背景" --out output/bg-new.png

# 4. 合成新的分层 PSD（底层在前；原文件永不被修改）
python3 scripts/nanaiamge.py psd-compose \
  --layer "背景=output/bg-new.png" --layer "产品=output/product.png" --out output/poster-v2.psd
```

支持与边界（超界会明确报错，绝不悄悄猜）：

| 支持 | 不支持 |
| --- | --- |
| 8-bit RGB PSD；raw / RLE / ZIP / ZIP+预测 压缩 | PSB、16/32 位深、CMYK/灰度 |
| 图层记录（名称/位置/可见性/不透明度）+ 位图像素 | 图层蒙版单独导出、调整层/文字层的可编辑往返（导出为渲染位图） |
| 写出新分层 PSD（RLE 通道，PS/Photopea/Krita/Affinity 可开） | 写回原 PSD（compose 永远生成新文件） |

在 Codex 里直接说：“把 poster.psd 的背景层换成夕阳，其他不动” 或 “分层给我出背景/主体/装饰三张图再合成 PSD”，Skill 会按 SKILL.md 的路由执行以上链路。

## 路由与凭证

- 配置路径：`--config PATH` > `$CODEX_HOME/config.toml` > `~/.codex/config.toml`。
- endpoint：`OPENAI_BASE_URL` > 所选 provider 的 `base_url`；必须显式配置，不自动切换到 OpenAI 官方地址。
- API key：`OPENAI_API_KEY` 环境变量 > provider 的 `experimental_bearer_token` > 配置文件同目录 `auth.json` 的顶层 `OPENAI_API_KEY`。
- 仅当前两项缺失时读取 `auth.json`；不读取 OAuth `tokens`。该 API key 会用于上述 endpoint，请确保二者匹配。使用 `--config` 时不会再读取默认目录的认证文件。
- 仅有 `auth.json` 时，可设置 `OPENAI_BASE_URL`，无需创建 `config.toml`。dry run 会标明是否使用了认证文件，但不输出 key。

当前已在 Codex 图片策略选择「启用 Hosted 桥接」的配置下测试通过：

![Codex 图片策略启用 Hosted 桥接](assets/codex-hosted-image-bridge.png)

## 分辨率说明

尺寸默认 `auto`，也可用 `--size 1536x864` 等合法尺寸显式指定。请求尺寸不等于输出承诺；客户端检测实际 PNG 尺寸，遇到差异会提示，并原样保存服务端字节，不进行本地放大、缩放或补边。

## 安全说明

- 凭证只从本机环境变量、Codex 配置或 `auth.json` 读取，不写入仓库或生成文件。
- 客户端不主动打印 bearer token 或完整请求头；dry run 也不包含凭证。
- `doctor` 和 dry run 会显示当前 provider 的 base URL，API 错误会包含上游返回的错误正文。公开日志前请检查其中是否包含私人域名、路径或上游回显的信息。
- 不要提交 `.env`、`config.toml`、API key、cookie 或真实请求日志；仓库自带的 `.gitignore` 会忽略常见本地凭证文件。

## 已知限制

- `doctor` 只能验证路由连通和模型列表，不能保证付费生成一定成功。
- 不自动重试付费请求。
- Sunburst 和 Flare 均支持透明背景；使用 `--background transparent --output-format png`（或 `webp`）。
- 实际支持的模型、尺寸和质量参数取决于上游兼容实现。
- PSD 通道仅覆盖 8-bit RGB 的 AI 工作流子集（见上文边界表）。
- 部分网关会忽略 `stream` 参数直接回 JSON——客户端已做兼容回退；`--stream on` 不保证一定是 SSE。

## License

本项目采用 [MIT License](LICENSE)。来源仓库：[yyx0506/nanaiamge](https://github.com/yyx0506/nanaiamge)。

## 本地验证

```bash
python3 -B -m unittest discover -s tests -p 'test_*.py'
```

测试使用本地 HTTP 服务和虚拟凭证，不调用付费接口；含 PSD/PNG/TOML 回归（往返一致性、全部压缩路径、合成与回滚）。
