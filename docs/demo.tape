# Records docs/demo.gif with vhs (https://github.com/charmbracelet/vhs).
#
#   uv sync && vhs docs/demo.tape
#
# Run it from the repo root with your Langfuse keys in .env. Off camera, it
# runs each example agent, which sends a trace to Langfuse, and waits until
# the trace shows up. On camera, `lftrace --latest` draws it.

Output docs/demo.gif

Set Shell "bash"
Set FontSize 12
Set Width 1050
Set Height 1090
Set Padding 15
Set Margin 15
Set MarginFill "#FFFFFF"
Set BorderRadius 10
Set WindowBar Colorful
Set TypingSpeed 32ms
Set WaitTimeout 90s
Set Theme { "name": "Catppuccin Mocha", "black": "#1e1e2e", "red": "#f38ba8", "green": "#a6e3a1", "yellow": "#f9e2af", "blue": "#89b4fa", "magenta": "#cba6f7", "cyan": "#94e2d5", "white": "#cdd6f4", "brightBlack": "#585b70", "brightRed": "#f38ba8", "brightGreen": "#a6e3a1", "brightYellow": "#f9e2af", "brightBlue": "#89b4fa", "brightMagenta": "#cba6f7", "brightCyan": "#94e2d5", "brightWhite": "#a6adc8", "background": "#1e1e2e", "foreground": "#cdd6f4", "selection": "#585b70", "cursor": "#f5e0dc" }

Env "PS1" "$ "
Env "VIRTUAL_ENV_DISABLE_PROMPT" "1"

Hide
Type "source .venv/bin/activate && set -a && source .env && set +a"
Enter
Type `latest() { lftrace --latest --mermaid 2>/dev/null | sed -n 's/^%% Trace \([0-9a-f]*\).*/\1/p'; }`
Enter
Type `run() { local before; before=$(latest); python "examples/$1.py" > /dev/null; while [ "$(latest)" = "$before" ]; do sleep 1; done; sleep 3; }`
Enter

# demo_agent
Hide
Type "run demo_agent"
Enter
Wait+Line /^\$\s*$/
Ctrl+L
Show
Type "lftrace --latest"
Enter
Sleep 8s

# support_bot
Hide
Type "run support_bot"
Enter
Wait+Line /^\$\s*$/
Ctrl+L
Show
Type "lftrace --latest"
Enter
Sleep 6s

# coding_agent
Hide
Type "run coding_agent"
Enter
Wait+Line /^\$\s*$/
Ctrl+L
Show
Type "lftrace --latest"
Enter
Sleep 7s
