-- Opt-in template. See docs/developer_setup.md for per-machine setup.
herdr.caps { agents_per_run = 0, tokens_per_day = 0, wall_time = "30m" }

herdr.every("1d", { name = "stale-worktrees" }, function()
  -- Pin the helper's bytes too, so the approved plan cannot silently run changed code.
  local result = sh(
    "PATH=\"$HOME/.local/bin:$PATH\" agent-coord run --cores 1 --mem 2G --lock worktree-cleanup --max-wait 2m --expect 15m --label stale-worktrees -- sh -c '" ..
    "echo \"42ac7e346061ba22b044dc3e2b83b64acce5ef336ffce2c3efc22782983db11c  scripts/cleanup_worktrees.py\" | /usr/bin/sha256sum --check --quiet" ..
    " && exec /usr/bin/python3 -I -B scripts/cleanup_worktrees.py --github --inspector --trim-caches --apply'",
    { check = false, timeout = "28m" }
  )
  if result.ok then
    notify("WGS Extract worktree cleanup completed:\n" .. result.log_tail)
  else
    notify("WGS Extract worktree cleanup failed. See this run's log and out/worktree-cleanup/ for details.")
  end
end)
