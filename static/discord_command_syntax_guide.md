# Command Syntax & Flags Guide

Percy supports both **slash commands** (`/command`) and **prefix commands** (e.g. `!command`). Slash commands use Discord's built-in parameter UI — you just fill in the fields. The syntax notation below applies to **prefix commands**, where you type arguments directly.

---

## Argument Notation

> **Tip:** Never type the brackets themselves — they only indicate the argument type.

| Notation | Meaning | Example |
|:---------|:--------|:--------|
| `<argument>` | **Required** — the command fails without it | `<user>` |
| `[argument]` | **Optional** — omit to use the default behaviour | `[reason]` |
| `<A\|B>` | **Choice** — pick exactly one | `<ban\|kick>` |
| `<argument...>` | **Greedy** — consumes the rest of the input | `<reason...>` |
| `<"argument">` | **Exact** — case-sensitive, type it verbatim | `<"CONFIRM">` |
| `<argument="X">` | **Default** — uses `X` when omitted | `<limit="10">` |
| `[--flag]` | **Boolean flag** — see below | `[--silent]` |
| `[--name <arg>]` | **Valued flag** — see below | `[--reason <text>]` |

---

## Flags

Flags are optional, POSIX-style parameters prefixed with `--` (long form) or `-` (short form). They can appear **anywhere** in the command — order does not matter.

### Boolean flags

A flag shown as `[--flag]` is a toggle: absent = off, present = on.

```
!warn @User Spamming --silent
```

Here `--silent` is `True`; the rest before it is the reason.

### Valued flags

A flag shown as `[--name <arg>]` requires a value immediately after it. The value runs until the next flag or end of input.

```
!search discord bots --limit 5 --sort date
```

`--limit` receives `5`, `--sort` receives `date`.

### Short flags

Single-letter shortcuts use one dash. You can chain boolean short flags together — only the **last** one in a chain may take a value.

```
!command -sf            (equivalent to --s --f, both boolean)
!command -sf "value"    (equivalent to --s --f "value", -f takes the value)
```

---

## Putting it together

Given a command signature like:

```
!ban <user> [reason...] [--silent] [--duration <time>]
```

You might type:

```
!ban @User Repeated rule violations --silent --duration 7d
```

- `@User` fills the required `<user>` parameter
- `Repeated rule violations` fills the greedy `[reason...]`
- `--silent` enables the silent flag
- `--duration 7d` passes `7d` to the duration flag
