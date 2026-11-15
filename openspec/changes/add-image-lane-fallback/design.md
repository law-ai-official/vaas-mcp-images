## Context

图像车道只有一条凭证路径（`vaas/pool/wanxing/image`），而它的上游唯一账号被封。
现场已有一条可用替代上游（组 19 `fed-workbuddy-media` / `grok-imagine-image`），
唯一挡路的是 `_ciyuan_image.py` 只认 `data[].b64_json`。

## Decisions

### D1 两种产物形态，`b64_json` 优先

`data[]` 单项按 `b64_json → url → 失败` 顺序解析。`b64_json` 仍是首选：它不引入
二次网络请求，也不引入「这个 URL 指向谁」的判断面。顺序固定，不做按大小/尺寸的智能选择。

### D2 URL 下载的安全包封

URL 形态是**新增的网络面**，因此下载路径必须自带约束，不能靠上游善意：

- scheme 只许 `http` / `https`（拒 `file:`/`data:`/其余）；
- 连接与读取超时（默认 60s，可用 `CIYUAN_IMAGE_URL_TIMEOUT_S` 覆盖）；
- 体积上限（默认 24 MiB，可用 `CIYUAN_IMAGE_MAX_URL_BYTES` 覆盖），边读边计，
  超限立即中断而不是读完再判；
- `Content-Type` 必须以 `image/` 开头 **且** 字节头命中图像魔数（JPEG/PNG/WEBP）——
  两条都要，因为上游既可能把 HTML 标成 image，也可能把 image 标成 octet-stream；
- 失败信息只带 URL 的主机名，不带查询串（签名下载 URL 的凭据常在 query 里）。

### D3 不在代码里做「自动切车道」

上游失败时**不**回落到另一条凭证。理由：车道切换会改变计费主体与产物归属口径，
静默切换会让「这条产出来自哪条车道」无法回溯，也与本仓既有纪律（缺陷如实暴露、
以实调销账）冲突。切换是凭证记录的运维动作，由 D4 支撑。

### D4 换上游 = 改凭证记录

`provider` / `base_url` / `api_key` / `model` 全在 vault 记录里（`credentials.resolve`），
所以切换上游不需要改代码、不需要重出镜像；镜像内唯一与车道状态有关的是
`lane_health.json`。这条写进规格，避免下次又有人以为要出片。

### D6 契约外容器：保留产物 + 点名披露

`IMAGE_FORMATS`（png/jpeg/webp）是**请求**契约。真实容器可能落在它外面（tiff/heic/avif）。
两条路都不好：丢弃 = 上游已经计费而客户拿不到东西（就是 11-06 那种「空计费」形态的翻版）；
谎报扩展名 = 下游按扩展名选上传类型，必错。

取第三条：产物**保留并如实命名**，同时在 `warnings` 里点名「契约外 + 请先转换」。
这样钱没白花、真相在场，代价是调用者要读 warnings——比静默错标或静默丢件都轻。

### D5 台账只认实调

`lane_health.json` 的 `down` 会阻断上游探测（车道闸先拒），`last_verified` 不会自己前进。
因此翻 `active` 必须有一次「产物落盘 + 形状合规」的实调证据；仅改状态视为未销账。

### D7 grok 是第三条 provider 档，不是别名

组 19 的图像端点形状与 ciyuan 档完全一致（`POST /v1/images/generations`），
但**视频端点是另一个协议族**：`POST /v1/videos` → `GET /v1/videos/<id>` →
`GET /v1/videos/<id>/content`，与 ciyuan 档的 `/v1/contents/generations/tasks` 形状不同。
因此：

- **图像**：`provider=grok` 委托 `_ciyuan_image.generate`（同一驱动），只换凭据来源
  （`GROK_API_KEY`/`GROK_BASE_URL`，缺省回落 `CIYUAN_*`——同一个网关）。
- **视频**：新驱动 `_grok_video.py`，四函数接口（submit/poll/status_once/download）
  与 `_ciyuan_video.py` 完全同名同签名——宿主两阶段防重复计费语义
  （`--async` 只提交、`--status` 只查态）原样保留，不改分派层骨架。

三个实测上游事实写死在驱动里（2026-11-14/15 真调）：

1. **终态词表**：上游回 `completed`（不是 ciyuan 的 `succeeded`，也不是 xAI 文档的
   `done`）；驱动把 `completed`/`succeeded`/`done` 都映射为统一 `succeeded`，
   `url` 扁平在场（兼容嵌套 `video.url`）。
2. **取件 URL 是相对路径**（`/v1/videos/<id>/content`）：下载必须拼 `base_url`
   **并带 Authorization**——ciyuan 档「绝对签名 URL 免鉴权」的假设在这里不成立。
   绝对 URL（COS 签名那种）仍免鉴权直取。
3. **产物魔数**：MP4 容器（`ftyp`）校验通过才写盘，与图像车道「契约外容器」纪律一致——
   不写打不开的假产物。

台账条目叫 `video.grok`/`image.grok`，**不**写进 `video.ciyuan`——车道身份跟着
凭据组走，混写会让「组 9 封禁」与「组 19 可用」互相遮蔽。
`video.grok` 在实调销账前**不进台账**（未知车道 → degraded 放行），与 D5 同理。

### D8 时长夹紧披露，不静默改写

上游视频时长硬边界 [3,15] 秒（给 1 报「时长必须在 3 到 15 秒之间」），而本地计费侧
允许 1 秒——两处口径不一致，且这不是我们能改的。驱动把越界时长**夹紧到边界**并在
返回的 `warnings` 里点名（原值 → 生效值）。不静默改写（客户不知道自己付了几秒的钱）、
也不硬拒（夹紧后产物仍有用）。上游无视请求分辨率（要 480p 给 720P）同理披露。

## Risks / Trade-offs

- URL 下载引入 SSRF 面（上游可让服务端去取任意地址）。当前上游是我们自己的词元网关
  与受信分组，风险有限；若将来接入第三方，需再加私网/回环地址拒绝（`ip.is_global`）。
  本 change 不做，先记录。
- 24 MiB 上限对 4K 图足够，但对视频帧导出类产物可能偏小；届时按产物类型分档。
