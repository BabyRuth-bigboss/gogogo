#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_FILE="${SCRIPT_DIR}/report.html"

escape_html() {
  sed \
    -e 's/&/\&amp;/g' \
    -e 's/</\&lt;/g' \
    -e 's/>/\&gt;/g' \
    -e 's/"/\&quot;/g' \
    -e "s/'/\&#39;/g"
}

human_bytes() {
  local bytes="$1"
  awk -v bytes="${bytes}" '
    BEGIN {
      split("B KB MB GB TB", units, " ")
      value = bytes
      unit = 1
      while (value >= 1024 && unit < 5) {
        value = value / 1024
        unit++
      }
      printf "%.2f %s", value, units[unit]
    }
  '
}

disk_rows() {
  df -H | awk '
  function html(value) {
    gsub("&", "\\&amp;", value)
    gsub("<", "\\&lt;", value)
    gsub(">", "\\&gt;", value)
    gsub("\"", "\\&quot;", value)
    gsub("\047", "\\&#39;", value)
    return value
  }
  NR > 1 && $1 != "map" {
    mounted = $9
    for (i = 10; i <= NF; i++) {
      mounted = mounted " " $i
    }
    printf "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><span class=\"pill\">%s</span></td><td>%s</td></tr>\n", html($1), html($2), html($3), html($4), html($5), html(mounted)
  }'
}

memory_summary() {
  local page_size pages_active pages_wired pages_compressed pages_free pages_speculative
  page_size="$(pagesize)"
  pages_active="$(vm_stat | awk '/Pages active/ {gsub("\\.","",$3); print $3}')"
  pages_wired="$(vm_stat | awk '/Pages wired down/ {gsub("\\.","",$4); print $4}')"
  pages_compressed="$(vm_stat | awk '/Pages occupied by compressor/ {gsub("\\.","",$5); print $5}')"
  pages_free="$(vm_stat | awk '/Pages free/ {gsub("\\.","",$3); print $3}')"
  pages_speculative="$(vm_stat | awk '/Pages speculative/ {gsub("\\.","",$3); print $3}')"

  local used_pages available_pages used_bytes available_bytes total_bytes percent_used
  used_pages=$((pages_active + pages_wired + pages_compressed))
  available_pages=$((pages_free + pages_speculative))
  used_bytes=$((used_pages * page_size))
  available_bytes=$((available_pages * page_size))
  total_bytes="$(sysctl -n hw.memsize)"
  percent_used="$(awk -v used="${used_bytes}" -v total="${total_bytes}" 'BEGIN { printf "%.1f", used / total * 100 }')"

  cat <<HTML
<div class="metric">
  <span>内存总量</span>
  <strong>$(human_bytes "${total_bytes}")</strong>
</div>
<div class="metric">
  <span>已用内存</span>
  <strong>$(human_bytes "${used_bytes}")</strong>
</div>
<div class="metric">
  <span>可用内存</span>
  <strong>$(human_bytes "${available_bytes}")</strong>
</div>
<div class="metric">
  <span>使用率</span>
  <strong>${percent_used}%</strong>
</div>
HTML
}

generated_at="$(date '+%Y-%m-%d %H:%M:%S %Z')"
computer_name="$(scutil --get ComputerName 2>/dev/null || hostname)"
computer_name="$(printf '%s' "${computer_name}" | escape_html)"
disk_table="$(disk_rows)"
memory_cards="$(memory_summary)"

cat > "${OUTPUT_FILE}" <<HTML
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>系统使用情况报告</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --text: #172033;
      --muted: #657089;
      --line: #dbe2ef;
      --accent: #0f766e;
      --accent-soft: #dff5f1;
      --shadow: 0 12px 30px rgba(31, 43, 68, 0.10);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }

    main {
      width: min(1100px, calc(100% - 32px));
      margin: 0 auto;
      padding: 40px 0;
    }

    header {
      margin-bottom: 24px;
    }

    h1 {
      margin: 0 0 8px;
      font-size: clamp(28px, 5vw, 44px);
      letter-spacing: 0;
    }

    .meta {
      color: var(--muted);
      font-size: 15px;
    }

    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      margin-top: 18px;
      overflow: hidden;
    }

    section h2 {
      margin: 0;
      padding: 18px 20px;
      border-bottom: 1px solid var(--line);
      font-size: 18px;
      letter-spacing: 0;
    }

    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      padding: 20px;
    }

    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      min-height: 92px;
      background: #fbfcff;
    }

    .metric span {
      display: block;
      color: var(--muted);
      font-size: 14px;
      margin-bottom: 8px;
    }

    .metric strong {
      font-size: 24px;
      letter-spacing: 0;
      overflow-wrap: anywhere;
    }

    .table-wrap {
      overflow-x: auto;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
    }

    th,
    td {
      padding: 13px 16px;
      text-align: left;
      border-bottom: 1px solid var(--line);
      white-space: nowrap;
    }

    th {
      color: var(--muted);
      font-size: 13px;
      font-weight: 650;
      background: #fbfcff;
    }

    tr:last-child td {
      border-bottom: 0;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 54px;
      padding: 3px 8px;
      border-radius: 999px;
      color: var(--accent);
      background: var(--accent-soft);
      font-weight: 700;
    }

    footer {
      color: var(--muted);
      font-size: 13px;
      margin-top: 18px;
    }

    @media (max-width: 820px) {
      main {
        width: min(100% - 24px, 1100px);
        padding: 28px 0;
      }

      .metrics {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }

    @media (max-width: 520px) {
      .metrics {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>系统使用情况报告</h1>
      <div class="meta">${computer_name} · ${generated_at}</div>
    </header>

    <section>
      <h2>内存使用情况</h2>
      <div class="metrics">
        ${memory_cards}
      </div>
    </section>

    <section>
      <h2>磁盘使用情况</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>文件系统</th>
              <th>容量</th>
              <th>已用</th>
              <th>可用</th>
              <th>使用率</th>
              <th>挂载点</th>
            </tr>
          </thead>
          <tbody>
            ${disk_table}
          </tbody>
        </table>
      </div>
    </section>

    <footer>运行脚本会刷新这份 HTML 报告。</footer>
  </main>
</body>
</html>
HTML

echo "报告已生成：${OUTPUT_FILE}"
