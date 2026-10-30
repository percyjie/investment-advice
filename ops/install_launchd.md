# 安装 launchd 定时任务

在项目根目录执行：

```bash
PROJECT="$(pwd)"
ENV_FILE="$HOME/.config/ia/env"
AGENTS="$HOME/Library/LaunchAgents"

mkdir -p "$AGENTS" "$PROJECT/logs"

for kind in daily weekly; do
  sed -e "s|__PROJECT_DIR__|$PROJECT|g" \
      -e "s|__ENV_FILE__|$ENV_FILE|g" \
      "ops/com.investment.advice.$kind.plist.template" \
      > "$AGENTS/com.investment.advice.$kind.plist"
  launchctl load "$AGENTS/com.investment.advice.$kind.plist"
done

launchctl list | grep com.investment.advice
```

立即手动触发一次，确认能跑通：

```bash
launchctl kickstart -k gui/$(id -u)/com.investment.advice.daily
tail -n 40 logs/daily.out.log
tail -n 40 logs/daily.err.log
```

修改模板后重新生成并加载：

```bash
launchctl unload "$AGENTS/com.investment.advice.daily.plist"
# 重新执行上面的 sed + load
```

卸载：

```bash
for kind in daily weekly; do
  launchctl unload "$AGENTS/com.investment.advice.$kind.plist"
  rm "$AGENTS/com.investment.advice.$kind.plist"
done
```

## 注意事项

- plist 模板里的路径占位符在安装时由 `sed` 替换为当前项目路径，仓库里不保留任何本机路径。
- 密钥文件权限保持 600：`chmod 600 ~/.config/ia/env`。
- 电脑睡眠期间错过的任务由 launchd 唤醒后补跑；报告页脚标"补发"。
- 非交易日 `ia daily` 会直接退出，不会推送。
- 若 macOS 提示"未允许后台项"，到「系统设置 → 通用 → 登录项与扩展」里放行。
