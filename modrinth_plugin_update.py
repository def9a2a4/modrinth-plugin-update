"""Download the newest compatible version of Modrinth plugins for a server jar."""

import argparse
import hashlib
import json
import os
import pathlib
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Any

API: str = "https://api.modrinth.com"
HEADERS: dict[str, str] = {"User-Agent": "def9a2a4/modrinth-plugin-update"}
# Personal access token, only needed for private collections (scope: Read collections).
TOKEN: str | None = os.environ.get("MODRINTH_TOKEN")
DEFAULT_CONFIG: pathlib.Path = pathlib.Path(__file__).parent / "config.json"
CHANNELS: list[str] = ["release", "beta", "alpha"]
# Modrinth loaders each server type can run. Unknown types are assumed to be Paper forks.
LOADERS: dict[str, list[str]] = {
    "purpur": ["purpur", "paper", "spigot", "bukkit"],
    "paper": ["paper", "spigot", "bukkit"],
    "spigot": ["spigot", "bukkit"],
    "bukkit": ["bukkit"],
    "folia": ["folia"],
}

EPILOG: str = """\
config file (JSON, every key optional; relative paths resolve from the config's directory):
  {
    "jar": "../server/paper-26.1.2-74.jar",
    "plugins": ["luckperms", "essentialsx"],
    "collections": ["AbCdEf12"],
    "channels": ["release"],
    "out": "plugins",
    "timeout": 10
  }

instead of "jar", give "version" (and optionally "loader", default paper):
  {"version": "26.1.2", "loader": "purpur", "plugins": ["luckperms"]}
both together: version and loader override what the jar says.

config.json next to this script is used only when no config or flags are given. flags
override the matching key of a config passed explicitly. files already in the output dir
with the same name are skipped. when a plugin is downloaded, its other jars in the output
dir (matched by checksum on Modrinth) are deleted.

plugins with no version for this Minecraft version are skipped on the first pass. then,
if running in a terminal, each one is offered case by case: the newest version for this
server type on any Minecraft version, download Y/n. anything still missing exits 1.

private collections need a Modrinth personal access token with the "Read collections"
scope in the MODRINTH_TOKEN environment variable.

examples:
  %(prog)s                  # uses config.json next to this script
  %(prog)s myserver.json
  %(prog)s -j paper-26.1.2-74.jar -p luckperms chunky -o plugins
  %(prog)s -j purpur-26.1.2-1.jar -c AbCdEf12 --channels release beta
  %(prog)s -m 26.1.2 -p luckperms
"""


def get(
    url: str, headers: dict[str, str] = HEADERS, data: bytes | None = None
) -> bytes:
    """Fetch a URL (POST if data is given) and return the raw body. HTTP errors raise."""
    req: urllib.request.Request = urllib.request.Request(url, data, headers)
    with urllib.request.urlopen(req) as r:
        return r.read()


def api(path: str, body: Any = None, **params: Any) -> Any:
    """Call the Modrinth API, POSTing body as JSON if given.

    Query params are JSON-encoded, as Modrinth expects for list filters.
    """
    query: str = urllib.parse.urlencode({k: json.dumps(v) for k, v in params.items()})
    url: str = f"{API}{path}?{query}"
    headers: dict[str, str] = HEADERS | {"Content-Type": "application/json"}
    # Token only goes to the API, never to download hosts.
    if TOKEN:
        headers["Authorization"] = TOKEN
    data: bytes | None = None if body is None else json.dumps(body).encode()
    return json.loads(get(url, headers, data))


def detect_target(
    jar: pathlib.Path | None, loader: str | None, version: str | None
) -> tuple[list[str], str]:
    """Return (loaders to query, game version). Explicit loader/version win over the jar.

    From a jar, the loader is the filename prefix (paper-26.1.2-74.jar -> paper) and the
    version is read from version.json inside it if it exists, otherwise from the filename.
    Without either, the loader defaults to paper.
    """
    if jar is not None:
        parts: list[str] = jar.stem.lower().split("-")
        loader = loader or parts[0]
        if version is None and jar.is_file():
            with zipfile.ZipFile(jar) as z:
                if "version.json" in z.namelist():
                    version = json.loads(z.read("version.json"))["id"]
        if version is None and len(parts) > 1:
            version = parts[1]
    loader = (loader or "paper").lower()
    if version is None:
        sys.exit('can\'t tell the Minecraft version, set it with -m or "version"')
    # Catches typos and odd jar names, which would otherwise match no plugin at all.
    known: set[str] = {t["version"] for t in api("/v2/tag/game_version")}
    if version not in known:
        sys.exit(
            f'{version} isn\'t a Minecraft version Modrinth knows, set it with -m or "version"'
        )
    loaders: list[str] = LOADERS.get(loader, [loader, "paper", "spigot", "bukkit"])
    return loaders, version


