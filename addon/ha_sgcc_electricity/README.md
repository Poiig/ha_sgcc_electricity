# 国家电网电费数据获取 - Home Assistant Add-on

将国家电网 95598 的电费、用电量、分时电量数据通过 REST API 同步到 Home Assistant。

## 使用前准备

- 一个可登录的国家电网 95598 账号，并且已经绑定户号
- Home Assistant 已生成长期访问令牌（个人资料页底部创建）

## 安装

1. Home Assistant 进入 `设置` → `加载项` → `加载项商店`
2. 右上角 `...` → `仓库`，添加：

```text
https://github.com/Poiig/ha_sgcc_electricity
```

3. 找到 `国家电网电费数据获取` 并安装
4. 在 `配置` 页面填写账号、密码和 Home Assistant 信息
5. 启动 add-on，查看日志确认运行状态

## 配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| phone_number | 95598 登录手机号 | 必填 |
| password | 95598 登录密码 | 必填 |
| hass_url | Home Assistant 地址 | `http://homeassistant:8123/` |
| hass_token | HA 长期访问令牌 | 必填 |
| job_start_time | 每天同步开始时间 | `07:00` |
| db_type | 数据库类型 | sqlite |
| data_retention_days | 数据保留天数 | 365 |
| daily_fetch_days | 每日获取天数 | 7 |
| login_fallback | 登录失败备选 | qrcode |

完整配置项在 add-on 配置页面中都有说明。

## 说明

本 add-on 通过 REST API 将数据推送到 Home Assistant，需要在 `configuration.yaml` 中配置 template 实体。详见主项目 README。
