# Runbook — 把 VAAS 图片/视频车道滚到 grok 档（2026-11-16）

> 这份 runbook 是给**有 china-<node-a>（`<render-node-a>`）shell 的人**的。写它的时候
> 本机对该机的 SSH 是 `Permission denied (publickey,password)`，所以「出片」与
> 「滚版」被拆成了两半：**产物已就绪，只差最后一条 compose**。

## 0. 现在处于什么状态（只读核实口径）

| 项 | 值 | 怎么核 |
|---|---|---|
| 源码 | `fd-vaas-skills` main = `274872d` | `git ls-remote origin refs/heads/main` |
| 快照仓 | `law-ai-official/vaas-mcp-images` @ `4ee70c4` | `gh api repos/law-ai-official/vaas-mcp-images/commits/main -q .sha` |
| 镜像 tag | `yizuo/vaas-mcp:sha-4ee70c4` | 见下 |
| 镜像 digest | `sha256:239d6f1b3cf46a04109551d3e82bfabd3ca53b546169c22b04c6518d6f4d1f85` | hkccr 与 ccr **同一个值** |
| 在役 | 仍是没有 grok 档的旧镜像 | `docker inspect -f '{{.Config.Image}}' vaas-wanxing` |

两仓核对（任一台有凭据的机器上）：

```bash
for R in <registry-hk> <registry>; do
  skopeo inspect --tls-verify=false "docker://$R/yizuo/vaas-mcp:sha-4ee70c4" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["Digest"])'
done   # 两行必须逐字相同：sha256:239d6f1b…
```

**为什么两个都要核**：relay（cheap3 `/etc/tcr-relay`，cron `*/5`）只搬「首次见到」
的 tag，而节点拉的是 ccr。2026-11-16 实测：`sha-4ee70c4` 在 16:32Z 构建完，
16:46:03Z 才出现在 ccr（`/var/log/tcr-relay.log` → `OK yizuo/vaas-mcp:sha-4ee70c4`）。
**只看 hkccr 就宣布「可以拉了」会正好卡在这个窗口里。** 若 sha tag 迟迟没到 ccr，
可以按 digest 拉（digest 一致即同一份），或手动补单：
`tcr-relay-sync.sh yizuo/vaas-mcp sha-4ee70c4`（在 cheap3 上，无视 seen 过滤）。

## 1. 顺序不能颠倒：先写 vault，再滚版

这是本次最容易踩的坑。托管模式（`VAAS_HOSTED=1`）下凭据来自 vault，
`generation` 调用的链路是：

```
resolve(kind)              → 读 secret/vaas/pool/<LINE>/<kind>
subprocess_env(resolved)   → 按记录的 provider 生成桥接器 env
litellm-bridge             → 按 <KIND>_PROVIDER 分派（grok / ciyuan / volcengine）
```

`pool/wanxing/{image,video}` **不存在**时，实际抛的是
**`IdentityRequired: hosted call without valid service credential`**——
**不是** `no <kind> credential in vault`（那条是 `resolve()` 抛的，而身份/车道闸在
`resolve()` **之前**就拦下了；2026-11-16 用 `VAAS_HOSTED=1` + 空 vault 实跑
`runtime.prepare()` 逐字核过，image 与 video 同形）。
所以只滚镜像、不写 vault ＝车道仍然不可用，而且**报错里不会出现 "vault" 这个词**
——看到 `IdentityRequired` 先查 `pool/wanxing/<kind>` 有没有记录，不要先怀疑网关或 token。

写入内容（`provider` 必须写 `grok`——**不能**写 `ciyuan`）：

```jsonc
// secret/vaas/pool/wanxing/image
{"provider": "grok", "base_url": "https://<词元网关>/v1",
 "api_key": "<组19 专用钥，api_keys id 182>", "model": "grok-imagine-image"}

// secret/vaas/pool/wanxing/video
{"provider": "grok", "base_url": "https://<词元网关>/v1",
 "api_key": "<同一把钥>", "model": "grok-imagine-video"}
```

> **为什么不能写 `ciyuan`**：写 `ciyuan` 会让注入层设 `VIDEO_PROVIDER=ciyuan`，
> 桥接器于是走 `_ciyuan_video.py`——那是组 9 被封的 acct 40 那台，一辈子拿不到产物。
> 图片档 `ciyuan` 也能通（端点同形、`_ciyuan_image` 就是 grok 图片的驱动），
> 但**视频必须 `grok`**；两边统一写 `grok` 最不容易错。
>
> **`provider: grok` 这档是 2026-11-16 才补上的**（`274872d`）。在此之前的镜像里，
> 写 `provider: grok` 会抛 `unsupported provider 'grok'`——台账把 `video.grok` 判
> active、注册表挂 `seedance-grok`，唯独注入层没有它的档位，**部署当刻才炸**。
> 所以「先滚到 `sha-4ee70c4` 或更晚」是写 vault 的前置条件。

vault 写入需要 OpenBAO 写权限（`vaas-policy`，仅 `secret/data/vaas/*`）。当前阻塞：
本机无 `bao`/`vault` CLI、VAAS `.env` 无 `OPENBAO_TOKEN`；root token 在部署节点
`/root/<VAAS_DEPLOY_SECRETS_FILE>`（600）。**写之前先保留旧记录**以便回滚。

## 2. 滚版（在 china-<node-a> 上）

