# modrinth-plugin-update

Downloads the newest compatible version of a list of Minecraft server plugins from
[Modrinth](https://modrinth.com). Give it your server jar (or just a Minecraft version) and some
plugins and/or Modrinth collections.

Single Python file, standard library only.

## Usage

```sh
python3 modrinth_plugin_update.py                  # uses config.json next to the script
python3 modrinth_plugin_update.py myserver.json
python3 modrinth_plugin_update.py -j paper-26.1.2-74.jar -p luckperms chunky -o plugins
python3 modrinth_plugin_update.py -m 26.1.2 -l purpur -c 7le3oTcg
```

`config.json`:

```json
{
  "jar": "../server/paper-26.1.2-74.jar",
  "plugins": ["luckperms", "chunky"],
  "collections": ["7le3oTcg"],
  "channels": ["release"],
  "out": "plugins"
}
```

Use `"version"` (and optionally `"loader"`, default `paper`) instead of `"jar"` if you don't
have the jar handy. `-h` lists every option.

What it does:

- Downloads the newest version of each plugin for your exact Minecraft version and server type.
- Plugins without one are skipped, then offered one by one (newest version for any Minecraft
  version, `[Y/n]`) if you're in a terminal.
- When a plugin is updated, its old jars in the output folder are deleted. Jars Modrinth doesn't
  recognise are never touched.
- Private collections need `MODRINTH_TOKEN` set to a
  [personal access token](https://modrinth.com/settings/pats) with "Read collections".

## Development

```sh
make check   # ruff format, ruff check, ty (via uvx)
```

## Not affiliated with Modrinth

This is an independent project, not affiliated with, endorsed by, or connected to Modrinth in
any way. It just uses their [public API](https://docs.modrinth.com), which they very kindly
provide for free. Thanks, Modrinth :)

[modrinth.com](https://modrinth.com) · [github.com/modrinth](https://github.com/modrinth)