def resolve_projects(
    plugins: list[str], collections: list[str], loaders: list[str]
) -> dict[str, str]:
    """Return {project ID: title} for the plugins plus every project in the collections.

    Deduped in order, so a plugin listed by slug and also in a collection appears once.
    Projects that don't support any of these loaders (mods, other server types) are dropped.
    Exits if a listed plugin isn't on Modrinth, before anything is downloaded.
    """
    wanted: list[str] = list(plugins)
    for c in collections:
        try:
            collection: dict[str, Any] = api(f"/v3/collection/{c}")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            sys.exit(f"collection {c} not found (if it's private, set MODRINTH_TOKEN)")
        wanted += collection["projects"]
    # One bulk lookup; it accepts slugs or IDs and silently drops unknown ones.
    found: list[dict[str, Any]] = api("/v2/projects", ids=list(dict.fromkeys(wanted)))
    # IDs are case-sensitive; slugs aren't.
    by_id: dict[str, dict[str, Any]] = {p["id"]: p for p in found}
    by_slug: dict[str, dict[str, Any]] = {p["slug"].lower(): p for p in found}
    matched: dict[str, dict[str, Any] | None] = {
        w: by_id.get(w) or by_slug.get(w.lower()) for w in wanted
    }
    unknown: list[str] = [w for w, p in matched.items() if p is None]
    # A typo in plugins is fatal; collections can still list deleted or hidden projects.
    if typos := [w for w in unknown if w in plugins]:
        sys.exit(f"not found on Modrinth: {', '.join(typos)}")
    if unknown:
        print(f"ignoring, gone from Modrinth: {', '.join(unknown)}")
    projects: list[dict[str, Any]] = [p for p in matched.values() if p is not None]
    ignored: list[str] = [
        p["title"] for p in projects if not set(p["loaders"]) & set(loaders)
    ]
    if ignored:
        print(f"ignoring, not for {loaders[0]}: {', '.join(dict.fromkeys(ignored))}")
    return {p["id"]: p["title"] for p in projects if set(p["loaders"]) & set(loaders)}


def installed(out: pathlib.Path) -> dict[str, list[pathlib.Path]]:
    """Return {project ID: jars} for the jars in out that Modrinth recognises by checksum."""
    # A list per checksum, so identical copies under different names are all found.
    jars: dict[str, list[pathlib.Path]] = {}
    for j in out.glob("*.jar"):
        jars.setdefault(hashlib.sha1(j.read_bytes()).hexdigest(), []).append(j)
    if not jars:
        return {}
    found: dict[str, dict[str, Any]] = api(
        "/v2/version_files", {"hashes": list(jars), "algorithm": "sha1"}
    )
    result: dict[str, list[pathlib.Path]] = {}
    for h, v in found.items():
        result.setdefault(v["project_id"], []).extend(jars[h])
    return result


def newest_version(
    project: str, loaders: list[str], channels: list[str], game_version: str | None
) -> dict[str, Any] | None:
    """Return the newest version for these loaders in an allowed channel, or None.

    Limited to game_version if given, otherwise any Minecraft version.
    """
    params: dict[str, list[str]] = {"loaders": loaders}
    if game_version is not None:
        params["game_versions"] = [game_version]
    versions: list[dict[str, Any]] = api(f"/v2/project/{project}/version", **params)
    # Modrinth lists versions newest first.
    return next((v for v in versions if v["version_type"] in channels), None)


def download(
    version: dict[str, Any], out: pathlib.Path, old: list[pathlib.Path]
) -> str:
    """Download a version's primary file into out unless it's already there, then delete the
    project's other jars (old) so the server doesn't load two copies. Returns a status line.
    """
    files: list[dict[str, Any]] = version["files"]
    f: dict[str, Any] = next((f for f in files if f["primary"]), files[0])
    dest: pathlib.Path = out / f["filename"]
    status: str = f"have {f['filename']}"
    if not dest.exists():
        # Write then rename, so an interrupted download doesn't leave a file that looks complete.
        part: pathlib.Path = dest.with_name(dest.name + ".part")
        part.write_bytes(get(f["url"]))
        part.replace(dest)
        status = f"downloaded {f['filename']}"
    for j in old:
        if j != dest:
            j.unlink()
            status += f", removed {j.name}"
    return status


def ask_fallback(
    title: str,
    project: str,
    loaders: list[str],
    channels: list[str],
    out: pathlib.Path,
    old: list[pathlib.Path],
) -> bool:
    """Offer the newest version for any Minecraft version. Returns True if it was downloaded."""
    v: dict[str, Any] | None = newest_version(project, loaders, channels, None)
    if v is None:
        print(f"{title}: no {'/'.join(channels)} version for {loaders[0]} at all")
        return False
    game_versions: list[str] = v["game_versions"]
    # Long lists (1.8 ... 1.21) are noise; the newest few are what matter.
    shown: str = ", ".join(game_versions[-3:])
    if len(game_versions) > 3:
        shown = "..., " + shown
    answer: str = input(
        f"{title} {v['version_number']} (for {shown}). download? [Y/n] "
    )
    if answer.strip().lower() in ("n", "no"):
        return False
    print(f"{title}: {download(v, out, old)}")
    return True


