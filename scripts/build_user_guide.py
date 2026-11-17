"""Generate the packaged help page from docs/USER_GUIDE.md (stdlib only).

Run python scripts/build_user_guide.py after editing the guide. --check verifies
the committed runtime asset without writing. Supported Markdown: headings,
paragraphs, single-level bullet lists, **strong** text and local tutorial images.
Images link to their original for native browser enlargement. HTML is escaped.
"""
from pathlib import Path
import argparse
import html
import json
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs/USER_GUIDE.md'
OUTPUT = ROOT / 'src/webapp/static/user-guide.html'


def inline(text):
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html.escape(text, quote=True))


def render(markdown):
    blocks = []
    paragraph = []
    listing = False
    sections = []

    def flush():
        if paragraph:
            blocks.append('<p>' + inline(' '.join(paragraph)) + '</p>')
            paragraph.clear()

    for line in markdown.splitlines():
        line = line.strip()
        if not line:
            flush()
            continue
        if not line.startswith('- ') and listing:
            blocks.append('</ul>')
            listing = False
        if line.startswith('# '):
            flush()
            blocks.append('<h1>' + inline(line[2:]) + '</h1>')
        elif line.startswith('## '):
            flush()
            title = line[3:]
            anchor = 'section-' + str(len(sections) + 1)
            sections.append((anchor, title))
            blocks.append(f'<h2 id="{anchor}">' + inline(title) + '</h2>')
        elif (image := re.fullmatch(r'!\[([^\]\n]+)\]\(((?:/science-static|\.\./src/webapp/static)/tutorial/step-(?:0[1-9]|1[0-2])\.webp)\)', line)):
            flush()
            alt, url = image.groups()
            url = url.replace('../src/webapp/static/', '/science-static/')
            metadata = json.loads((ROOT / 'src/webapp/static/tutorial/manifest.json').read_text(encoding='utf8'))
            size = next(item['size'] for item in metadata['images'] if url.endswith('/' + item['file']))
            number = int(re.search(r'step-(\d+)', url).group(1))
            blocks.append('<figure class="tutorial-shot"><a href="' + url + '" target="_blank" rel="noopener" aria-label="Enlarge screenshot: ' + html.escape(alt, quote=True) + ' (opens in a new tab)"><img src="' + url + '" alt="' + html.escape(alt, quote=True) + '" width="' + str(size[0]) + '" height="' + str(size[1]) + '" loading="lazy" decoding="async"></a><figcaption>Screenshot ' + str(number) + ' · Tap to enlarge · synthetic example</figcaption></figure>')
        elif line.startswith('- '):
            flush()
            if not listing:
                blocks.append('<ul>')
                listing = True
            blocks.append('<li>' + inline(line[2:]) + '</li>')
        else:
            paragraph.append(line)
    flush()
    if listing:
        blocks.append('</ul>')
    contents = '\n'.join(f'<a href="#{anchor}">{inline(title)}</a>' for anchor, title in sections)
    body = '\n'.join(blocks)
    return f'''<!doctype html>
<!-- Generated from docs/USER_GUIDE.md by scripts/build_user_guide.py. -->
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>User guide · Leaf Research</title><link rel="stylesheet" href="/science-static/research-ui.css"><link rel="stylesheet" href="/science-static/user-guide.css"></head>
<body><a class="skip-link" href="#guide">Skip to guide</a>
<header><a class="brand" href="/analyze">Leaf Research</a><span>User guide</span></header>
<div class="layout"><nav aria-label="Guide contents"><a class="return-link" href="/analyze">Open Analyze a plant</a>{contents}</nav>
<main id="guide">{body}<a class="return-link" href="#guide">Back to top</a></main></div>
</body></html>
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    expected = render(SOURCE.read_text(encoding='utf-8'))
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding='utf-8') != expected:
            raise SystemExit('User guide is stale. Run python scripts/build_user_guide.py')
        print('PASS: packaged user guide matches docs/USER_GUIDE.md')
    else:
        OUTPUT.write_text(expected, encoding='utf-8', newline='\n')
        print('Generated src/webapp/static/user-guide.html')
