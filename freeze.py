"""Render Coupon to a static site for GitHub Pages.

GitHub Pages serves files, not Flask, and the repo is public so the API key
can never ship to the browser. So the odds are fetched here — on a runner,
with the key in a secret — and the result is written out as flat HTML.

The site lives under a path prefix (lukebriscoe.com/Coupon/), so every page is
rendered against a base_url carrying that prefix. Werkzeug puts it in
SCRIPT_NAME, and everything `url_for` builds — CSS, favicons, internal links —
comes out correctly prefixed without a single hardcoded path. (Setting
SCRIPT_NAME through environ_base looks equivalent but does not work: the test
client binds its URL adapter from base_url, so the prefix is ignored.)

    python freeze.py --output _site --prefix /Coupon
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import app as coupon_app
from football import store

PROJECT_ROOT = Path(__file__).resolve().parent
UK = ZoneInfo("Europe/London")


def render(client, path: str, prefix: str) -> bytes:
    """Render one route exactly as the live app would serve it."""
    response = client.get(path, base_url=f"http://localhost{prefix}/")
    if response.status_code != 200:
        raise RuntimeError(
            f"{path} returned {response.status_code} — refusing to publish a "
            f"broken page.\n{response.data.decode('utf-8', 'replace')[:800]}"
        )
    return response.data


def write(output: Path, url_path: str, body: bytes) -> Path:
    """Write a route to disk as a directory index, so URLs need no .html."""
    target = output / url_path.strip("/") / "index.html" if url_path.strip("/") \
        else output / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return target


def build(output: Path, prefix: str, target_date: date | None = None) -> list[Path]:
    coupon_app.app.config["STATIC_BUILD"] = True
    coupon_app.app.config["BUILT_AT"] = (
        datetime.now(timezone.utc).astimezone(UK).strftime("%H:%M on %a %-d %B %Y")
    )

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    client = coupon_app.app.test_client()
    written: list[Path] = []

    index_path = "/" if target_date is None else f"/?date={target_date.isoformat()}"
    written.append(write(output, "", render(client, index_path, prefix)))

    for route, url_path in (("/history", "history"),
                            ("/performance", "performance")):
        written.append(write(output, url_path, render(client, route, prefix)))

    # One page per logged Saturday, so the history links resolve.
    for record in store.load_slips():
        slip_date = record.get("date")
        if not slip_date:
            continue
        body = render(client, f"/slip/{slip_date}", prefix)
        written.append(write(output, f"slip/{slip_date}", body))

    shutil.copytree(PROJECT_ROOT / "static", output / "static")
    # Tell Pages not to run the output through Jekyll, which would drop
    # anything beginning with an underscore.
    (output / ".nojekyll").write_text("")

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="_site", help="output directory")
    parser.add_argument("--prefix", default="/Coupon",
                        help="path the site is served under")
    parser.add_argument("--date", help="build for a specific Saturday (YYYY-MM-DD)")
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else None
    output = (PROJECT_ROOT / args.output).resolve()

    written = build(output, args.prefix.rstrip("/"), target)

    print(f"Built {len(written)} pages into {output}")
    for path in written:
        print(f"  {path.relative_to(output)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
