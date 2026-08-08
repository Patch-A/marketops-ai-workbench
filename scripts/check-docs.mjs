import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const excluded = new Set(['.git', 'node_modules', 'research-temp']);
const markdownFiles = [];

function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (excluded.has(entry.name)) continue;
    const fullPath = path.join(directory, entry.name);
    const relative = path.relative(root, fullPath).replaceAll('\\', '/');
    if (relative.startsWith('.agents/skills/finesse-ui/')) continue;
    if (entry.isDirectory()) walk(fullPath);
    else if (entry.isFile() && entry.name.endsWith('.md')) markdownFiles.push(fullPath);
  }
}

walk(root);

const failures = [];
const linkPattern = /\[[^\]]+\]\(([^)]+)\)/g;
const mojibakeMarkers = ['锛', '鈥', '銆', '馃'];

for (const file of markdownFiles) {
  const text = fs.readFileSync(file, 'utf8');
  for (const marker of mojibakeMarkers) {
    if (text.includes(marker)) failures.push(`${path.relative(root, file)}: possible mojibake marker ${marker}`);
  }

  for (const match of text.matchAll(linkPattern)) {
    const target = match[1];
    if (/^(https?:\/\/|mailto:|#)/.test(target)) continue;
    const cleanTarget = target.split('#')[0];
    const resolved = path.resolve(path.dirname(file), cleanTarget);
    if (!fs.existsSync(resolved)) failures.push(`${path.relative(root, file)}: missing local link ${target}`);
  }
}

if (failures.length > 0) {
  console.error(failures.join('\n'));
  process.exit(1);
}

console.log(`Checked ${markdownFiles.length} Markdown files.`);