```bash
cd /opt/vaas-deploy
# ① 先记下当前在役，回滚要用
docker inspect -f '{{.Config.Image}} {{.Image}} {{.Created}}' vaas-wanxing

# ② 拉新镜像（ccr，节点侧拉取源）
docker pull <registry>/yizuo/vaas-mcp:sha-4ee70c4

# ③ 改的是**权威的那份** compose（不是默认 docker-compose.yml）
cp -p docker-compose.prebuilt.yml docker-compose.prebuilt.yml.bak-$(date +%Y%m%d-%H%M%S)
$EDITOR docker-compose.prebuilt.yml          # 只把 vaas-wanxing 的 image: 改成上面那个 tag
grep -n 'image:' docker-compose.prebuilt.yml | head -5

# ④ 重建（--no-build：默认 yml 会尝试本地 build，别走它）
docker compose -f docker-compose.prebuilt.yml up -d \
  --no-deps --force-recreate --no-build vaas-wanxing

# ⑤ 起来了吗
sleep 15
docker inspect -f '{{.State.Health.Status}}' vaas-wanxing
docker logs --tail 30 vaas-wanxing
```

`docker-compose.prebuilt.yml` **目前只活在这台机上、任何仓库里都没有**（本仓
`deploy/` 只有示例 `docker-compose.vaas.example.yml`）。这是「宿主有、仓内零出处」
那类缺口：滚坏了没法按仓里那份重建，回滚也只能靠 `docker inspect` 里那个旧 tag。
**建议滚版后把脱敏版（去掉所有 token/key 行）提交进本仓 `deploy/`**，并在文件头
写明它是权威副本、以及 secrets 从哪来。

## 3. 验收（三层，缺一层都不算通）

### 3.1 进程与车道台账

```bash
curl -s http://127.0.0.1:30950/health          # {"ok":true,"hosted":true}
# 公网/tailnet 走 MCP 读台账（下面这段就是 /tmp/mcp_probe.sh 的内容）
```

`registry_list_generators` 必须出现 **`seedream-grok` 与 `seedance-grok`** 两条，
且各自 `lane.health == "active"`、`lane.last_verified` 是本次日期。
本次改造前实测四条 image/video 车道全 down（volcengine 未配凭据、ciyuan 组 9 被封），
**没有任何 grok 条目**——所以「出现 grok 两条」本身就是「新镜像生效了」的证据。

> **别把 `model: "${GROK_IMAGE_MODEL}"` 当成缺陷**：`registry.py::_resolve` 对未设的
> 环境变量**原样保留**占位符，而这些名字只由 `subprocess_env` 注入桥接器子进程、
> 不在服务进程的 env 里。它只影响发现面的显示（`voice` 那条 `${TTS_MODEL}` 从上线
> 起就是这样），**进不了调用路径**：`generate_image` 与 `generate_video_submit`
> 的签名里都没有 `model` 参数，桥接器取的是自己进程里的 `GROK_*_MODEL` / 缺省值。
> 真要让它显示得好看，就在部署侧 `.env` 里补 `GROK_IMAGE_MODEL` / `GROK_VIDEO_MODEL`。

### 3.2 真调一次（判据是产物，不是 200）

```bash
# 经 MCP 调 generate_image（model=grok-imagine-image）→ 期望 images[].local_path 落盘、
# file_size_bytes > 0、format=png、warnings 为空
# 再调 generate_video（model=grok-imagine-video, duration>=3）→ 期望真 mp4（ftyp 魔数）
```

**判据顺序**：`ok:true` → 产物落盘且字节数 > 0 → 魔数对得上（PNG `89504e47` /
MP4 `ftypisom`）→ `warnings` 里如实披露被夹紧的时长与实得分辨率。

### 3.3 词元侧真的记账了

在 sub2api 库上查当天：

```sql
select model, count(*), sum(total_cost) from usage_logs
where created_at >= date_trunc('day', now()) and model like 'grok-imagine-%'
group by model;
```

期望图片 `$0.02/张`、视频 `$0.15/条(480p×3s)`。**并且**看 sub2api 的 pod 日志里
`grok_media.image_billing_terms` / `grok_media.video_billing_terms` 两行是否带
`serving_model`（`hunyuan-image-alpha` / `kling-video-v3`）——这两行是承做方的唯一
留痕，`usage_logs` 里只有客户买的名字（= 账单身份，刻意不改）。

## 4. 回滚

```bash
cd /opt/vaas-deploy
# 用第 2 步 ① 记下的旧 image（或备份 compose）
$EDITOR docker-compose.prebuilt.yml     # image: 改回旧 tag
docker compose -f docker-compose.prebuilt.yml up -d \
  --no-deps --force-recreate --no-build vaas-wanxing
```

回滚**不**需要撤 vault 记录：旧镜像不认识 `provider: grok`，调用会抛
`unsupported provider 'grok'`（fail-closed，不会误走 volcengine 白花钱）。
若要彻底回到旧行为，把 vault 记录改回旧值（这也是写 vault 前要先备份旧记录的原因）。

## 5. 遗留（不在本 runbook 范围）

- `openspec/changes/add-image-lane-fallback` 的 3.1/3.2/3.3/5.3 要等上面验收通过才能勾；
  2.2/6.5/8.7 依赖 vault 写权限。全部勾完才 `openspec archive`。
- 组 9 的 `DEBT-LANE-9-01` **不因本车道恢复而销账**：车道身份跟着凭据分组走，
  grok（组 19）与 ciyuan（组 9）是两条不同的路。
- `video.grok` 的台账 reason 里记着「视频完成不落 usage_logs」这条上游缺陷的实测——
  2026-11-15 起 sub2api 侧已修（`sha-586a87c`），复验时可以回填。
