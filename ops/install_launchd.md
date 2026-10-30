# 安装 launchd 定时任务

以下命令均在项目根目录执行：

```bash
PROJECT=/Users/panjie/mycode/investment-advice
mkdir -p ~/Library/LaunchAgents "$PROJECT/logs"

cp ops/com.panjie.ia.daily.plist ~/Library/LaunchAgents/
cp ops/com.panjie.ia.weekly.plist ~/Library/LaunchAgents/

launchctl load ~/Library/LaunchAgents/com.panjie.ia.daily.plist
launchctl load ~/Library/LaunchAgents/com.panjie.ia.weekly.plist

launchctl list | grep com.panjie.ia
```

立即手动触发一次，确认能跑通：

```bash
launchctl kickstart -k gui/$(id -u)/com.panjie.ia.daily
tail -n 40 "$PROJECT/logs/daily.out.log"
tail -n 40 "$PROJECT/logs/daily.err.log"
```

修改 plist 后需要重新加载：

```bash
launchctl unload ~/Library/LaunchAgents/com.panjie.ia.daily.plist
launchctl load ~/Library/LaunchAgents/com.panjie.ia.daily.plist
```

卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.panjie.ia.daily.plist
launchctl unload ~/Library/LaunchAgents/com.panjie.ia.weekly.plist
rm ~/Library/LaunchAgents/com.panjie.ia.daily.plist
rm ~/Library/LaunchAgents/com.panjie.ia.weekly.plist
```

## 注意事项

- plist 里的绝对路径、`IA_ENV_FILE` 按实际情况修改。
- 密钥文件权限保持 600：`chmod 600 ~/.config/ia/env`。
- 电脑睡眠期间错过的任务由 launchd 唤醒后补跑；报告页脚标"补发"。
- 非交易日 `ia daily` 会直接退出，不会推送。
- 若 macOS 提示"未允许后台项"，到「系统设置 → 通用 → 登录项与扩展」里放行。
