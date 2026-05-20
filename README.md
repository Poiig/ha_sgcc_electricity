# 国家电网电费数据获取

[![Docker Build](https://github.com/Poiig/ha_sgcc_electricity/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/Poiig/ha_sgcc_electricity/actions/workflows/docker-publish.yml)

将国家电网（95598）的电费、用电量、分时电量数据接入 Home Assistant，支持腾讯点选验证码自动识别。

## 致谢

- [ARC-MX/sgcc_electricity_new](https://github.com/ARC-MX/sgcc_electricity_new) — 项目基础框架和数据抓取逻辑
- [renxiaoyaoo/ha-95598](https://github.com/renxiaoyaoo/ha-95598) — 点选验证码识别方案参考

本项目遵循 Apache License 2.0 协议。

---

## 功能

- 自动登录国家电网（支持点选验证码自动识别）
- 支持二维码扫码登录（测试时更方便）
- 通过 Home Assistant REST API 推送传感器数据
- 支持每日/月度/年度分时电量（谷/平/峰/尖）采集
- 支持电费余额增强信息
- 统一数据库表设计（SQLite / MySQL），支持数据保留天数配置
- 用户名自动从网站获取，无需手动配置映射
- 忽略用户自动清理数据库旧数据
- 密码登录失败自动切换二维码登录兜底
- 电费余额不足通知（PushPlus / URL Push）

### 传感器列表

| 实体 | 说明 |
|------|------|
| `sensor.electricity_charge_balance_xxxx` | 电费余额（CNY） |
| `sensor.last_electricity_usage_xxxx` | 最近一天用电量（kWh） |
| `sensor.yearly_electricity_usage_xxxx` | 今年总用电量（kWh） |
| `sensor.yearly_electricity_charge_xxxx` | 今年总电费（CNY） |
| `sensor.month_electricity_usage_xxxx` | 最近一个月用电量（kWh） |
| `sensor.month_electricity_charge_xxxx` | 上月总电费（CNY） |
| `sensor.month_valley_usage_xxxx` | 月度谷时电量（kWh） |
| `sensor.month_flat_usage_xxxx` | 月度平时电量（kWh） |
| `sensor.month_peak_usage_xxxx` | 月度峰时电量（kWh） |
| `sensor.month_tip_usage_xxxx` | 月度尖时电量（kWh） |
| `sensor.prepay_balance_xxxx` | 预付费余额（CNY） |

> 适用于国家电网覆盖省份（南方电网省份不可用），支持 `linux/amd64`、`linux/arm64`。

---

## 安装部署

### 方式一：Home Assistant Add-on（推荐）

1. 进入 `设置` → `加载项` → `加载项商店`
2. 右上角 `...` → `仓库`，添加：`https://github.com/Poiig/ha_sgcc_electricity`
3. 刷新页面，找到 **国家电网电费数据获取** 并安装
4. 切换到 `配置` 标签，填写手机号、密码、HA 地址和令牌
5. 保存配置，启动 Add-on

### 方式二：Docker Compose

```bash
mkdir ha_sgcc_electricity && cd ha_sgcc_electricity
curl -O https://raw.githubusercontent.com/Poiig/ha_sgcc_electricity/master/docker-compose.yml
curl -O https://raw.githubusercontent.com/Poiig/ha_sgcc_electricity/master/example.env
cp example.env .env && vim .env
docker compose up -d
```

**镜像地址：**

| 来源 | 地址 |
|------|------|
| GHCR | `ghcr.io/poiig/ha_sgcc_electricity:latest` |
| GHCR 国内加速 | `ghcr.nju.edu.cn/poiig/ha_sgcc_electricity:latest` |
| Docker Hub | `poiigzhao/ha_sgcc_electricity:latest` |
| Docker Hub 国内加速 | `docker.1ms.run/poiigzhao/ha_sgcc_electricity:latest` |

### 方式三：本地运行

详见 [LOCAL_DEV_GUIDE.md](LOCAL_DEV_GUIDE.md)

```bash
pip install -r requirements.txt
cp example.env .env && vim .env
cd scripts && python main.py
```

---

## Home Assistant 配置

需要在 `configuration.yaml` 中配置 template 以确保 HA 重启后实体可用，详见 [docs/HA_CONFIG.md](docs/HA_CONFIG.md)。

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

**可选：**

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LOGIN_METHOD` | password | 登录方式（password / qrcode） |
| `LOGIN_FALLBACK` | qrcode | 登录失败备选（qrcode / none） |
| `JOB_START_TIME` | `07:00` | 每天同步开始时间 |
| `DB_TYPE` | none | 数据库类型（none / sqlite / mysql） |
| `DAILY_FETCH_DAYS` | 7 | 每次获取日用电量天数（7 或 30） |
| `DATA_RETENTION_DAYS` | 365 | 数据库记录保留天数 |
| `IGNORE_USER_ID` | 空 | 忽略的户号（逗号分隔） |
| `RETRY_WAIT_TIME_OFFSET_UNIT` | 10 | 页面操作等待秒数（2-30） |
| `PUSH_TYPE` | none | 通知方式（none / pushplus / urlpush） |
| `BALANCE` | 100 | 余额低于此值时通知（元） |

---

## 数据库

启用数据库后，程序自动创建 5 张表存储用电数据，详见 [docs/DATABASE.md](docs/DATABASE.md)。

---

## 常见问题

**Q: 验证码识别失败**
> 检查 `data/pages/` 下的调试截图。国网每天有登录次数限制，频繁测试会触发 RK001 错误。

**Q: RK001 网络连接超时**
> 国网检测到异常登录频率，等待几小时后重试。

**Q: Docker 镜像较大**
> 镜像包含 Chromium 浏览器、中文字体和验证码识别依赖。

**Q: 分时电量数据为空**
> 分时电量通过 Vue state 注入从页面提取，部分省份可能不支持。基础用电量数据通过 DOM 提取，兼容性更好。

**Q: HA Add-on 启动报 Duplicate mount point**
> 升级到 v2.0.0+ 已修复此问题。如仍出现，卸载重装 Add-on。

---

## License

[Apache License 2.0](LICENSE)
