# 国家电网电费数据获取

[![Docker Build](https://github.com/Poiig/ha_sgcc_electricity/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/Poiig/ha_sgcc_electricity/actions/workflows/docker-publish.yml)

将国家电网（95598）的电费、用电量数据接入 Home Assistant，支持腾讯点选验证码自动识别。

## 致谢

- [ARC-MX/sgcc_electricity_new](https://github.com/ARC-MX/sgcc_electricity_new) — 项目基础框架和数据抓取逻辑
- [renxiaoyaoo/ha-95598](https://github.com/renxiaoyaoo/ha-95598) — 点选验证码识别方案参考

本项目遵循 Apache License 2.0 协议。

---

## 功能

- 自动登录国家电网（支持点选验证码自动识别）
- 通过 Home Assistant REST API 推送传感器数据
- 可选将每日用电量保存到数据库（SQLite / MySQL）
- 密码登录失败自动切换二维码登录兜底
- 电费余额不足通知（PushPlus / URL Push）

| 实体 | 说明 |
|------|------|
| `sensor.electricity_charge_balance_xxxx` | 电费余额（CNY） |
| `sensor.last_electricity_usage_xxxx` | 最近一天用电量（KWH） |
| `sensor.yearly_electricity_usage_xxxx` | 今年总用电量（KWH） |
| `sensor.yearly_electricity_charge_xxxx` | 今年总电费（CNY） |
| `sensor.month_electricity_usage_xxxx` | 最近一个月用电量（KWH） |
| `sensor.month_electricity_charge_xxxx` | 上月总电费（CNY） |

> 适用于国家电网覆盖省份（南方电网省份不可用），支持 `linux/amd64`、`linux/arm64`。

---

## 安装部署

提供三种部署方式，按需选择：

### 方式一：Home Assistant Add-on（推荐）

适用于 Home Assistant OS / Supervised 用户，图形界面配置，无需命令行。

**安装步骤：**

1. 进入 `设置` → `加载项` → `加载项商店`
2. 右上角 `...` → `仓库`，添加以下地址：

```
https://github.com/Poiig/ha_sgcc_electricity
```

3. 刷新页面，找到 **国家电网电费数据获取** 并安装
4. 切换到 `配置` 标签，填写以下必填项：

| 配置项 | 说明 |
|--------|------|
| phone_number | 95598 登录手机号 |
| password | 95598 登录密码 |
| hass_url | Home Assistant 地址（默认 `http://homeassistant:8123/`） |
| hass_token | HA 长期访问令牌（在 HA 个人资料页底部创建） |

5. 保存配置，启动 Add-on
6. 切换到 `日志` 标签查看运行状态

**可选配置项：**

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| job_start_time | 每天同步开始时间 | `07:00` |
| data_retention_days | 每次同步天数 | 7 |
| db_type | 数据库类型 | none |
| login_fallback | 登录失败备选方案 | qrcode |
| push_type | 余额通知方式 | none |
| balance | 余额预警阈值 | 100 |
| ignore_user_id | 忽略的户号（逗号分隔） | 空 |

### 方式二：Docker Compose

适用于所有支持 Docker 的环境。

**步骤：**

```bash
# 1. 下载配置文件
mkdir ha_sgcc_electricity && cd ha_sgcc_electricity
curl -O https://raw.githubusercontent.com/Poiig/ha_sgcc_electricity/master/docker-compose.yml
curl -O https://raw.githubusercontent.com/Poiig/ha_sgcc_electricity/master/example.env
cp example.env .env

# 2. 编辑 .env（填写账号密码和 HA 地址令牌）
vim .env

# 3. 启动
docker compose up -d

# 4. 查看日志
docker compose logs -f sgcc_electricity

# 5. 更新
docker compose pull && docker compose up -d
```

**镜像地址：**

| 来源 | 地址 |
|------|------|
| GHCR | `ghcr.io/poiig/ha_sgcc_electricity:latest` |
| GHCR 国内加速 | `ghcr.nju.edu.cn/poiig/ha_sgcc_electricity:latest` |
| Docker Hub | `poiigzhao/ha_sgcc_electricity:latest` |
| Docker Hub 国内加速 | `docker.1ms.run/poiigzhao/ha_sgcc_electricity:latest` |

国内用户在 `docker-compose.yml` 中切换镜像源即可，已有注释说明。

### 方式三：本地运行

详见 [LOCAL_DEV_GUIDE.md](LOCAL_DEV_GUIDE.md)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp example.env .env
cd scripts && python main.py
```

---

## Home Assistant 配置

程序通过 REST API 自动创建实体，但需要在 `configuration.yaml` 中配置 template 以确保 HA 重启后实体可用。

将以下内容添加到 `configuration.yaml`（`xxxx` 替换为日志中显示的户号后四位）：

```yaml
template:
  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.electricity_charge_balance_xxxx
    sensor:
      - name: electricity_charge_balance_xxxx
        unique_id: electricity_charge_balance_xxxx
        state: "{{ states('sensor.electricity_charge_balance_xxxx') }}"
        state_class: measurement
        unit_of_measurement: "CNY"
        device_class: monetary

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.last_electricity_usage_xxxx
    sensor:
      - name: last_electricity_usage_xxxx
        unique_id: last_electricity_usage_xxxx
        state: "{{ states('sensor.last_electricity_usage_xxxx') }}"
        state_class: measurement
        unit_of_measurement: "kWh"
        device_class: energy

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.month_electricity_usage_xxxx
    sensor:
      - name: month_electricity_usage_xxxx
        unique_id: month_electricity_usage_xxxx
        state: "{{ states('sensor.month_electricity_usage_xxxx') }}"
        state_class: measurement
        unit_of_measurement: "kWh"
        device_class: energy

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.month_electricity_charge_xxxx
    sensor:
      - name: month_electricity_charge_xxxx
        unique_id: month_electricity_charge_xxxx
        state: "{{ states('sensor.month_electricity_charge_xxxx') }}"
        state_class: measurement
        unit_of_measurement: "CNY"
        device_class: monetary

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.yearly_electricity_usage_xxxx
    sensor:
      - name: yearly_electricity_usage_xxxx
        unique_id: yearly_electricity_usage_xxxx
        state: "{{ states('sensor.yearly_electricity_usage_xxxx') }}"
        state_class: total_increasing
        unit_of_measurement: "kWh"
        device_class: energy

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.yearly_electricity_charge_xxxx
    sensor:
      - name: yearly_electricity_charge_xxxx
        unique_id: yearly_electricity_charge_xxxx
        state: "{{ states('sensor.yearly_electricity_charge_xxxx') }}"
        state_class: total_increasing
        unit_of_measurement: "CNY"
        device_class: monetary
```

配置后重启 Home Assistant。

---

## 环境变量

Docker Compose 方式通过 `.env` 文件配置，完整配置项见 `example.env`。

**必填：**

| 变量 | 说明 |
|------|------|
| `PHONE_NUMBER` | 95598 登录手机号 |
| `PASSWORD` | 95598 登录密码 |
| `HASS_URL` | Home Assistant 地址 |
| `HASS_TOKEN` | HA 长期访问令牌 |

**常用可选：**

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JOB_START_TIME` | `07:00` | 每天同步开始时间 |
| `DATA_RETENTION_DAYS` | 7 | 每次同步天数（7 或 30） |
| `DB_TYPE` | none | 数据库类型（none / sqlite / mysql） |
| `LOGIN_FALLBACK` | qrcode | 登录失败备选（qrcode / none） |
| `RETRY_WAIT_TIME_OFFSET_UNIT` | 10 | 页面操作等待秒数（2-30） |

**通知相关：**

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PUSH_TYPE` | none | 通知方式（none / pushplus / urlpush） |
| `BALANCE` | 100 | 余额低于此值时通知（元） |
| `PUSHPLUS_TOKEN` | 空 | PushPlus token |
| `PUSH_URL` | 空 | URL Push 地址 |
| `PUSH_QRCODE_URL` | 空 | 二维码推送地址 |

---

## 登录流程

```
输入账号密码 → 点击登录
  → 检测到点选验证码 → 图像匹配找到目标位置 → 模拟点击 → 提交
  → 验证码识别失败 → 自动刷新重试
  → 多次失败 → 二维码登录兜底
```

验证码调试图片保存在 `data/pages/` 目录。

---

## 常见问题

**Q: 验证码识别失败**
> 检查 `data/pages/` 下的调试截图。国网每天有登录次数限制，频繁测试会触发 RK001 错误。

**Q: RK001 网络连接超时**
> 国网检测到异常登录频率，等待几小时后重试。

**Q: Docker 镜像较大**
> 镜像包含 Chromium 浏览器、中文字体和验证码识别依赖。

---

## License

[Apache License 2.0](LICENSE)
