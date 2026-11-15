## Why

图像车道自 2026-11 起整条 down：组 9 `image-video` 只挂了一个上游账号
（acct 40 `词元-openai-next-seedance`），被 openai-next 按 browser signature
站点级封禁（`consecutive_403=3/3`，最后一条错误停在 2026-11-11 04:59Z），
组 9 `fallback_group_id` 为空 ⇒ **一个账号被封＝整条车道死**。

更糟的是它不会自愈：车道闸先按 `lane.health=down` 拒绝，请求根本打不到上游，
`last_verified` 就永远停在 11-11——状态与实况之间没有任何东西会去对账。

而同一时刻，词元里**已有另一条活的图像上游**：acct 57 `fed-workbuddy-media`
（组 19，`grok-imagine-image`，`/v1/images/generations`，2026-11-14 12:55Z
实测 1 张 2K、$0.02 成功）。VAAS 用不了它的原因只有一个：`_ciyuan_image.py`
只认 `data[].b64_json`，遇到只回 URL 的上游直接按契约失败。

同时暴露一个结构问题：lane 的 `provider` / `base_url` / `api_key` / `model`
全部来自 vault 记录（`credentials.py:_resolve_entry`），换上游是**改数据不是改代码**——
这一点目前没有在规格里说清，于是每次换车道都有人以为要出片重编镜像。

## What Changes

- **图像车道接受两种产物形态**：`data[].b64_json`（现状）与 `data[].url`（新增：
  下载后落盘）。URL 形态 MUST 只接受 http/https、设超时与体积上限、按 `Content-Type`
  与魔数校验是图像；MUST NOT 从 HTML 里猜 URL（既有纪律不变）。
- **把「换上游＝改凭证记录」写成规格**：同一 kind 的备用上游通过改 vault 记录
  （`provider`/`base_url`/`api_key`/`model`）切换，MUST NOT 需要重出镜像；
  镜像内唯一需要跟着变的是 `lane_health.json`。
- **复验从手工配方变成工具**：`scripts/probe_image_lane.py` 把「这条车道还封着吗」判成
  三态（`restored` / `still-blocked` / `cannot-judge`），且 **4xx 一律不算封禁**
  （key/模型/参数问题指向我们自己的配置，不指向上游）；这样 `lane_health.json` 的
  陈旧判定有了一条低成本对账路径，`down` 不会因为没人复验而永久僵住。
- **grok 第三 provider 档（2026-11-15 扩展）**：词元组 19 现已实测可服务图像与视频
  （acct 57 联邦入口；图像真产物落盘 $0.02/2K，视频真 MP4）。`provider=grok` 新增为
  显式档位：图像委托 ciyuan 驱动（端点形状相同、只换凭据），视频走新驱动
  `_grok_video.py`（`/v1/videos` 协议族，四函数接口与 ciyuan 视频驱动同签名，
  两阶段防重复计费语义保留）。时长夹紧 [3,15]s 并披露；台账按凭据分组记账，
  组 9 与组 19 互不遮蔽。
- 文档订正：`docs/debt-ledger.md` 的 DEBT-LANE-9-01 补记「组 9 单账号＝单点」这一
  结构成因，以及组 19 这条可用替代车道。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `ciyuan-lane-providers`: 图像车道放宽为接受 `b64_json` 或 `url` 两种产物形态；
  「词元分组口径与分路凭证」补上备用上游的切换口径（改凭证记录、免出片）、
  provider 显式档位与「台账按凭据分组记账」要求；新增「grok 视频车道
  （/v1/videos 协议族）」requirement（四函数接口、相对 URL 鉴权取件、
  MP4 魔数、时长夹紧披露、未实调不进台账）。

## Impact

- 代码：`scripts/_ciyuan_image.py`（产物形态解析）、`scripts/_grok_video.py`（新驱动）、
  `scripts/litellm-bridge.py`（image/video 分支各加 grok 档）、
  `mcp-server/mcp_server/generators/image.py`（仅在需要时透传），以及对应单测。
- 数据：`mcp-server/mcp_server/lane_health.json`（随镜像内嵌，改动需出片/重部署）。
- 外部系统：萬星·词元 `router.finddatatech.cloud`（组 9 现役被封 / 组 19 现役可用）、
  vault `vaas/pool/wanxing/image`（换车道时的唯一写入点）。
- 不在本 change 范围：修 upstream openai-next 的账号（属词元账号侧，只能替换/解封，
  无代码可改）；视频车道 `seedance-ciyuan`（同一被封账号，随账号处置一起恢复）。
