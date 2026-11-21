# Reference: Container exit codes

Use the `Exit Code` from `kubectl describe pod` (Last State: Terminated) to classify a crash:

| Exit code | Meaning | Typical cause |
|---|---|---|
| 0 | Process finished successfully | Container ran a one-off command; in a Deployment this causes restarts because Deployments expect long-running processes (use a Job instead) |
| 1 | Generic application error | App threw an error at startup: missing config/env var, bad arguments, failed dependency connection |
| 2 | Misuse of shell builtin / bad CLI args | Wrong command arguments |
| 126 | Command cannot execute | Permission problem, not executable |
| 127 | Command not found | Wrong `command`/entrypoint, binary missing from image |
| 137 | SIGKILL (128+9) | OOMKilled (check Reason), or killed by liveness probe / eviction |
| 139 | SIGSEGV (128+11) | Segmentation fault in native code |
| 143 | SIGTERM (128+15) | Graceful termination requested (rolling update, scale down, probe kill) |

Rule of thumb: exit code 1 -> read the logs (`kubectl logs --previous`); exit code 137 -> check Reason for OOMKilled and compare memory limits.