def parse_args() -> argparse.Namespace:
    """Parse the command line and merge it over the config file. Flags win over config keys."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=__doc__,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "config",
        nargs="?",
        type=pathlib.Path,
        help="JSON config file (default: config.json next to this script, if no flags are given)",
    )
    parser.add_argument(
        "-j",
        "--jar",
        type=pathlib.Path,
        help="server jar path, or just its name (paper-26.1.2-74.jar)",
    )
    parser.add_argument(
        "-m",
        "--mc-version",
        metavar="VERSION",
        help="Minecraft version (default: from the jar)",
    )
    parser.add_argument(
        "-l",
        "--loader",
        help="server type: paper, purpur, folia, spigot, ... (default: from the jar, else paper)",
    )
    parser.add_argument(
        "-p",
        "--plugins",
        nargs="+",
        metavar="SLUG",
        help="Modrinth plugin slugs or project IDs",
    )
    parser.add_argument(
        "-c", "--collections", nargs="+", metavar="ID", help="Modrinth collection IDs"
    )
    parser.add_argument(
        "--channels",
        nargs="+",
        choices=CHANNELS,
        help="version types to accept (default: release)",
    )
    parser.add_argument(
        "-o", "--out", type=pathlib.Path, help="download directory (default: plugins)"
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=float,
        metavar="SECONDS",
        help="network timeout (default: 10)",
    )
    args: argparse.Namespace = parser.parse_args()

    # The default config is only used when nothing else is given. Without a config,
    # defaults are relative to the current directory.
    flags: bool = any(
        v is not None
        for v in (
            args.jar,
            args.mc_version,
            args.loader,
            args.plugins,
            args.collections,
            args.channels,
            args.out,
            args.timeout,
        )
    )
    config: pathlib.Path | None = args.config or (None if flags else DEFAULT_CONFIG)
    cfg: dict[str, Any] = {}
    base: pathlib.Path = pathlib.Path(".")
    if config is not None:
        if not config.is_file():
            parser.error(f"config not found: {config}")
        cfg = json.loads(config.read_text())
        base = config.parent

    args.jar = args.jar or (base / cfg["jar"] if "jar" in cfg else None)
    args.mc_version = args.mc_version or cfg.get("version")
    args.loader = args.loader or cfg.get("loader")
    args.timeout = args.timeout or cfg.get("timeout", 10)
    args.plugins = args.plugins or cfg.get("plugins", [])
    args.collections = args.collections or cfg.get("collections", [])
    args.channels = args.channels or cfg.get("channels", ["release"])
    args.out = args.out or base / cfg.get("out", "plugins")
    if args.jar is None and args.mc_version is None:
        parser.error(
            'no server: pass -j or -m, or set "jar" or "version" in the config'
        )
    if not args.plugins and not args.collections:
        parser.error(
            'nothing to download: pass -p/-c or set "plugins"/"collections" in the config'
        )
    return args


def main() -> None:
    """Download each plugin, ask about the incompatible ones, exit 1 if any are left out."""
    args: argparse.Namespace = parse_args()
    # Applies to every request, so a stalled connection fails instead of hanging.
    socket.setdefaulttimeout(args.timeout)

    loaders: list[str]
    version: str
    loaders, version = detect_target(args.jar, args.loader, args.mc_version)
    print(f"{loaders[0]} {version}")

    projects: dict[str, str] = resolve_projects(args.plugins, args.collections, loaders)
    out: pathlib.Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    have: dict[str, list[pathlib.Path]] = installed(out)

    # First pass: everything with a version for this exact Minecraft version.
    skipped: dict[str, str] = {}
    for p, title in projects.items():
        v: dict[str, Any] | None = newest_version(p, loaders, args.channels, version)
        if v is None:
            skipped[p] = title
            print(f"{title}: skipped, no version for {version}")
        else:
            print(f"{title}: {download(v, out, have.get(p, []))}")

    # Second pass: let the user decide on each skipped plugin.
    if skipped and sys.stdin.isatty():
        print(
            f"\n{len(skipped)} skipped. newest versions for other Minecraft versions:"
        )
        skipped = {
            p: title
            for p, title in skipped.items()
            if not ask_fallback(title, p, loaders, args.channels, out, have.get(p, []))
        }
    if skipped:
        sys.exit(f"no version downloaded for: {', '.join(skipped.values())}")


if __name__ == "__main__":
    main()
