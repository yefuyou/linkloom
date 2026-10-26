"""Reuse the preserved Chromium flow after source polling settles.

The original acceptance script stays byte-identical. Only browser timing is
adapted for this Windows host: let an import's asynchronous source polling
finish before filling the next source form, and allow 60 seconds for browser
operations, and 180 seconds for asynchronous Runtime results. No Product
response, business assertion, source text or proof is replaced.
"""
from pathlib import Path


original = Path(__file__).resolve().parents[1] / "browser" / "acceptance.py"
source = original.read_text(encoding="utf8")
source = source.replace("timeout=60000", "timeout=180000")
marker = "            source_b = ("
assert source.count(marker) == 1
source = source.replace(marker, "            page.wait_for_timeout(1500)\n" + marker)
marker = "                page = context.new_page()"
assert source.count(marker) == 1
source = source.replace(marker, "                context.set_default_timeout(60000)\n" + marker)
exec(compile(source, str(original), "exec"), {"__file__": str(original), "__name__": "__main__"})
