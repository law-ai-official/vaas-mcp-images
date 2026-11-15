## MODIFIED Requirements

### Requirement: 图像车道生成与模型白名单

图像生成 SHALL 以 OpenAI 图像协议调用词元 `POST /v1/images/generations`，并仅使用实测
返回合规 JSON 产物的模型；返回非 JSON（如 HTML）的模型 MUST NOT 作为默认或推荐值。

产物形态 SHALL 同时接受 `data[].b64_json` 与 `data[].url` 两种：前者直接解码落盘，
后者下载后落盘。**两种形态的校验 MUST 同等级别**：解码或下载所得字节 MUST 通过图像魔数
识别，识别不出 SHALL 失败且 MUST NOT 写盘（避免产出打不开的假产物）。下载另 MUST 只允许
http/https、设连接与读取超时、设体积上限，并按 `Content-Type` 与魔数双重校验。
产物扩展名 MUST 取实际容器类型，MUST NOT 沿用请求的 `output_format`；二者不一致时
SHALL 在响应 `warnings` 中如实披露。

#### Scenario: 图像生成成功落盘

- **WHEN** 调用者请求文生图且凭证为 ciyuan 档
- **THEN** 系统请求 `/v1/images/generations` 并解析响应 `data[]` 的产物
- **AND** `b64_json` 形态直接解码、`url` 形态先下载，二者都按 VAAS 命名约定写入产物目录
- **AND** 响应返回产物相对路径列表，形状与既有图像响应一致

#### Scenario: 图像车道返回非 JSON 应答

- **WHEN** 词元图像车道对某模型返回 `Content-Type: text/html` 或不可解析的响应体
- **THEN** 该次生成失败并明确报错（说明该模型应答形状不合规）
- **AND** MUST NOT 从 HTML 中猜测或提取图片 URL 充当产物

#### Scenario: 默认图像模型可服务

- **WHEN** 使用 ciyuan 档凭证且调用者未指定模型
- **THEN** 系统使用凭证记录声明的模型；未声明时回落到实测可服务的白名单值
- **AND** 已知返回 HTML 或不可路由的模型 MUST NOT 出现在白名单内

#### Scenario: 图像车道返回 url 形态产物

- **WHEN** 上游返回 `data[].url` 而未返回 `b64_json`（只回 URL 的 OpenAI 兼容上游）
- **THEN** 系统下载该 URL 并落盘为图像产物，响应形状与 `b64_json` 形态一致
- **AND** 下载体非图像（HTML/JSON 错误体）、超出体积上限或超时 SHALL 失败并说明原因
- **AND** MUST NOT 把上游 URL 直接当作产物路径返回给调用者

#### Scenario: b64 产物同样要过魔数校验

- **WHEN** 上游在 `data[].b64_json` 里返回的不是图像（例如 HTML 错误页的 base64）
- **THEN** 该次生成失败并指出首字节内容
- **AND** MUST NOT 把该字节写成产物文件（旧行为会写成一个打不开的假图片）

#### Scenario: 契约外容器保留并点名

- **WHEN** 上游返回的容器不在请求契约（`png`/`jpeg`/`webp`）之内（如 tiff/heic/avif）
- **THEN** 产物 SHALL 保留并按其真实容器命名，MUST NOT 因容器不合约而丢弃
  （上游已计费，丢弃等于让客户白付一次）
- **AND** 响应 `warnings` MUST 点名该容器契约外并提示发布前转换

#### Scenario: 扩展名跟实际容器走

- **WHEN** 上游返回的容器与调用者请求的 `output_format` 不一致（如请求 jpeg、实得 PNG）
- **THEN** 产物文件按实际容器命名（`.png`），MUST NOT 命名为 `.jpeg`
- **AND** 响应的 `warnings` 中如实披露该差异

### Requirement: 词元分组口径与分路凭证

系统 SHALL 承认词元「一把密钥只绑一个分组、图像/视频与语音分属不同分组」的约束，
并按生成类型（kind）分路解析凭证；MUST NOT 假设单一 key 可服务全部车道。

同一 kind 的 provider、端点、密钥与模型 SHALL 全部由凭证记录
（`vaas/pool/<line>/<kind>` 或 `vaas/service/default/<kind>`）声明；切换上游
MUST 只改该记录，MUST NOT 需要改动生成代码或重出镜像。镜像内与车道状态相关的唯一
产物是 `lane_health.json`，其变更随镜像生效。

provider 档 SHALL 为显式枚举（`volcengine`/`ciyuan`/`grok`）；未受支持的 provider
MUST 显式拒绝并列出受支持档位，MUST NOT 静默回落。**车道身份跟随凭据分组**：
台账条目按凭据所属分组记账（如 `image.grok`/`video.grok`），MUST NOT 把一条分组
的销账顺手写到另一条分组的条目上（一条分组的封禁与另一条分组的可用互相独立，
混写会互相遮蔽）。

#### Scenario: 语音与图像使用各自分组的凭证

- **WHEN** 同一次部署内分别发起语音合成与图像生成
- **THEN** 两者可分别解析到不同分组的词元 key
- **AND** 各自调用成功，不因分组差异而互相失败

#### Scenario: 缺失某路凭证时仅该路失败

- **WHEN** 某一 kind（如 image）未配置 ciyuan 凭证而其他 kind 已配置
- **THEN** 该 kind 的生成调用失败并指出缺失的凭证路径
- **AND** 其他 kind 的生成不受影响

#### Scenario: 备用上游经凭证记录切换

- **WHEN** 主用上游账号被封禁而同一 kind 存在另一条可用上游（如另一分组的
  `grok-imagine-image`）
- **THEN** 只需改写该 kind 的凭证记录（provider/base_url/api_key/model）即可切到备用上游
- **AND** 该切换 MUST NOT 要求改动生成代码、MUST NOT 要求重出镜像
- **AND** `lane_health.json` 的状态更新 MUST 以切换后的实调端到端证据为准

#### Scenario: grok 图像档与 ciyuan 档共用驱动、分记账本

- **WHEN** 凭证记录声明 `provider: grok` 发起图像生成
- **THEN** 系统经同一图像驱动请求 `/v1/images/generations`（组 19 端点形状与组 9 一致），
  仅凭据与模型名不同（缺省 `grok-imagine-image`，`GROK_API_KEY`/`GROK_BASE_URL`
  缺省回落 `CIYUAN_*`——同一网关）
- **AND** 台账销账记在 grok 分组的条目上；组 9 的缺陷台账 MUST NOT 因组 19 可用而销账

#### Scenario: 未受支持的 provider 显式拒绝

- **WHEN** 调用者指定的 image/video provider 不在受支持档位内
- **THEN** 调用失败并列出受支持的 provider 档位
- **AND** MUST NOT 回落到任何其他 provider 静默代做

### Requirement: 词元侧车道缺陷的记录与规避

对已实测确认的词元侧车道缺陷（应答形状不合规、模型不可路由、上游账号封禁），系统 SHALL 以默认模型白名单与显式错误规避之，并在文档中如实标注；MUST NOT 假装这些车道正常可用。缺陷台账 SHALL 同时机器可读：每条车道维护状态（`active`/`degraded`/`down`）、原因类别与最后验证时间，可经发现工具查询，并与部署侧台账一致；状态为 down 的车道 MUST NOT 在任何发现面被标为可用，对其调用 SHALL 返回 `lane_down` 分类错误并附状态与可行动提示。缺陷销账 MUST 以上游修复后的实调端到端证据为准，MUST NOT 仅凭状态翻转收口。

台账条目 SHALL 自带陈旧度：按条目声明的复验周期（缺省 7 天，可用 `stale_after_days` 覆盖）判定
`stale`，并在发现面随状态一并暴露 `stale` 与 `age_days`。**`stale` MUST NOT 被当作新鲜**——
陈旧的 `down` 会继续拒绝一条可能已恢复的车道，陈旧的 `active` 会推荐一条可能已死的车道；
二者都是错，且方向相反。时间戳缺失或不可解析时 `stale` SHALL 为「判不出」（`null`），
MUST NOT 记为新鲜。

#### Scenario: 已知缺陷模型不作为默认

- **WHEN** 配置或文档声明 ciyuan 档的默认模型
- **THEN** 默认模型取自实测可服务的白名单
- **AND** 已知返回 HTML 或不可路由的模型被明确标注为不可用

#### Scenario: 语音车道上游未配置时如实报错

- **WHEN** 调用 ASR 转写且词元语音车道无可用的上游账号
- **THEN** 系统如实上报车道不可用（含车道与引擎标识），不伪装成输入错误

#### Scenario: 上游封禁时车道状态如实可查

- **WHEN** 图像车道因上游账号被封而对请求返回 503
- **THEN** 发现工具中该车道状态为 down（含原因类别与最后验证时间）
- **AND** 租户调用返回 lane_down 分类错误而非上游原始错误体，且提示不重试等待修复

#### Scenario: 修复以实调为凭

- **WHEN** 上游账号封禁解除后运行车道探针
- **THEN** 实调成功（产物落盘、形状合规）方可将状态记为 active
- **AND** 探针证据与时间戳写入台账

#### Scenario: 台账陈旧在发现面可见

- **WHEN** 某车道的 `last_verified` 距今超过其复验周期（缺省 7 天）
- **THEN** 发现工具对该车道的 `lane` 视图带 `stale: true` 与 `age_days`
- **AND** 该标注对 `down` 与 `active` 一视同仁（两种陈旧都需要人复验）
- **AND** 时间戳不可解析时标注为「判不出」（`null`），MUST NOT 记为 `false`

## ADDED Requirements

### Requirement: grok 视频车道（/v1/videos 协议族）

`provider=grok` 的视频生成 SHALL 走独立的 `/v1/videos` 协议族驱动
（`POST /v1/videos` → `GET /v1/videos/<id>` → `GET /v1/videos/<id>/content`），
MUST NOT 复用 ciyuan 档的 `/v1/contents/generations/tasks` 端点——两者形状不同。
驱动 SHALL 提供 `submit/poll/status_once/download` 四函数接口，与 ciyuan 视频驱动
同名同签名，使宿主两阶段防重复计费语义（`--async` 只提交不等待、`--status` 只查态
不提交）原样保留。

驱动 SHALL 编入三个实测上游事实：① 终态按上游真实词表判（`completed` 映射为统一
`succeeded`，兼容 `succeeded`/`done`）；② 取件地址可为相对路径（`/v1/videos/<id>/content`），
下载 SHALL 拼 base_url 并携带 Authorization，绝对签名 URL 免鉴权直取；③ 下载字节
MUST 通过 MP4 容器魔数（`ftyp`）校验，不符 SHALL 失败且 MUST NOT 写盘。

时长 SHALL 夹紧到上游硬边界 [3,15] 秒：越界值夹到边界并在响应 `warnings` 中点名
原值与生效值，MUST NOT 静默改写不披露，MUST NOT 因越界直接拒绝。上游未按请求交付的
参数（如要 480p 实得 720P）SHALL 在 `warnings` 中如实披露。

#### Scenario: grok 视频两阶段提交与查询

- **WHEN** 调用者以 `--async` 发起 grok 视频生成
- **THEN** 系统仅 `POST /v1/videos` 并立即返回任务 id 与上游状态，不内部轮询
- **AND** 后续以 `--status <id>` 查询时仅读状态，MUST NOT 重新提交付费任务

#### Scenario: grok 视频完成取件

- **WHEN** 任务上游状态到达终态 `completed` 且返回取件地址
- **THEN** 驱动把状态归一为 `succeeded` 并解析扁平 `url`（兼容嵌套 `video.url`）
- **AND** 相对路径取件 SHALL 拼 base_url 并带 Authorization；绝对 URL 直取
- **AND** 下载体非 MP4 容器 SHALL 失败且不写盘

#### Scenario: 越界时长夹紧披露

- **WHEN** 调用者请求时长 1 秒（上游硬边界为 [3,15] 秒）
- **THEN** 驱动以 3 秒提交任务
- **AND** 响应 `warnings` 点名「请求 1s → 实发 3s」，MUST NOT 静默改写

#### Scenario: grok 视频车道未实调前不进台账

- **WHEN** grok 视频车道尚未取得端到端实调证据（提交→轮询→取件真产物）
- **THEN** 台账 MUST NOT 预先记录该车道为 active
- **AND** 未记录的车道按 degraded 放行（车道闸不拦未知车道），但这不构成销账证据

